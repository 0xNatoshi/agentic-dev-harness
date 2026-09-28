#!/usr/bin/env python3
"""Verify a dev-harness package and install, roll back or recover its github-workflow skill.

Standard library only; Python 3.8+. Exit codes: 0 done, 1 refused or failed and restored,
2 blocked before any change, 3 restoration incomplete (recovery data kept; run `recover`).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
import uuid
import zipfile

INSTALLER_VERSION = "1.0.0"
SKILL = "github-workflow"
SKILL_PREFIX = "skills/" + SKILL + "/"
# Obsolete v5.2 files owned by earlier installations. They leave the active tree but stay in the
# retired and backup copies, so rollback restores them.
RETIREMENT_LIST = {
    "version": 1,
    "paths": {
        "templates/LICENSE-MIT": "v5.2",
        "templates/LICENSE-PolyForm-Noncommercial": "v5.2",
        "templates/LICENSE-proprietary": "v5.2",
        "readme-guide.md": "v5.2",
        "templates/README.md": "v5.2",
    },
}
PRESERVED = ("authentic history", "customization")
HISTORY = re.compile(r"templates/history/AGENTS-v[0-9][^/]*\.md")
CONSUMERS = {"claude", "codex"}
BOUNDARY_LIMITS = (
    "The process probe sees only this machine's process list. Sessions in containers, virtual "
    "machines, WSL, remote hosts or the web are not visible, and the probe cannot prevent a session "
    "started after it ran. Discovery is prevented only because consumers are stopped."
)
TEST_FAULT = "DEV_HARNESS_INSTALL_TEST_FAULT"
TEST_CRASH = "DEV_HARNESS_INSTALL_TEST_CRASH"
TEST_TRACE = "DEV_HARNESS_INSTALL_TEST_TRACE"
TEST_PROCESSES = "DEV_HARNESS_INSTALL_TEST_PROCESSES"
REPARSE_POINT = 0x400


class Failure(Exception):
    code = 1

    def __init__(self, message: str, details: object = None) -> None:
        super().__init__(message)
        self.details = details


class Refused(Failure):
    code = 1


class Blocked(Failure):
    code = 2


class Incomplete(Failure):
    code = 3


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# Package path rules, shared with scripts/build.py.
def package_path(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or re.search(r'[<>:"\\|?*\x00-\x1f]', name):
        raise ValueError("Package paths must be portable relative POSIX paths")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != name or name == ".":
        raise ValueError(f"Unsafe package path: {name}")
    for component in path.parts:
        device = component.split(".", 1)[0].rstrip(" ").upper()
        if component.endswith((".", " ")) or re.fullmatch(r"CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]", device):
            raise ValueError(f"Windows-reserved package path: {name}")
    return path


def destination_key(name: str) -> str:
    # Casefold alone keeps dotless i distinct; include uppercase aliases too.
    # These conservative comparison keys never change the spelling in the ZIP.
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).upper().casefold())


def colliding(names) -> list:
    """Names that collide case- or normalization-insensitively, or with a parent directory."""
    files, directories, collisions = {}, set(), []
    for name in sorted(names):
        path = PurePosixPath(name)
        key = destination_key(name)
        parents = {destination_key(parent.as_posix()) for parent in path.parents if parent != PurePosixPath(".")}
        if key in files or key in directories or parents & set(files):
            collisions.append(name)
        files[key] = name
        directories.update(parents)
    return collisions


# Test hooks. They only inject failures or replace the process list; the receipt records them.
def active_test_hooks() -> dict:
    return {name: os.environ[name] for name in (TEST_FAULT, TEST_CRASH, TEST_TRACE, TEST_PROCESSES) if os.environ.get(name)}


def checkpoint(name: str) -> None:
    trace = os.environ.get(TEST_TRACE)
    if trace:
        with open(trace, "a", encoding="utf-8") as stream:
            stream.write(name + "\n")
    if os.environ.get(TEST_CRASH) == name:
        os._exit(70)


def rename(source: Path, destination: Path, point: str) -> None:
    # DEV_HARNESS_INSTALL_TEST_FAULT: comma-separated points such as `apply:activate:permission`.
    for fault in filter(None, os.environ.get(TEST_FAULT, "").split(",")):
        permission = fault.endswith(":permission")
        if (fault[: -len(":permission")] if permission else fault) == point:
            if permission:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(source))
            raise OSError(5, "Injected rename failure", str(source))
    os.rename(source, destination)
    sync_directory(source.parent)
    sync_directory(destination.parent)


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def write_json(path: Path, value: object) -> None:
    write_durable(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"Cannot read {path}: {error}")
    if not isinstance(value, dict):
        raise Refused(f"{path} does not hold a JSON object")
    return value


# Filesystem inventory.
def is_link(path: Path) -> bool:
    try:
        status = os.lstat(str(path))
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & REPARSE_POINT)


def exists(path: Path) -> bool:
    return os.path.lexists(str(path))


def inventory(root: Path) -> dict | None:
    """Relative POSIX path -> type, mode and, for files, SHA-256 and size. None when absent.

    A link, junction or special file anywhere in the tree blocks: its target is outside what an
    exact rollback can own.
    """
    if not exists(root):
        return None
    if is_link(root):
        raise Blocked(f"{root} is a link or junction; its canonical source is not identified", {"path": str(root)})
    if not root.is_dir():
        raise Blocked(f"{root} is not a directory", {"path": str(root)})
    entries = {}
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(str(directory)) as listing:
            for item in listing:
                relative = prefix + item.name
                path = directory / item.name
                if is_link(path):
                    raise Blocked(f"Link or junction inside the tree: {path}", {"path": str(path)})
                status = os.lstat(str(path))
                if stat.S_ISDIR(status.st_mode):
                    entries[relative] = {"type": "dir", "mode": stat.S_IMODE(status.st_mode)}
                    pending.append((path, relative + "/"))
                elif stat.S_ISREG(status.st_mode):
                    data = path.read_bytes()
                    entries[relative] = {"type": "file", "mode": stat.S_IMODE(status.st_mode), "sha256": digest(data), "bytes": len(data)}
                else:
                    raise Blocked(f"Special file inside the tree: {path}", {"path": str(path)})
    return dict(sorted(entries.items()))


def differences(expected: dict | None, actual: dict | None) -> list:
    if expected is None or actual is None:
        return [] if expected == actual else [{"path": ".", "expected": "absent" if expected is None else "present", "actual": "absent" if actual is None else "present"}]
    result = []
    for name in sorted(set(expected) | set(actual)):
        if expected.get(name) != actual.get(name):
            result.append({"path": name, "expected": expected.get(name), "actual": actual.get(name)})
    return result


def is_cache(name: str) -> bool:
    return "__pycache__" in PurePosixPath(name).parts or name.endswith(".pyc")


# Package loading and verification.
class Package:
    def __init__(self, source: Path, files: dict, manifest_bytes: bytes, archive: bytes | None) -> None:
        self.source = source
        self.files = files
        self.manifest_bytes = manifest_bytes
        self.archive_sha256 = digest(archive) if archive is not None else None
        try:
            self.manifest = json.loads(manifest_bytes.decode("utf-8"))
        except ValueError as error:
            raise Refused(f"MANIFEST.json is not valid JSON: {error}")
        if not isinstance(self.manifest, dict) or self.manifest.get("format_version") != 2 or not isinstance(self.manifest.get("files"), dict):
            raise Refused("MANIFEST.json is not a format 2 dev-harness manifest")
        name = self.manifest.get("package")
        if not isinstance(name, str) or not re.fullmatch(r"dev-harness-v\d+\.\d+\.\d+", name) or name != "dev-harness-v" + str(self.manifest.get("version")):
            raise Refused("MANIFEST.json names no valid package version")
        self.name = name
        self.version = self.manifest["version"]

    def skill_files(self) -> dict:
        return {name[len(SKILL_PREFIX):]: data for name, data in self.files.items() if name.startswith(SKILL_PREFIX)}

    def identity(self) -> dict:
        return {
            "source": str(self.source),
            "package": self.name,
            "version": self.version,
            "manifest_sha256": digest(self.manifest_bytes),
            "archive_sha256": self.archive_sha256,
        }


def load_package(source: Path) -> Package:
    source = Path(os.path.abspath(str(source)))
    if is_link(source):
        raise Refused(f"Package {source} is a link")
    if source.is_file():
        return load_archive(source)
    if not source.is_dir():
        raise Refused(f"Package not found: {source}")
    files = {}
    for name, entry in (inventory_unchecked(source)).items():
        if entry is not None:
            files[name] = (source / Path(*PurePosixPath(name).parts)).read_bytes()
    if "MANIFEST.json" not in files:
        raise Refused(f"No MANIFEST.json in {source}")
    manifest = files.pop("MANIFEST.json")
    return Package(source, files, manifest, None)


def inventory_unchecked(source: Path) -> dict:
    """Package directory listing: regular files only; links and special files refuse."""
    try:
        entries = inventory(source)
    except Blocked as error:
        raise Refused(f"Package directory: {error}")
    return {name: entry for name, entry in entries.items() if entry["type"] == "file"}


def load_archive(source: Path) -> Package:
    archive = source.read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            names = [member.filename for member in members]
            if len(set(names)) != len(names):
                raise Refused("The archive repeats a member name")
            prefixes = {name.split("/", 1)[0] for name in names}
            if len(prefixes) != 1:
                raise Refused("The archive must hold exactly one top-level package directory")
            prefix = prefixes.pop()
            files = {}
            for member in members:
                if member.is_dir() or "/" not in member.filename:
                    raise Refused(f"Unexpected archive entry: {member.filename}")
                kind = (member.external_attr >> 16) & 0o170000
                if kind not in (0, stat.S_IFREG):
                    raise Refused(f"Archive entry is not a regular file: {member.filename}")
                name = member.filename.split("/", 1)[1]
                try:
                    package_path(name)
                except ValueError as error:
                    raise Refused(str(error))
                files[name] = bundle.read(member)
            if bundle.testzip() is not None:
                raise Refused("Archive CRC check failed")
    except zipfile.BadZipFile as error:
        raise Refused(f"Not a ZIP archive: {error}")
    if "MANIFEST.json" not in files:
        raise Refused("No MANIFEST.json in the archive")
    manifest = files.pop("MANIFEST.json")
    package = Package(source, files, manifest, archive)
    if prefix != package.name:
        raise Refused(f"Archive directory {prefix} does not match manifest package {package.name}")
    return package


def read_checksums(path: Path) -> dict:
    entries = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"Cannot read checksums {path}: {error}")
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](\S.*)", line)
        if not match:
            raise Refused(f"Malformed checksum line: {line!r}")
        name = match.group(2)
        if name in entries:
            raise Refused(f"Checksum listed twice: {name}")
        entries[name] = match.group(1).lower()
    return entries


def verify_package(package: Package, checksums: Path) -> dict:
    problems = []
    listed = package.manifest["files"]
    for name in sorted(set(listed) - set(package.files)):
        problems.append(f"missing: {name}")
    for name in sorted(set(package.files) - set(listed)):
        problems.append(f"extra: {name}")
    for name in sorted(set(listed) & set(package.files)):
        expected, data = listed[name], package.files[name]
        try:
            package_path(name)
        except ValueError as error:
            problems.append(str(error))
            continue
        if not isinstance(expected, dict) or expected.get("sha256") != digest(data) or expected.get("bytes") != len(data):
            problems.append(f"hash or size mismatch: {name}")
    for name in colliding(list(listed) + ["MANIFEST.json"]):
        problems.append(f"colliding path: {name}")
    sums = read_checksums(checksums)
    manifest_entry = package.name + "-MANIFEST.json"
    if sums.get(manifest_entry) != digest(package.manifest_bytes):
        problems.append(f"manifest digest does not match the {manifest_entry} checksum entry")
    archive_entry = package.name + "-codex-claude.zip"
    if package.archive_sha256 is None:
        archive = "not checked: directory input has no archive bytes"
    elif sums.get(archive_entry) != package.archive_sha256:
        problems.append(f"archive digest does not match the {archive_entry} checksum entry")
        archive = "mismatch"
    else:
        archive = "verified"
    if SKILL_PREFIX + "SKILL.md" not in package.files or skill_name(package.files[SKILL_PREFIX + "SKILL.md"]) != SKILL:
        problems.append(f"the package holds no {SKILL} SKILL.md")
    if problems:
        raise Refused("Package verification failed", problems)
    return {
        **package.identity(),
        "checksums_sha256": digest(Path(checksums).read_bytes()),
        "files_verified": len(listed),
        "archive_digest": archive,
        "scope": "Integrity against the supplied checksum file only; hash agreement is not independent provenance.",
    }


def skill_name(data: bytes) -> str | None:
    lines = data.decode("utf-8", "replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        match = re.fullmatch(r"name:\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?", line.strip())
        if match:
            return match.group(1).strip()
    return None


# Runtime locations.
class Locations:
    def __init__(self, runtime: str, home: str | None, state: str | None = None) -> None:
        explicit = home is not None
        user_home = Path(os.path.abspath(home)) if explicit else Path.home()
        self.runtime = runtime
        self.home = user_home
        if runtime == "claude":
            configured = None if explicit else os.environ.get("CLAUDE_CONFIG_DIR")
            config = Path(os.path.abspath(configured)) if configured else user_home / ".claude"
            self.skills = config / "skills"
            self.roots = [self.skills]
            self.instructions = [config / "CLAUDE.md", config / "AGENTS.md"]
            self.legacy = [config / "commands" / (SKILL + ".md")]
        elif runtime == "codex":
            configured = None if explicit else os.environ.get("CODEX_HOME")
            codex = Path(os.path.abspath(configured)) if configured else user_home / ".codex"
            self.skills = user_home / ".agents" / "skills"
            self.roots = [self.skills, codex / "skills"]
            self.instructions = [codex / "AGENTS.md", codex / "AGENTS.override.md"]
            self.legacy = []
        else:
            raise Refused(f"Unknown runtime: {runtime}")
        self.target = self.skills / SKILL
        self.state = Path(os.path.abspath(state)) if state else self.skills.parent / "dev-harness-install"

    def describe(self) -> dict:
        return {
            "runtime": self.runtime,
            "home": str(self.home),
            "target": str(self.target),
            "skill_roots": [str(root) for root in self.roots],
            "state_root": str(self.state),
        }

    def check(self) -> None:
        """Block when the target's canonical source or the state location cannot be owned safely."""
        for path in (self.skills.parent, self.skills, self.target):
            if is_link(path):
                raise Blocked(f"{path} is a link or junction; its canonical source is not identified", {"path": str(path)})
        for root in self.roots:
            if self.state == root or root in self.state.parents:
                raise Blocked(f"The state directory {self.state} is inside the skill root {root}")
        if is_link(self.state):
            raise Blocked(f"The state directory {self.state} is a link")
        for path in [self.state, *self.state.parents]:
            if exists(path / ".git"):
                raise Blocked(f"The state directory {self.state} is inside the Git work tree {path}; pass --state-dir outside it")
        anchor = next((path for path in [self.state, *self.state.parents] if path.exists()), None)
        volume = next((path for path in [self.target, *self.target.parents] if path.exists()), None)
        if anchor is None or volume is None or os.stat(str(anchor)).st_dev != os.stat(str(volume)).st_dev:
            raise Blocked(f"The state directory {self.state} is not on the target's volume")


