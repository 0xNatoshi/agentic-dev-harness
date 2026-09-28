#!/usr/bin/env python3
"""Build a deterministic portable archive from explicit, repository-owned sources."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# The installer verifies packages with the same path rules; load it by path so neither script
# depends on sys.path.
_SPEC = importlib.util.spec_from_file_location("harness_install", Path(__file__).resolve().with_name("install.py"))
_INSTALL = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, _INSTALL)
_SPEC.loader.exec_module(_INSTALL)
package_path = _INSTALL.package_path
destination_key = _INSTALL.destination_key


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version(root=ROOT):
    value = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("VERSION must contain a numeric major.minor.patch version")
    return value


def payloads(root=ROOT):
    mapping = json.loads((root / "package-files.json").read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("package-files.json must declare the exported sources")
    files = {destination_key("MANIFEST.json")}  # Generated, never a mapped payload or directory.
    directories = set()
    for source, destinations in mapping.items():
        if not isinstance(destinations, list) or not destinations:
            raise ValueError(f"Missing package destinations: {source}")
        package_path(source)
        for name in destinations:
            path = package_path(name)
            key = destination_key(name)
            parents = {destination_key(parent.as_posix()) for parent in path.parents if parent != PurePosixPath(".")}
            if key in files or key in directories or parents & files:
                raise ValueError(f"Conflicting package destination: {name}")
            files.add(key)
            directories.update(parents)
    for directory in ["configurations", "skills/github-workflow"]:
        base = root / directory
        if base.is_symlink() or not base.is_dir():
            raise ValueError(f"Expected a real source directory: {directory}")
        for source in sorted(base.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Symlink cannot enter the package: {source.relative_to(root)}")
            if "__pycache__" in source.parts or source.suffix == ".pyc" or source.name == ".DS_Store":
                continue
            if source.is_file():
                if source.suffix not in {".md", ".txt", ".py", ".sh", ".toml", ".yaml", ".yml"}:
                    raise ValueError(f"Unexpected source file: {source.relative_to(root)}")
                relative = source.relative_to(root).as_posix()
                if relative not in mapping:
                    raise ValueError(f"Undeclared source file: {relative}")
    result = {}
    for source, destinations in mapping.items():
        path = root / source
        if any((root / Path(*Path(source).parts[:length])).is_symlink() for length in range(1, len(Path(source).parts) + 1)):
            raise ValueError(f"Symlink cannot enter the package: {source}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source must remain inside the repository: {source}")
        data = path.read_bytes()
        for destination in destinations:
            result[destination] = data
    return result


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".harness-build-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build(output, root=ROOT):
    release = version(root)
    prefix = f"dev-harness-v{release}"
    content = payloads(root)
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    manifest = {
        "format_version": 2,
        "package": prefix,
        "version": release,
        "description": project["description"],
        "repository_template_version": project["repository_template_version"],
        "hash_scope": "Every regular package file except MANIFEST.json itself",
        "qualification": "Read STATUS.md; reproducible packaging is not target-runtime qualification",
        "files": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in sorted(content.items())},
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    content["MANIFEST.json"] = manifest_bytes
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(content.items()):
            item = zipfile.ZipInfo(prefix + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    archive_bytes = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(content):
            raise ValueError("Archive integrity failed")
        for name, expected in content.items():
            if archive.read(prefix + "/" + name) != expected:
                raise ValueError(f"Archive readback failed: {name}")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive_name = prefix + "-codex-claude.zip"
    manifest_name = prefix + "-MANIFEST.json"
    atomic_write(output / archive_name, archive_bytes)
    atomic_write(output / manifest_name, manifest_bytes)
    sums = f"{digest(archive_bytes)}  {archive_name}\n{digest(manifest_bytes)}  {manifest_name}\n"
    atomic_write(output / (prefix + "-SHA256SUMS.txt"), sums.encode())
    return {"archive": archive_name, "sha256": digest(archive_bytes), "files": len(content), "version": release}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    options = parser.parse_args()
    print(json.dumps(build(options.output)))