def discoverable(roots) -> list:
    """Every directory under the skill roots whose SKILL.md is a github-workflow skill."""
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for directory, subdirectories, names in os.walk(str(root)):
            subdirectories[:] = sorted(name for name in subdirectories if not is_link(Path(directory) / name))
            if "SKILL.md" in names:
                path = Path(directory)
                try:
                    name = skill_name((path / "SKILL.md").read_bytes())
                except OSError:
                    name = None
                if path.name == SKILL or name == SKILL:
                    found.append(str(path))
    return sorted(found)


def file_hashes(paths) -> dict:
    return {str(path): digest(path.read_bytes()) if path.is_file() else None for path in paths}


# Planning.
def classify(name: str, entry: dict, package_files: dict) -> str:
    if is_cache(name):
        return "regenerable cache"
    if entry["type"] == "dir":
        return "directory"
    if name in RETIREMENT_LIST["paths"]:
        return "named obsolete"
    if name in package_files:
        return "package"
    if HISTORY.fullmatch(name):
        return "authentic history"
    return "customization"


def make_plan(runtime: str, home: str | None, state: str | None, package_source: Path, checksums: Path) -> dict:
    locations = Locations(runtime, home, state)
    locations.check()
    package = load_package(package_source)
    verification = verify_package(package, checksums)
    package_files = package.skill_files()
    before = inventory(locations.target)
    classification = {name: classify(name, entry, package_files) for name, entry in (before or {}).items()}
    preserved = [name for name, kind in classification.items() if kind in PRESERVED]
    kept = [name for name, kind in classification.items() if kind == "directory"]
    conflicts = colliding(list(package_files) + preserved)
    package_directories = {destination_key(parent.as_posix()) for name in package_files for parent in PurePosixPath(name).parents}
    conflicts += [name for name in kept if destination_key(name) in {destination_key(file) for file in package_files}]
    conflicts += [name for name in preserved if destination_key(name) in package_directories]
    if conflicts:
        raise Blocked("Preserved files collide with package paths", conflicts)
    duplicates = [path for path in discoverable(locations.roots) if path != str(locations.target)]
    return {
        "plan_format": 1,
        "installer_version": INSTALLER_VERSION,
        **locations.describe(),
        "package": verification,
        "checksums": str(Path(os.path.abspath(str(checksums)))),
        "retirement_list": RETIREMENT_LIST,
        "before": before,
        "classification": classification,
        "duplicates": duplicates,
        "legacy_commands": [str(path) for path in locations.legacy if exists(path)],
        "interrupted": str(locations.state / "CURRENT") if exists(locations.state / "CURRENT") else None,
    }


DRIFT_KEYS = ("installer_version", "runtime", "home", "target", "skill_roots", "state_root", "package", "before", "classification", "duplicates")


def drift(plan: dict, fresh: dict) -> list:
    return [key for key in DRIFT_KEYS if canonical(plan.get(key)) != canonical(fresh.get(key))]


# Maintenance boundary.
def is_consumer(command_line: str) -> bool:
    try:
        tokens = shlex.split(command_line, posix=os.name != "nt")
    except ValueError:
        tokens = command_line.split()
    for token in tokens[:2]:
        name = re.split(r"[\\/]", token.strip('"'))[-1].lower()
        for suffix in (".exe", ".cmd", ".js", ".mjs"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        if name in CONSUMERS:
            return True
    return False


def maintenance_boundary(confirmed: bool) -> dict:
    if not confirmed:
        raise Blocked("Stop every Claude and Codex session, then rerun with --maintenance-confirmed")
    stub = os.environ.get(TEST_PROCESSES)
    if stub:
        probe = "test stub " + stub
        try:
            lines = Path(stub).read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise Blocked(f"Consumer state is unverifiable: {error}")
    else:
        if os.name == "nt":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            command = [shell, "-NoProfile", "-NonInteractive", "-Command", "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"] if shell else None
        else:
            ps = shutil.which("ps")
            command = [ps, "-A", "-o", "args="] if ps else None
        if not command:
            raise Blocked("Consumer state is unverifiable: no process listing command found")
        probe = " ".join(command)
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise Blocked(f"Consumer state is unverifiable: {error}")
        if result.returncode != 0:
            raise Blocked(f"Consumer state is unverifiable: the process listing exited {result.returncode}")
        lines = result.stdout.splitlines()
    active = [line.strip() for line in lines if line.strip() and is_consumer(line)]
    if active:
        raise Blocked("Active Claude or Codex processes use skills; stop them and rerun", active)
    return {"confirmed_by_operator": True, "probe": probe, "active_consumers": [], "limitations": BOUNDARY_LIMITS}


class Lock:
    """Exclusive installer lock, released by the operating system if the process dies."""

    def __init__(self, state: Path) -> None:
        state.mkdir(parents=True, exist_ok=True)
        self.stream = open(state / "LOCK", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise Blocked(f"Another installer holds {state / 'LOCK'}")

    def __enter__(self) -> "Lock":
        return self

    def __exit__(self, *_: object) -> None:
        self.stream.close()


# The swap engine. An operation moves `moves` (from -> to), parks the target and activates an
# incoming tree. Undo decides from the filesystem, checked against recorded inventories, never
# from the journal state alone.
class Operation:
    def __init__(self, journal_path: Path, record: dict) -> None:
        self.path = journal_path
        self.record = record

    def save(self, state: str) -> None:
        self.record["state"] = state
        self.record.setdefault("history", []).append({"state": state, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        write_json(self.path, self.record)
        checkpoint(self.record["operation"] + ":" + state)

    @property
    def target(self) -> Path:
        return Path(self.record["target"])

    def run(self) -> None:
        op = self.record["operation"]
        self.save("prepared")
        for index, move in enumerate(self.record["moves"]):
            self.save(f"moving-{index}")
            Path(move["to"]).parent.mkdir(parents=True, exist_ok=True)
            rename(Path(move["from"]), Path(move["to"]), f"{op}:move-{index}")
            self.save(f"moved-{index}")
        if self.record["origin"] is not None:
            self.save("parking")
            Path(self.record["parked"]).parent.mkdir(parents=True, exist_ok=True)
            rename(self.target, Path(self.record["parked"]), f"{op}:park")
            self.save("parked")
            if differences(self.record["origin"], inventory(Path(self.record["parked"]))):
                raise Refused("The parked copy does not match the verified origin inventory")
        if self.record["incoming"] is not None:
            self.save("activating")
            self.target.parent.mkdir(parents=True, exist_ok=True)
            rename(Path(self.record["incoming_path"]), self.target, f"{op}:activate")
            self.save("activated")
        problems = differences(self.record["incoming"], inventory(self.target))
        for move in self.record["moves"]:
            problems += differences(move["inventory"], inventory(Path(move["to"])))
        if problems:
            raise Refused("The activated state does not match its verified inventory", problems)

    def undo(self) -> None:
        """Return to the origin state, verified; Incomplete when that cannot be established."""
        op = self.record["operation"]
        target, parked = self.target, Path(self.record["parked"])
        incoming_path = Path(self.record["incoming_path"]) if self.record["incoming_path"] else None
        self.save("undoing")
        if exists(target) and exists(parked):
            if differences(self.record["incoming"], inventory(target)) or incoming_path is None or exists(incoming_path):
                raise Incomplete("The target matches neither the origin nor the incoming inventory", {"target": str(target)})
            rename(target, incoming_path, f"{op}:undo-activate")
        elif exists(target) and self.record["origin"] is None:
            if differences(self.record["incoming"], inventory(target)) or incoming_path is None or exists(incoming_path):
                raise Incomplete("The target was absent before and now holds unverified content", {"target": str(target)})
            rename(target, incoming_path, f"{op}:undo-activate")
        if not exists(target) and exists(parked):
            if differences(self.record["origin"], inventory(parked)):
                raise Incomplete("The parked copy no longer matches the origin inventory", {"parked": str(parked)})
            rename(parked, target, f"{op}:undo-park")
        if not exists(target) and self.record["origin"] is not None:
            self.restore_from_backup()
        for index, move in reversed(list(enumerate(self.record["moves"]))):
            source, destination = Path(move["from"]), Path(move["to"])
            if exists(destination) and not exists(source):
                if differences(move["inventory"], inventory(destination)):
                    raise Incomplete("A moved copy no longer matches its inventory", {"path": str(destination)})
                rename(destination, source, f"{op}:undo-move-{index}")
            elif exists(destination) or not exists(source):
                raise Incomplete("A moved copy cannot be located unambiguously", {"from": str(source), "to": str(destination)})
        problems = differences(self.record["origin"], inventory(target))
        for move in self.record["moves"]:
            problems += differences(move["inventory"], inventory(Path(move["from"])))
        if problems:
            raise Incomplete("The restored state does not match the origin inventory", problems)
        self.save("undone")

    def restore_from_backup(self) -> None:
        backup = self.record.get("origin_backup")
        if not backup or differences(self.record["origin"], inventory(Path(backup))):
            raise Incomplete("The origin copy is missing and no verified backup exists", {"target": str(self.target)})
        copy = Path(self.record["parked"]).parent / "restore" / SKILL
        if exists(copy):
            raise Incomplete("A previous restoration copy is in the way", {"path": str(copy)})
        copy_tree(Path(backup), copy)
        if differences(self.record["origin"], inventory(copy)):
            raise Incomplete("The restoration copy does not match the origin inventory", {"path": str(copy)})
        rename(copy, self.target, self.record["operation"] + ":undo-restore")


def copy_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(destination), symlinks=True, copy_function=shutil.copy2)
    shutil.copystat(str(source), str(destination))


def begin(state: Path, journal: Path) -> None:
    current = state / "CURRENT"
    if exists(current):
        raise Blocked(f"An interrupted transaction is recorded in {current}; run `recover` first")
    write_durable(current, (str(journal.relative_to(state)) + "\n").encode("utf-8"))


def finish(state: Path) -> None:
    (state / "CURRENT").unlink()
    sync_directory(state)


def attempt(operation: Operation, state: Path) -> None:
    """Run an operation; on failure restore the origin state or report an incomplete restoration."""
    try:
        operation.run()
    except Failure as error:
        failure = error
    except OSError as error:
        failure = Refused(f"Rename failed: {error}")
    else:
        return
    try:
        operation.undo()
    except (Failure, OSError) as error:
        operation.record["undo_error"] = str(error)
        write_json(operation.path, operation.record)
        raise Incomplete(f"{failure}; restoration incomplete: {error}. Recovery data kept in {operation.path.parent}; run `recover`",
                         getattr(error, "details", None))
    finish(state)
    raise Refused(f"{failure}; the original state was restored and verified", failure.details)


# Commands.
def command_verify(options) -> dict:
    return verify_package(load_package(Path(options.package)), Path(options.checksums))


def command_plan(options) -> dict:
    plan = make_plan(options.runtime, options.home, options.state_dir, Path(options.package), Path(options.checksums))
    if options.output:
        write_json(Path(options.output), plan)
    return plan


def command_apply(options) -> dict:
    plan = read_json(Path(options.plan))
    if plan.get("plan_format") != 1:
        raise Refused("Unsupported plan format")
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations = Locations(plan["runtime"], plan["home"], plan["state_root"])
    locations.check()
    with Lock(locations.state):
        fresh = make_plan(plan["runtime"], plan["home"], plan["state_root"], Path(plan["package"]["source"]), Path(options.checksums))
        changed = drift(plan, fresh)
        if changed:
            raise Refused("The package, target or duplicates changed since the plan; plan again", changed)
        if fresh["interrupted"]:
            raise Blocked(f"An interrupted transaction is recorded in {fresh['interrupted']}; run `recover` first")
        requested = [os.path.abspath(path) for path in options.retire_duplicate]
        unowned = [path for path in fresh["duplicates"] if path not in requested]
        if unowned:
            raise Blocked("Other github-workflow copies are discoverable; retain them or name each with --retire-duplicate", unowned)
        unknown = [path for path in requested if path not in fresh["duplicates"]]
        if unknown:
            raise Refused("--retire-duplicate names a path that is not a planned duplicate", unknown)
        return install(fresh, locations, boundary)


def install(plan: dict, locations: Locations, boundary: dict) -> dict:
    package = load_package(Path(plan["package"]["source"]))
    package_files = package.skill_files()
    before = plan["before"]
    transaction = locations.state / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    journal = transaction / "journal.json"
    transaction.mkdir(parents=True)
    begin(locations.state, journal)
    try:
        backup = transaction / "backup" / SKILL
        if before is not None:
            copy_tree(locations.target, backup)
            if differences(before, inventory(backup)):
                raise Refused("The backup copy does not match the target inventory")
        moves = []
        for index, duplicate in enumerate(plan["duplicates"]):
            path = Path(duplicate)
            duplicate_inventory = inventory(path)
            duplicate_backup = transaction / "backup" / f"duplicate-{index}" / path.name
            copy_tree(path, duplicate_backup)
            if differences(duplicate_inventory, inventory(duplicate_backup)):
                raise Refused(f"The backup copy of {path} does not match its inventory")
            moves.append({"from": str(path), "to": str(transaction / "duplicates" / str(index) / path.name),
                          "inventory": duplicate_inventory, "backup": str(duplicate_backup)})
        staged = transaction / "staged" / SKILL
        stage(staged, package_files, locations.target, before or {}, plan["classification"])
        after = inventory(staged)
        problems = staging_problems(after, package_files, before or {}, plan["classification"])
        if problems:
            raise Refused("The staged tree does not match its expected inventory", problems)
        checkpoint("apply:staged")
    except OSError as error:
        finish(locations.state)
        raise Refused(f"Backup or staging failed before any rename: {error}; the target is unchanged")
    except BaseException:
        finish(locations.state)
        raise
    retired = transaction / "retired" / SKILL
    operation = Operation(journal, {
        "operation": "apply",
        "target": str(locations.target),
        "origin": before,
        "origin_backup": str(backup) if before is not None else None,
        "incoming": after,
        "incoming_path": str(staged),
        "parked": str(retired),
        "moves": moves,
    })
    attempt(operation, locations.state)
    discovered = discoverable(locations.roots)
    if discovered != [str(locations.target)]:
        operation.record["discovered"] = discovered
        try:
            operation.undo()
        except (Failure, OSError) as error:
            raise Incomplete(f"Exactly one discoverable copy was not established and restoration is incomplete: {error}")
        finish(locations.state)
        raise Refused("Exactly one discoverable copy was not established; the original state was restored", discovered)
    classification = plan["classification"]
    receipt = {
        "receipt_format": 1,
        "installer_version": INSTALLER_VERSION,
        "package": plan["package"],
        "retirement_list": RETIREMENT_LIST,
        **locations.describe(),
        "transaction": str(transaction),
        "journal": str(journal),
        "backup": str(backup) if before is not None else None,
        "retired": str(retired) if before is not None else None,
        "before": before,
        "after": after,
        "retired_paths": sorted(name for name, kind in classification.items() if kind == "named obsolete"),
        "dropped_paths": sorted(name for name, kind in classification.items() if kind == "regenerable cache"),
        "preserved_paths": sorted(name for name, kind in classification.items() if kind in PRESERVED),
        "replaced_paths": sorted(name for name, kind in classification.items() if kind == "package"),
        "duplicates": [{"path": move["from"], "retired_to": move["to"], "backup": move["backup"]} for move in moves],
        "legacy_commands": plan["legacy_commands"],
        "instruction_files": file_hashes(locations.instructions),
        "maintenance_boundary": boundary,
        "test_hooks": active_test_hooks(),
        "discovered_after": discovered,
        "state": "installed",
        "note": "Two renames are not an atomic or continuously available replacement; the maintenance boundary covers the gap.",
    }
    write_json(transaction / "receipt.json", receipt)
    operation.save("committed")
    finish(locations.state)
    return {"result": "installed", "receipt": str(transaction / "receipt.json"), "target": str(locations.target),
            "retired_paths": receipt["retired_paths"], "dropped_paths": receipt["dropped_paths"],
            "preserved_paths": receipt["preserved_paths"], "duplicates_retired": [move["from"] for move in moves]}


def stage(staged: Path, package_files: dict, target: Path, before: dict, classification: dict) -> None:
    """Write package files, copy preserved files and keep every non-cache directory."""
    staged.mkdir(parents=True)
    for name, data in sorted(package_files.items()):
        path = staged / Path(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name.endswith(".sh") else 0o644)
    for name, kind in sorted(classification.items()):
        path = staged / Path(*PurePosixPath(name).parts)
        if kind in PRESERVED:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(target / Path(*PurePosixPath(name).parts)), str(path))
        elif kind == "directory":
            path.mkdir(parents=True, exist_ok=True)


def staging_problems(staged: dict, package_files: dict, before: dict, classification: dict) -> list:
    """Compare the staged inventory with the files and directories staging promised."""
    files = {name: {"sha256": digest(data), "bytes": len(data)} for name, data in package_files.items()}
    files.update({name: {"sha256": before[name]["sha256"], "bytes": before[name]["bytes"]}
                  for name, kind in classification.items() if kind in PRESERVED})
    directories = {name for name, kind in classification.items() if kind == "directory"}
    for name in files:
        directories.update(parent.as_posix() for parent in PurePosixPath(name).parents if parent != PurePosixPath("."))
    problems = []
    for name in sorted(set(files) | set(staged)):
        entry = staged.get(name)
        if name in files:
            actual = {"sha256": entry.get("sha256"), "bytes": entry.get("bytes")} if entry else None
            if actual != files[name]:
                problems.append({"path": name, "expected": files[name], "actual": entry})
            elif name in before and classification.get(name) in PRESERVED and entry["mode"] != before[name]["mode"]:
                problems.append({"path": name, "expected": before[name], "actual": entry})
        elif name in directories:
            if entry["type"] != "dir":
                problems.append({"path": name, "expected": "dir", "actual": entry})
        else:
            problems.append({"path": name, "expected": None, "actual": entry})
    problems += [{"path": name, "expected": "dir", "actual": None} for name in sorted(directories - set(staged))]
    return problems


def command_rollback(options) -> dict:
    receipt_path = Path(os.path.abspath(options.receipt))
    receipt = read_json(receipt_path)
    if receipt.get("receipt_format") != 1:
        raise Refused("Unsupported receipt format")
    if receipt.get("state") != "installed":
        raise Refused(f"The receipt state is {receipt.get('state')!r}; only an installed receipt can be rolled back")
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations = Locations(receipt["runtime"], receipt["home"], receipt["state_root"])
    if str(locations.target) != receipt["target"]:
        raise Refused("The receipt target does not match its runtime locations")
    locations.check()
    with Lock(locations.state):
        if exists(locations.state / "CURRENT"):
            raise Blocked(f"An interrupted transaction is recorded in {locations.state / 'CURRENT'}; run `recover` first")
        changed = differences(receipt["after"], inventory(locations.target))
        if changed:
            raise Refused("The active tree no longer matches the receipt's after-inventory; rollback refused", changed)
        transaction = Path(receipt["transaction"])
        work = transaction / ("rollback-" + uuid.uuid4().hex[:8])
        journal = work / "journal.json"
        work.mkdir(parents=True)
        begin(locations.state, journal)
        try:
            incoming_path = None
            if receipt["before"] is not None:
                retired = Path(receipt["retired"])
                if exists(retired) and not differences(receipt["before"], inventory(retired)):
                    incoming_path = retired
                else:
                    incoming_path = work / "restore" / SKILL
                    copy_tree(Path(receipt["backup"]), incoming_path)
                    if differences(receipt["before"], inventory(incoming_path)):
                        raise Incomplete("Neither the retired copy nor the backup matches the before-inventory")
            moves = []
            for duplicate in receipt["duplicates"]:
                source, original = Path(duplicate["retired_to"]), Path(duplicate["path"])
                duplicate_inventory = inventory(Path(duplicate["backup"]))
                if exists(original) or differences(duplicate_inventory, inventory(source)):
                    raise Refused(f"The retired duplicate {original} cannot be restored exactly")
                moves.append({"from": str(source), "to": str(original), "inventory": duplicate_inventory})
        except BaseException:
            finish(locations.state)
            raise
        operation = Operation(journal, {
            "operation": "rollback",
            "receipt": str(receipt_path),
            "journal": str(journal),
            "target": str(locations.target),
            "origin": receipt["after"],
            "origin_backup": None,
            "incoming": receipt["before"],
            "incoming_path": str(incoming_path) if incoming_path else None,
            "parked": str(work / "parked" / SKILL),
            "moves": moves,
        })
        attempt(operation, locations.state)
        operation.record["rollback_evidence"] = {"maintenance_boundary": boundary, "test_hooks": active_test_hooks()}
        operation.save("committed")
        mark_rolled_back(operation.record)
        finish(locations.state)
        return {"result": "rolled back", "target": str(locations.target), "restored": "absent" if receipt["before"] is None else "before-inventory"}


def mark_rolled_back(record: dict) -> None:
    receipt_path = Path(record["receipt"])
    receipt = read_json(receipt_path)
    receipt["state"] = "rolled back"
    receipt["rollback"] = {"journal": record["journal"], "parked": record["parked"], **record.get("rollback_evidence", {})}
    write_json(receipt_path, receipt)


def command_recover(options) -> dict:
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations = Locations(options.runtime, options.home, options.state_dir)
    locations.check()
    with Lock(locations.state):
        current = locations.state / "CURRENT"
        if not exists(current):
            return {"result": "nothing to recover", "state_root": str(locations.state)}
        journal = locations.state / current.read_text(encoding="utf-8").strip()
        if not exists(journal):
            # The journal is written durably before the first rename, so nothing was renamed.
            finish(locations.state)
            return {"result": "no rename happened; staging data kept", "transaction": str(journal.parent)}
        record = read_json(journal)
        operation = Operation(journal, record)
        if record.get("state") == "committed":
            if record["operation"] == "rollback":
                mark_rolled_back(record)
            finish(locations.state)
            return {"result": "already committed", "journal": str(journal)}
        operation.record["recovery_boundary"] = boundary
        operation.undo()
        receipt = journal.parent / "receipt.json"
        if record["operation"] == "apply" and exists(receipt):
            value = read_json(receipt)
            value["state"] = "recovered to the before-state; not installed"
            write_json(receipt, value)
        finish(locations.state)
        return {"result": "restored", "operation": record["operation"], "journal": str(journal), "target": record["target"]}


def parser() -> argparse.ArgumentParser:
    here = Path(os.path.abspath(__file__)).parent
    top = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-package", help="verify an extracted package or ZIP against its checksum file")
    verify.add_argument("package")
    verify.add_argument("--checksums", required=True)
    plan = commands.add_parser("plan", help="read-only inventory, classification and duplicates")
    plan.add_argument("--runtime", choices=("claude", "codex"), required=True)
    plan.add_argument("--home")
    plan.add_argument("--state-dir")
    plan.add_argument("--package", default=str(here))
    plan.add_argument("--checksums", required=True)
    plan.add_argument("--output")
    apply = commands.add_parser("apply", help="install from a plan during a maintenance boundary")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--checksums", required=True)
    apply.add_argument("--maintenance-confirmed", action="store_true")
    apply.add_argument("--retire-duplicate", action="append", default=[])
    rollback = commands.add_parser("rollback", help="restore the before-state recorded in a receipt")
    rollback.add_argument("--receipt", required=True)
    rollback.add_argument("--maintenance-confirmed", action="store_true")
    recover = commands.add_parser("recover", help="restore the state before an interrupted transaction")
    recover.add_argument("--runtime", choices=("claude", "codex"), required=True)
    recover.add_argument("--home")
    recover.add_argument("--state-dir")
    recover.add_argument("--maintenance-confirmed", action="store_true")
    return top


def main(argv=None) -> int:
    options = parser().parse_args(argv)
    handlers = {"verify-package": command_verify, "plan": command_plan, "apply": command_apply,
                "rollback": command_rollback, "recover": command_recover}
    try:
        result = handlers[options.command](options)
    except Failure as error:
        print(json.dumps({"error": str(error), "exit": error.code, "details": error.details}, indent=2, ensure_ascii=False), file=sys.stderr)
        return error.code
    except OSError as error:
        print(json.dumps({"error": f"Unexpected filesystem error: {error}", "exit": 3}, indent=2), file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
