#!/usr/bin/env python3
"""Verify a dev-harness package and install, roll back or recover its github-workflow skill.

Standard library only; Python 3.8+. Exit codes: 0 done, 1 refused or failed and restored,
2 blocked before any change, 3 restoration incomplete (recovery data kept; run `recover`).
"""
from __future__ import annotations

import argparse
import errno
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
import tempfile
import time
import unicodedata
import uuid
import zipfile
import zlib

INSTALLER_VERSION = "1.1.0"
SKILL = "github-workflow"
SKILL_PREFIX = "skills/" + SKILL + "/"
# The installer's own path in the package; apply keeps a verified copy of it with each receipt.
PACKAGE_INSTALLER = "install.py"
# Obsolete v5.2 files owned by earlier installations. They leave the active tree but stay in the
# retired and backup copies, so rollback restores them. Retirement is by pathname, without a hash
# check, so an edited copy is retired too; the plan, apply output and receipt name each one (#43).
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
# Command-line path components that identify a skill-consuming runtime process.
CONSUMERS = {"claude", "claude-code", "codex"}
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
# IO_REPARSE_TAG_SYMLINK and IO_REPARSE_TAG_MOUNT_POINT (junctions). Other reparse points, such as
# cloud placeholders, hold their own content.
LINK_REPARSE_TAGS = {0xA000000C, 0xA0000003}
MEMBER_LIMIT = 64 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024


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


def contains(parent: str, child: str) -> bool:
    """Whether the normalized, resolved child path is the parent or inside it."""
    parent, child = (os.path.normcase(os.path.realpath(path)) for path in (parent, child))
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def require_test_home(locations: "Locations") -> None:
    """Honour test hooks only when every location the command can touch is under the temporary directory."""
    hooks = active_test_hooks()
    if not hooks:
        return
    temporary = tempfile.gettempdir()
    paths = [locations.home, locations.config, locations.skills, locations.state, *locations.roots]
    if hooks.get(TEST_TRACE):
        paths.append(os.path.abspath(hooks[TEST_TRACE]))
    outside = [str(path) for path in paths if not contains(temporary, str(path)) or contains(str(path), temporary)]
    if outside:
        raise Blocked("Installer test hooks are set outside a temporary test home; unset them",
                      {"hooks": sorted(hooks), "outside": outside})


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
    # A crash here models a kill after the rename and before the journal records it.
    checkpoint(point + ":done")


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


def read_json(path: Path, retry: str | None = None) -> dict:
    """With retry, a directory on the way to path that cannot be searched blocks with its fix (#54)."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except PermissionError as error:
        if retry is not None:
            searchable_part(Path(os.path.abspath(path)), retry)
        raise Refused(f"Cannot read {path}: {error}")
    except (OSError, ValueError) as error:
        raise Refused(f"Cannot read {path}: {error}")
    if not isinstance(value, dict):
        raise Refused(f"{path} does not hold a JSON object")
    return value


# Filesystem inventory.
def is_link(path: Path) -> bool:
    try:
        status = os.lstat(str(path))
    except (FileNotFoundError, NotADirectoryError):
        return False  # Nothing, link or not, is below a file (#68).
    if stat.S_ISLNK(status.st_mode):
        return True
    if getattr(status, "st_file_attributes", 0) & REPARSE_POINT:
        # Without a readable tag, the reparse point is treated as a link.
        tag = getattr(status, "st_reparse_tag", None)
        return tag is None or tag in LINK_REPARSE_TAGS
    return False


def exists(path: Path) -> bool:
    return os.path.lexists(str(path))


def permission_fix(path: Path, needed: int, permission: str, held: str, allow: str) -> tuple[str | None, str]:
    """The mode of a path the OS refused and the fix for it (#54).

    On POSIX the fix follows the owner and mode: a path another user owns (one created with sudo) or one whose
    owner already has the needed permission (an ACL or a macOS privacy control) is not fixed by the owner's mode."""
    try:
        status = os.stat(str(path))
    except OSError:
        status = None
    mode = f"{stat.S_IMODE(status.st_mode):04o}" if status else None
    fix = f"add owner {permission} permission to {path}"
    if status and os.name != "nt":
        if status.st_uid != os.geteuid():
            fix = (f"{path} belongs to another user (uid {status.st_uid}): have its owner or an administrator give you "
                   f"{permission} access to it, or its ownership")
        elif status.st_mode & needed == needed:
            fix = (f"its owner already has {held} permission, so an access control list or system privacy "
                   f"setting denies access: allow this process to {allow} {path}")
    return mode, fix


def inaccessible(path: Path, action: str, retry: str = "plan again") -> Blocked:
    """A directory the OS refused to search or list, named with the fix instead of an error on one of its children (#54).

    Read and search permission are both named: either alone leaves the directory uninspectable."""
    mode, fix = permission_fix(path, stat.S_IRUSR | stat.S_IXUSR, "read and search (execute)", "read and search",
                               "read and search")
    shown = f" (mode {mode})" if mode else ""
    return Blocked(f"{path} cannot be {action}{shown}, so the installer cannot inspect what it holds; {fix}, then {retry}",
                   {"path": str(path), "mode": mode})


def unwritable(path: Path, reason: str, retry: str) -> Blocked:
    """A directory or file the OS refused to write before anything changed, named with the fix (#68).

    Creating or opening an entry needs write and search permission on its directory, so both are named there."""
    if os.path.isdir(str(path)):
        mode, fix = permission_fix(path, stat.S_IWUSR | stat.S_IXUSR, "write and search (execute)", "write and search",
                                   "write to and search")
        action = "written or searched"
    else:
        mode, fix = permission_fix(path, stat.S_IWUSR, "write", "write", "write to")
        action = "written"
    shown = f" (mode {mode})" if mode else ""
    return Blocked(f"{path} cannot be {action}{shown}, {reason}; {fix}, then {retry}", {"path": str(path), "mode": mode})


def probe_writable(directory: Path, reason: str, retry: str, folders: bool = False) -> None:
    """Block, before anything changes, when directory refuses the files, and with folders the folders, that an action
    creates there (#68, #71).

    The OS is asked directly to create them, which also covers POSIX ACLs and a directory another user owns (one a
    sudo run created); a Windows ACL can allow files but deny folders. Only the creation decides: removing a probe and
    any other failure stay unexpected errors."""
    try:
        descriptor, probe = tempfile.mkstemp(prefix=".write-check-", dir=str(directory))
    except PermissionError:
        raise unwritable(directory, reason, retry) from None
    os.close(descriptor)
    os.unlink(probe)
    if folders:
        try:
            probe = tempfile.mkdtemp(prefix=".write-check-", dir=str(directory))
        except PermissionError:
            raise refuses_folders(directory, reason, retry) from None
        os.rmdir(probe)


def refuses_folders(path: Path, reason: str, retry: str) -> Blocked:
    """A directory that accepted a new file but refused a new folder, as a Windows access control list allows (#68)."""
    return Blocked(f"{path} accepts new files but refuses new folders, {reason}; allow this account to create folders in "
                   f"{path} (an access control list or security policy denies it), then {retry}", {"path": str(path)})


def searchable_part(path: Path, retry: str) -> Path | None:
    """The deepest existing path on the way to path, or None when none exists.

    Top-down with lstat, as in check(): a directory that cannot be searched is named with its fix,
    never taken for an absent one."""
    found = None
    for part in reversed([path, *path.parents]):
        try:
            os.lstat(str(part))
        except (FileNotFoundError, NotADirectoryError):
            break
        except PermissionError:
            raise inaccessible(part.parent, "searched", retry) from None
        found = part
    return found


def inventory(root: Path, retry: str | None = None) -> dict | None:
    """Relative POSIX path -> type, mode and, for files, SHA-256 and size. None when absent.

    The root itself is the `.` entry, so its mode takes part in every drift and restoration check.

    A link, junction or special file anywhere in the tree blocks: its target is outside what an
    exact rollback can own.
    With retry, given by a command checking before any rename, a directory in the tree that cannot be
    listed or searched blocks with its fix (#54); inside a transaction the error stays an OSError.
    """
    if not exists(root):
        return None
    if is_link(root):
        raise Blocked(f"{root} is a link or junction; its canonical source is not identified", {"path": str(root)})
    if not root.is_dir():
        raise Blocked(f"{root} is not a directory", {"path": str(root)})
    entries = {".": {"type": "dir", "mode": stat.S_IMODE(os.lstat(str(root)).st_mode)}}
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            listing = os.scandir(str(directory))
        except PermissionError:
            if retry is None:
                raise
            raise inaccessible(directory, "listed", retry) from None
        with listing:
            for item in listing:
                relative = prefix + item.name
                path = directory / item.name
                try:
                    # Listing needs read permission only; reaching an entry also needs search permission.
                    linked, status = is_link(path), os.lstat(str(path))
                except PermissionError:
                    if retry is None:
                        raise
                    raise inaccessible(directory, "searched", retry) from None
                if linked:
                    raise Blocked(f"Link or junction inside the tree: {path}", {"path": str(path)})
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
    if source.stat().st_size > ARCHIVE_LIMIT:
        raise Refused(f"The archive exceeds {ARCHIVE_LIMIT} bytes")
    archive = source.read_bytes()
    total = 0
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
                # Bounded reads: the declared size of a member is not trusted.
                with bundle.open(member) as stream:
                    data = stream.read(MEMBER_LIMIT + 1)
                total += len(data)
                if len(data) > MEMBER_LIMIT or total > ARCHIVE_LIMIT:
                    raise Refused(f"The archive expands beyond the size limits at {member.filename}")
                files[name] = data
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError) as error:
        raise Refused(f"Not a valid ZIP archive: {error}")
    except (RuntimeError, NotImplementedError) as error:
        raise Refused(f"Unsupported ZIP archive (encrypted or unknown compression): {error}")
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
    unsafe = set()
    for name in sorted(listed):
        try:
            package_path(name)
        except ValueError as error:
            problems.append(str(error))
            unsafe.add(name)
    for name in sorted(set(listed) - set(package.files) - unsafe):
        problems.append(f"missing: {name}")
    for name in sorted(set(package.files) - set(listed)):
        problems.append(f"extra: {name}")
    for name in sorted((set(listed) & set(package.files)) - unsafe):
        expected, data = listed[name], package.files[name]
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
    lines = data.decode("utf-8-sig", "replace").splitlines()
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
    """Where a runtime discovers skills. The resolved config root is recorded in plans and receipts,
    so later commands address the same directories whatever the environment then says."""

    def __init__(self, runtime: str, home: str | None, config: str | None = None) -> None:
        explicit = home is not None
        user_home = Path(os.path.abspath(home)) if explicit else Path.home()
        self.runtime = runtime
        self.home = user_home
        if runtime == "claude":
            configured = config or (None if explicit else os.environ.get("CLAUDE_CONFIG_DIR"))
            self.config = Path(os.path.abspath(configured)) if configured else user_home / ".claude"
            self.skills = self.config / "skills"
            self.roots = [self.skills]
            self.instructions = [self.config / "CLAUDE.md", self.config / "AGENTS.md"]
            self.legacy = [self.config / "commands" / (SKILL + ".md")]
        elif runtime == "codex":
            configured = config or (None if explicit else os.environ.get("CODEX_HOME"))
            self.config = Path(os.path.abspath(configured)) if configured else user_home / ".codex"
            self.skills = user_home / ".agents" / "skills"
            self.roots = [self.skills, self.config / "skills"]
            self.instructions = [self.config / "AGENTS.md", self.config / "AGENTS.override.md"]
            self.legacy = []
        else:
            raise Refused(f"Unknown runtime: {runtime}")
        self.target = self.skills / SKILL
        self.state = self.skills.parent / "dev-harness-install"

    def require_movable(self, recorded_mode: int | None = None, retry: str = "plan again", moves=()) -> None:
        """Block before any rename when the selected skill directory, its skill root or a moved duplicate lacks owner write permission.

        A conservative precondition of this installer, not a full access check: POSIX refuses to move a
        directory to another parent without write permission on it, or to rename inside a root without
        write permission there, so apply and rollback would stop at their first rename. It runs after the
        location checks, which already block a root that cannot be searched or listed (#54). Windows is not
        checked: a directory's read-only attribute does not prevent renames there, and Explorer sets it on
        customized folders.
        recorded_mode is the target mode a receipt expects, named so one fix also satisfies its drift check;
        a recorded mode without owner write is also satisfied by that mode with owner write added.
        moves adds (directory, reason) pairs for retired duplicates, which leave their parent on apply and
        return to it on rollback.
        """
        if os.name == "nt":
            return
        for path, reason in ((self.skills, f"so {SKILL} cannot be moved in or out of it"),
                             (self.target, "so it cannot be moved to another directory"), *moves):
            if not exists(path):
                continue
            # A rename writes to the physical directory, so a link the location checks accept (a secondary root
            # linked since apply) is followed: the link's own mode, often 0777, says nothing about its target.
            # Links above path are already followed by lstat.
            physical = Path(os.path.realpath(str(path))) if is_link(path) else path
            try:
                status = os.stat(str(path))
            except FileNotFoundError:
                continue  # A dangling link is not refused here, as before: its own mode passed too.
            except PermissionError:
                raise inaccessible(physical.parent, "searched", retry) from None
            if not status.st_mode & stat.S_IWUSR:
                mode = stat.S_IMODE(status.st_mode)
                hint = f"add owner write permission to {physical}"
                if path == self.target and recorded_mode is not None:
                    hint += f" (the receipt records mode {recorded_mode:04o}"
                    if not recorded_mode & stat.S_IWUSR:
                        hint += f"; rollback also accepts {recorded_mode | stat.S_IWUSR:04o}"
                    hint += ")"
                named = str(path) if physical == path else f"{path} leads to {physical}, which"
                details = {"path": str(path), "mode": f"{mode:04o}"}
                if physical != path:
                    details["physical"] = str(physical)
                raise Blocked(f"{named} has no owner write permission (mode {mode:04o}), {reason}; {hint}, then {retry}",
                              details)

    def describe(self) -> dict:
        return {
            "runtime": self.runtime,
            "home": str(self.home),
            "config_root": str(self.config),
            "target": str(self.target),
            "skill_roots": [str(root) for root in self.roots],
            "state_root": str(self.state),
        }

    def check(self, retry: str = "plan again") -> None:
        """Block when the target's canonical source or the state location cannot be owned safely."""
        # Top-down, so a directory that cannot be searched or listed is named before any of its children is
        # inspected (#54). The OS decides, so ownership and ACLs need no mode reasoning: lstat needs search
        # permission on the parent, listdir read permission on the directory itself.
        for root in self.roots:
            for path in reversed([root / SKILL, root, *root.parents]):
                try:
                    os.lstat(str(path))
                except (FileNotFoundError, NotADirectoryError):
                    break
                except PermissionError:
                    raise inaccessible(path.parent, "searched", retry) from None
                if path in (self.skills.parent, self.skills) and is_link(path):
                    break  # Refused as a link below, before anything behind it is inspected.
                if path == root and os.path.isdir(str(root)):
                    try:
                        os.listdir(str(root))
                    except PermissionError:
                        raise inaccessible(root, "listed", retry) from None
        for path in (self.skills.parent, self.skills, self.target):
            if is_link(path):
                raise Blocked(f"{path} is a link or junction; its canonical source is not identified", {"path": str(path)})
        if exists(self.target) and SKILL not in os.listdir(str(self.skills)):
            # A case-insensitive volume resolves another spelling to the target.
            raise Blocked(f"{self.target} exists under another spelling; rename it to {SKILL} exactly, then plan again")
        for root in self.roots:
            if contains(str(root), str(self.state)):
                raise Blocked(f"The state directory {self.state} is inside the skill root {root}")
        if is_link(self.state):
            raise Blocked(f"The state directory {self.state} is a link")
        for path in [self.state, *self.state.parents]:
            if exists(path / ".git"):
                raise Blocked(f"The state directory {self.state} is inside the Git work tree {path}; "
                              "the installer never writes installation state into a repository")
        anchor = next((path for path in [self.state, *self.state.parents] if path.exists()), None)
        volume = next((path for path in [self.target, *self.target.parents] if path.exists()), None)
        if anchor is None or volume is None or os.stat(str(anchor)).st_dev != os.stat(str(volume)).st_dev:
            raise Blocked(f"The state directory {self.state} is not on the target's volume")


def same_path(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def declares_skill(directory: Path) -> bool:
    try:
        return skill_name((directory / "SKILL.md").read_bytes()) == SKILL
    except OSError:
        return False


def discoverable(roots) -> list:
    """Every directory under the skill roots that a runtime could load as github-workflow.

    Linked directories are reported, not followed, so the plan can block on them. A directory that cannot
    be listed or searched blocks: whether it holds another copy cannot be established (#54).
    """
    def unlisted(error: OSError) -> None:
        if isinstance(error, PermissionError):
            raise inaccessible(Path(error.filename), "listed") from None

    found = []
    existing = [root for root in roots if root.is_dir()]
    for index, root in enumerate(existing):
        # A root that is, or lies inside, another root (for example a linked or shared Codex root)
        # is walked once, so one directory is never counted twice.
        if any(contains(str(other), str(root)) and (not contains(str(root), str(other)) or position < index)
               for position, other in enumerate(existing) if position != index):
            continue
        for directory, subdirectories, names in os.walk(str(root), onerror=unlisted):
            kept = []
            try:
                if subdirectories or names:
                    # Listing needs read permission only; reaching any entry also needs search permission.
                    try:
                        os.lstat(os.path.join(directory, [*subdirectories, *names][0]))
                    except FileNotFoundError:
                        pass
                for name in sorted(subdirectories):
                    path = Path(directory) / name
                    if not is_link(path):
                        kept.append(name)
                    elif name == SKILL or declares_skill(path):
                        found.append(str(path))
            except PermissionError:
                raise inaccessible(Path(directory), "searched") from None
            subdirectories[:] = kept
            path = Path(directory)
            if "SKILL.md" in names and (path.name == SKILL or declares_skill(path)):
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


def retirements(classification: dict, before: dict | None, backup: Path | None = None, retired: Path | None = None) -> list:
    """Each named obsolete file with its hash; with the transaction copies, where its exact bytes are kept."""
    entries = []
    for name in sorted(name for name, kind in classification.items() if kind == "named obsolete"):
        entry = {"path": name, "listed_as": RETIREMENT_LIST["paths"][name], "sha256": before[name]["sha256"],
                 "bytes": before[name]["bytes"]}
        if retired is not None and backup is not None:
            parts = PurePosixPath(name).parts
            entry.update(retired_copy=str(retired.joinpath(*parts)), backup=str(backup.joinpath(*parts)))
        entries.append(entry)
    return entries


def make_plan(runtime: str, home: str | None, config: str | None, package_source: Path, checksums: Path):
    """Return the plan and the verified package it describes."""
    locations = Locations(runtime, home, config)
    locations.check()
    locations.require_movable()
    package = load_package(package_source)
    verification = verify_package(package, checksums)
    package_files = package.skill_files()
    before = inventory(locations.target, "plan again")
    classification = {name: classify(name, entry, package_files) for name, entry in (before or {}).items()}
    preserved = [name for name, kind in classification.items() if kind in PRESERVED]
    kept = [name for name, kind in classification.items() if kind == "directory"]
    conflicts = colliding(list(package_files) + preserved)
    package_directories = {destination_key(parent.as_posix()) for name in package_files for parent in PurePosixPath(name).parents}
    conflicts += [name for name in kept if destination_key(name) in {destination_key(file) for file in package_files}]
    conflicts += [name for name in preserved if destination_key(name) in package_directories]
    if conflicts:
        raise Blocked("Preserved files collide with package paths", conflicts)
    duplicates = [path for path in discoverable(locations.roots) if not same_path(path, str(locations.target))]
    linked = [path for path in duplicates if is_link(Path(path))]
    if linked:
        raise Blocked("Linked github-workflow copies are discoverable and their canonical source is not identified. "
                      "Owner: the operator. Trigger: remove the link or move it out of the skill roots, then plan again", linked)
    # Only a leaf copy can be retired: never a skill root, nor a folder holding the target, a root or another copy.
    target, roots = str(locations.target), [str(root) for root in locations.roots]
    overlapping = []
    for path in duplicates:
        copies = [item for item in duplicates if item != path]
        if any(contains(path, other) for other in [target, *roots, *copies]) or any(contains(other, path) for other in [target, *copies]):
            overlapping.append(path)
    if overlapping:
        raise Blocked("A github-workflow SKILL.md overlaps a skill root, the target or another copy. Owner: the operator. "
                      "Trigger: move the stray SKILL.md or nested copy out of the skill roots, then plan again", overlapping)
    # Each duplicate leaves its root on apply (--retire-duplicate) and returns on rollback (#54).
    locations.require_movable(moves=[move for path in duplicates for move in (
        (Path(path).parent, f"so the duplicate {path} cannot be moved out of it"),
        (Path(path), "so this duplicate cannot be moved to another directory"))])
    plan = {
        "plan_format": 1,
        "installer_version": INSTALLER_VERSION,
        **locations.describe(),
        "package": verification,
        "checksums": str(Path(os.path.abspath(str(checksums)))),
        "retirement_list": RETIREMENT_LIST,
        "before": before,
        "classification": classification,
        # Derived from before and classification, but still drift keys: the operator approves this preview,
        # and apply retires from a fresh plan, so an edited or truncated preview must stop apply (#43).
        "retirements": retirements(classification, before),
        "retirement_copies": {"state_root": str(locations.state), "transaction": None,
                              "note": "The transaction directory is allocated at apply; the apply output and receipt "
                                      "give each retired file's retired_copy and backup location, and a rollback_command "
                                      "that runs the package installer's verified copy kept in that directory"},
        "duplicates": duplicates,
        "duplicate_inventories": {path: inventory(Path(path), "plan again") for path in duplicates},
        "legacy_commands": [str(path) for path in locations.legacy if exists(path)],
        "interrupted": str(locations.state / "CURRENT") if exists(locations.state / "CURRENT") else None,
    }
    return plan, package


DRIFT_KEYS = ("installer_version", "runtime", "home", "config_root", "target", "skill_roots", "state_root", "package", "retirement_list",
              "before", "classification", "retirements", "retirement_copies", "duplicates", "duplicate_inventories")


def drift(plan: dict, fresh: dict) -> list:
    return [key for key in DRIFT_KEYS if canonical(plan.get(key)) != canonical(fresh.get(key))]


# Maintenance boundary.
def is_consumer(command_line: str) -> bool:
    """Conservative: any path component of any argument naming a consumer counts, so wrappers such as
    `node .../claude-code/cli.js` or a quoted Windows path are caught. False positives only block."""
    for token in command_line.split():
        for component in re.split(r"[\\/]", token.strip("\"'")):
            name = component.lower()
            for suffix in (".exe", ".cmd", ".js", ".mjs"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
            if name in CONSUMERS:
                return True
    return False


def own_lines_removed(lines) -> list:
    """Drop this installer's line and the launchers above it (`py -3 install.py ...`, `sh -c ...`).

    Each line is `PID PPID COMMAND`. An ancestor is dropped only when its command line names this script
    and the words before that name do not name a consumer. The arguments after it can name the runtime
    (`--runtime codex`), while `claude -p "run install.py"` or `node .../claude-code/cli.js` is a live
    consumer and still counts. The listing must include this process: an empty or truncated listing
    must not read as "no consumer".
    """
    table, unparsed = {}, []
    for line in lines:
        fields = line.strip().split(None, 2)
        if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = (int(fields[1]), fields[2] if len(fields) > 2 else "")
        elif line.strip():
            unparsed.append(line.strip())
    own = os.getpid()
    if own not in table:
        raise Blocked("Consumer state is unverifiable: the process listing does not include this installer")
    script = os.path.basename(os.path.abspath(__file__)).lower()
    dropped, pid = {own}, table[own][0]
    while pid in table and pid not in dropped:
        words = table[pid][1].split()
        named = [index for index, word in enumerate(words) if script in word.lower()]
        if named and not is_consumer(" ".join(words[: named[0]])):
            dropped.add(pid)
        pid = table[pid][0]
    return [command for pid, (_, command) in table.items() if pid not in dropped] + unparsed


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
        listed = [line.strip() for line in lines if line.strip()]
    else:
        if os.name == "nt":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            script = "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1} {2}' -f $_.ProcessId, $_.ParentProcessId, $_.CommandLine }"
            command = [shell, "-NoProfile", "-NonInteractive", "-Command", script] if shell else None
        else:
            ps = shutil.which("ps")
            command = [ps, "-A", "-ww", "-o", "pid=,ppid=,args="] if ps else None
        if not command:
            raise Blocked("Consumer state is unverifiable: no process listing command found")
        probe = " ".join(command)
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise Blocked(f"Consumer state is unverifiable: {error}")
        if result.returncode != 0:
            raise Blocked(f"Consumer state is unverifiable: the process listing exited {result.returncode}")
        listed = own_lines_removed(result.stdout.splitlines())
    active = [line for line in listed if is_consumer(line)]
    if active:
        raise Blocked("Active Claude or Codex processes use skills; stop them and rerun", active)
    return {"confirmed_by_operator": True, "probe": probe, "active_consumers": [], "limitations": BOUNDARY_LIMITS}


class Lock:
    """Exclusive installer lock, released by the operating system if the process dies."""

    def __init__(self, state: Path, retry: str) -> None:
        self.state, self.retry = state, retry
        lock = state / "LOCK"
        try:
            state.mkdir(parents=True, exist_ok=True)
            self.stream = open(lock, "a+b")
        except OSError as error:
            # Nothing has changed yet, so a refused setup is a blocked state with its fix, not an uncertain one (#68).
            blocked = Lock.setup_blocked(state, error, retry)
            if blocked is None:
                raise  # Any other failure, or a type conflict gone before it is named, stays an unexpected error.
            raise blocked from None
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
            raise Blocked(f"Another installer holds {lock}")

    @staticmethod
    def setup_blocked(state: Path, error: OSError, retry: str) -> Blocked | None:
        lock = state / "LOCK"
        # A path of the wrong type is named first: Windows reports a LOCK directory as a permission error, and
        # mkdir(parents=True) can stop at any ancestor that is a file.
        for path in (lock, state, *state.parents):
            if exists(path) and os.path.isdir(str(path)) == (path == lock):
                kind = "a directory, not the installer's lock file" if path == lock else "not a directory"
                return Blocked(f"{path} is {kind}, so the installer cannot keep its state there; move it out of the way, "
                               f"then {retry}", {"path": str(path)})
        failed = Path(error.filename) if error.filename else lock
        if error.errno == errno.EROFS:
            volume = next((path for path in (failed, *failed.parents) if exists(path)), failed)
            return Blocked(f"{volume} is on a read-only file system, so the installer cannot create its state directory "
                           f"or lock file; make that file system writable, then {retry}", {"path": str(volume)})
        if not isinstance(error, PermissionError):
            return None
        # The OS refused a write: to the file itself when it exists (a read-only LOCK), otherwise to the directory
        # the missing path is created in. A LOCK in a directory that cannot be searched is not seen as existing.
        path = failed if exists(failed) else failed.parent
        reason = ("so the installer cannot take its lock" if path == lock
                  else "so the installer cannot create its state directory or lock file in it")
        return unwritable(path, reason, retry)

    def require_writable(self) -> None:
        """Block, before anything changes, when the state directory refuses the transaction directory, journal and
        CURRENT pointer an action writes there (#68).

        An existing writable LOCK opens without write permission on its directory, so the directory itself is
        probed; a Windows ACL that allows files but denies folders is refused where apply creates its transaction
        folder. A read-only mount already refused the LOCK."""
        probe_writable(self.state, "so the installer cannot record its transaction in it", self.retry)

    def __enter__(self) -> "Lock":
        return self

    def __exit__(self, *_: object) -> None:
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            try:
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
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
        # Apply retires duplicates first; rollback restores them last. Either way the count of
        # discoverable copies exceeds one only where the before-state already had more.
        if not self.record.get("moves_last"):
            self.run_moves()
        if self.record["origin"] is not None:
            self.save("parking")
            parked = Path(self.record["parked"])
            parked.parent.mkdir(parents=True, exist_ok=True)
            rename(self.target, parked, f"{op}:park")
            self.save("parked")
            actual = inventory(parked)
            if differences(self.record["origin"], actual):
                # The rename just moved the tree as it was, so that tree is the state to put back.
                self.record["planned_origin"] = self.record["origin"]
                self.record["origin"] = actual
                self.record["origin_backup"] = None
                self.save("parked-drift")
                raise Refused("The target changed after it was verified; it is put back as found, not as planned. Plan again")
        if self.record["incoming"] is not None:
            self.save("activating")
            self.target.parent.mkdir(parents=True, exist_ok=True)
            rename(Path(self.record["incoming_path"]), self.target, f"{op}:activate")
            self.save("activated")
        if self.record.get("moves_last"):
            self.run_moves()
        problems = differences(self.record["incoming"], inventory(self.target))
        for move in self.record["moves"]:
            problems += differences(move["inventory"], inventory(Path(move["to"])))
        if problems:
            raise Refused("The activated state does not match its verified inventory", problems)

    def run_moves(self) -> None:
        for index, move in enumerate(self.record["moves"]):
            self.save(f"moving-{index}")
            Path(move["to"]).parent.mkdir(parents=True, exist_ok=True)
            rename(Path(move["from"]), Path(move["to"]), self.record["operation"] + f":move-{index}")
            self.save(f"moved-{index}")

    def undo_moves(self) -> None:
        op = self.record["operation"]
        for index, move in reversed(list(enumerate(self.record["moves"]))):
            source, destination = Path(move["from"]), Path(move["to"])
            if exists(source):
                if differences(move["inventory"], inventory(source)) or (
                        exists(destination) and str(destination) not in self.record.get("moved_drift", [])):
                    raise Incomplete("A moved copy cannot be located unambiguously", {"from": str(source), "to": str(destination)})
                continue
            if exists(destination) and not differences(move["inventory"], inventory(destination)):
                rename(destination, source, f"{op}:undo-move-{index}")
                continue
            # The moved copy is lost or changed. Apply kept a verified backup of each retired duplicate.
            backup = move.get("backup")
            if not backup or differences(move["inventory"], inventory(Path(backup))):
                raise Incomplete("A moved copy is missing or changed and no verified backup exists",
                                 {"from": str(source), "to": str(destination)})
            if exists(destination) and str(destination) not in self.record.get("moved_drift", []):
                # Keep the differing copy where it is, as for the target. Saved before the restoring rename,
                # so a rerun after a kill recognizes the kept copy.
                self.record.setdefault("moved_drift", []).append(str(destination))
            self.save(f"restoring-move-{index}")
            self.restore_verified(Path(backup), move["inventory"], destination.parent.parent / f"restore-{index}" / source.name,
                                  source, f"{op}:undo-move-restore-{index}")
            self.save(f"restored-move-{index}")

    def undo(self) -> None:
        """Return to the origin state, verified; Incomplete when that cannot be established.

        Every step first checks whether it is already done, so an interrupted undo can be rerun.
        """
        op = self.record["operation"]
        target, parked = self.target, Path(self.record["parked"])
        origin = self.record["origin"]
        incoming_path = Path(self.record["incoming_path"]) if self.record["incoming_path"] else None
        self.save("undoing")
        if self.record.get("moves_last"):
            self.undo_moves()
        current = inventory(target)
        if current is not None and (origin is None or differences(origin, current)):
            # The target holds something other than the origin: only the verified incoming tree may move.
            if differences(self.record["incoming"], current) or incoming_path is None or exists(incoming_path):
                raise Incomplete("The target matches neither the origin nor the incoming inventory", {"target": str(target)})
            rename(target, incoming_path, f"{op}:undo-activate")
        if origin is not None and not exists(target):
            if exists(parked) and not differences(origin, inventory(parked)):
                rename(parked, target, f"{op}:undo-park")
            else:
                if exists(parked):
                    # Keep the differing copy where it is; restore the verified backup instead.
                    self.record["parked_drift"] = str(parked)
                self.save("restoring")
                self.restore_from_backup()
        if not self.record.get("moves_last"):
            self.undo_moves()
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
        self.restore_verified(Path(backup), self.record["origin"], Path(self.record["parked"]).parent / "restore" / SKILL,
                              self.target, self.record["operation"] + ":undo-restore")

    def restore_verified(self, backup: Path, expected: dict, copy: Path, destination: Path, label: str) -> None:
        """Copy a verified backup beside the transaction, check it, then rename it into place."""
        if exists(copy):
            # A copy left by an interrupted restoration; kept aside, never deleted. The journal names the aside
            # path before the rename, so a kill right after the rename still leaves it in the final report.
            stale = copy.parent.parent / ("restore-stale-" + uuid.uuid4().hex[:8])
            self.record.setdefault("stale_restore_copies", []).append(str(stale))
            write_json(self.path, self.record)
            rename(copy.parent, stale, label + ":set-aside")
        copy_tree(backup, copy)
        fsync_tree(copy)
        if differences(expected, inventory(copy)):
            raise Incomplete("The restoration copy does not match its inventory", {"path": str(copy)})
        rename(copy, destination, label)


def copy_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(destination), symlinks=True, copy_function=shutil.copy2)
    shutil.copystat(str(source), str(destination))


def fsync_tree(root: Path) -> None:
    """Flush a copied tree before a rename makes it the recovery source or the active tree."""
    for directory, _, names in os.walk(str(root)):
        for name in names:
            with open(os.path.join(directory, name), "rb") as stream:
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    # Windows refuses to flush a read-only handle; there the copy is not flushed.
                    if os.name != "nt":
                        raise
        sync_directory(Path(directory))
    sync_directory(root.parent)


def begin(state: Path, journal: Path) -> None:
    current = state / "CURRENT"
    if exists(current):
        raise Blocked(f"An interrupted transaction is recorded in {current}; run `recover` first")
    write_durable(current, (str(journal.relative_to(state)) + "\n").encode("utf-8"))


def finish(state: Path) -> None:
    (state / "CURRENT").unlink()
    sync_directory(state)


def record_undo_error(operation: Operation, error: Exception) -> str:
    """Journal why an undo failed; when the journal cannot record it either, say so in the report instead.

    The same cause (a full disk, an unwritable state directory) can stop both, and the report must still
    name the original failure and `recover` (#54)."""
    operation.record["undo_error"] = str(error)
    try:
        write_json(operation.path, operation.record)
    except OSError as unrecorded:
        return f" (the journal could not record it: {unrecorded})"
    return ""


def undo_or_report(operation: Operation, failure: object) -> None:
    """Restore the origin state after failure, or record why not and report an incomplete restoration."""
    try:
        operation.undo()
    except (Failure, OSError) as error:
        raise Incomplete(f"{failure}; restoration incomplete: {error}{record_undo_error(operation, error)}. Recovery "
                         f"data kept in {operation.path.parent}; run `recover` with the same --plan or --receipt before "
                         "any other step", getattr(error, "details", None))


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
    undo_or_report(operation, failure)
    finish(state)
    raise Refused(f"{failure}; the original state was restored and verified", failure.details)


# Commands.
def command_verify(options) -> dict:
    return verify_package(load_package(Path(options.package)), Path(options.checksums))


def command_plan(options) -> dict:
    require_test_home(Locations(options.runtime, options.home))
    plan, _ = make_plan(options.runtime, options.home, None, Path(options.package), Path(options.checksums))
    if options.output:
        write_json(Path(options.output), plan)
    return plan


def command_apply(options) -> dict:
    plan = read_json(Path(options.plan), "run apply again")
    if plan.get("plan_format") != 1:
        raise Refused("Unsupported plan format")
    locations = Locations(plan["runtime"], plan["home"], plan.get("config_root"))
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check()
    with Lock(locations.state, "run apply again") as lock:
        lock.require_writable()
        fresh, package = make_plan(plan["runtime"], plan["home"], plan.get("config_root"), Path(plan["package"]["source"]),
                                   Path(options.checksums))
        changed = drift(plan, fresh)
        if changed:
            raise Refused("The package, target, duplicates or the plan's retirement preview changed since the plan; "
                          "plan again", changed)
        if fresh["interrupted"]:
            raise Blocked(f"An interrupted transaction is recorded in {fresh['interrupted']}; run `recover` first")
        requested = [os.path.abspath(path) for path in options.retire_duplicate]
        unowned = [path for path in fresh["duplicates"] if not any(same_path(path, other) for other in requested)]
        if unowned:
            raise Blocked("Other github-workflow copies are discoverable. Owner: the operator. Trigger: move each out of "
                          "the skill roots, or rerun apply with --retire-duplicate PATH to retire it under this receipt", unowned)
        unknown = [path for path in requested if not any(same_path(path, other) for other in fresh["duplicates"])]
        if unknown:
            raise Refused("--retire-duplicate names a path that is not a planned duplicate", unknown)
        return install(fresh, package, locations, boundary)


def keep_installer(package: Package, transaction: Path) -> dict:
    """Keep the verified package's installer in the transaction, so the receipt's rollback command does
    not depend on the extracted package, which the operator may delete or replace with another build."""
    listed = package.manifest["files"].get(PACKAGE_INSTALLER)
    data = package.files.get(PACKAGE_INSTALLER)
    if data is None or not isinstance(listed, dict) or listed.get("sha256") != digest(data):
        raise Refused(f"The verified package holds no {PACKAGE_INSTALLER} matching its manifest")
    # Named like the original: the maintenance boundary recognizes this installer's launchers by that name.
    path = transaction / "installer" / PACKAGE_INSTALLER
    write_durable(path, data)
    if digest(path.read_bytes()) != listed["sha256"]:
        raise Refused(f"The installer copy {path} does not match the package manifest")
    return {"path": str(path), "sha256": listed["sha256"]}


def install(plan: dict, package: Package, locations: Locations, boundary: dict) -> dict:
    # The package bytes verified by the fresh plan, not a second read of the source.
    package_files = package.skill_files()
    before = plan["before"]
    transaction = locations.state / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    journal = transaction / "journal.json"
    try:
        transaction.mkdir(parents=True)
    except PermissionError:
        # Nothing has changed yet: the lock and require_writable's probe file are the only earlier writes. That probe
        # has just created a file here, so the refusal is of folders, as a Windows access control list allows (#68).
        raise refuses_folders(locations.state, "so the installer cannot create its transaction folder in it",
                              "run apply again") from None
    begin(locations.state, journal)
    try:
        installer = keep_installer(package, transaction)
        backup = transaction / "backup" / SKILL
        if before is not None:
            copy_tree(locations.target, backup)
            fsync_tree(backup)
            if differences(before, inventory(backup)):
                raise Refused("The backup copy does not match the target inventory")
        moves = []
        for index, duplicate in enumerate(plan["duplicates"]):
            path = Path(duplicate)
            duplicate_inventory = inventory(path)
            duplicate_backup = transaction / "backup" / f"duplicate-{index}" / path.name
            copy_tree(path, duplicate_backup)
            fsync_tree(duplicate_backup)
            if differences(duplicate_inventory, inventory(duplicate_backup)):
                raise Refused(f"The backup copy of {path} does not match its inventory")
            # Where the copy physically was, so rollback can refuse a link added or retargeted since (#54).
            moves.append({"from": str(path), "to": str(transaction / "duplicates" / str(index) / path.name),
                          "inventory": duplicate_inventory, "backup": str(duplicate_backup),
                          "resolved": os.path.realpath(str(path))})
        staged = transaction / "staged" / SKILL
        stage(staged, package_files, locations.target, before or {}, plan["classification"])
        fsync_tree(staged)
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
    try:
        discovered, unreadable = discoverable(locations.roots), None
    except Blocked as error:
        # A directory made unreadable after the checks hides whether another copy exists: the renames are
        # undone as for a second copy, never left in place behind an exit 2 (#54).
        discovered, unreadable = None, error
    if unreadable or len(discovered) != 1 or not same_path(discovered[0], str(locations.target)):
        operation.record["discovered"] = discovered
        failure = unreadable or "Exactly one discoverable copy was not established"
        undo_or_report(operation, failure)
        finish(locations.state)
        if unreadable:
            raise Refused(f"{unreadable}; the original state was restored", unreadable.details)
        raise Refused(f"{failure}; the original state was restored", discovered)
    classification = plan["classification"]
    receipt_path = transaction / "receipt.json"
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
        "retirements": retirements(classification, before, backup, retired),
        "dropped_paths": sorted(name for name, kind in classification.items() if kind == "regenerable cache"),
        "preserved_paths": sorted(name for name, kind in classification.items() if kind in PRESERVED),
        "replaced_paths": sorted(name for name, kind in classification.items() if kind == "package"),
        "duplicates": [{"path": move["from"], "retired_to": move["to"], "backup": move["backup"], "inventory": move["inventory"],
                        "resolved": move["resolved"]} for move in moves],
        "legacy_commands": plan["legacy_commands"],
        "instruction_files": file_hashes(locations.instructions),
        "maintenance_boundary": boundary,
        "test_hooks": active_test_hooks(),
        "discovered_after": discovered,
        "state": "installed",
        "note": "Two renames are not an atomic or continuously available replacement; the maintenance boundary covers the gap.",
        "installer_copy": installer,
        # An argument list, not a shell string: quoting differs between PowerShell, cmd and sh. It omits
        # --maintenance-confirmed, which the operator appends once every consuming session is stopped. It
        # runs the installer copy kept with the receipt, so it outlives the extracted package.
        "rollback_command": [sys.executable, installer["path"], "rollback", "--receipt", str(receipt_path)],
    }
    write_json(receipt_path, receipt)
    operation.save("committed")
    finish(locations.state)
    return {"result": "installed", "receipt": str(receipt_path), "target": str(locations.target),
            "retired_paths": receipt["retired_paths"], "retirements": receipt["retirements"],
            "dropped_paths": receipt["dropped_paths"], "preserved_paths": receipt["preserved_paths"],
            "duplicates_retired": [move["from"] for move in moves], "rollback_command": receipt["rollback_command"]}


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
    # Kept directories, the root (`.`) included, keep their mode: deepest first, after every write into them.
    for name in sorted(before, key=lambda name: (name != ".", name.count("/")), reverse=True):
        path = staged / Path(*PurePosixPath(name).parts)
        if before[name]["type"] == "dir" and path.is_dir():
            path.chmod(before[name]["mode"])


def staging_problems(staged: dict, package_files: dict, before: dict, classification: dict) -> list:
    """Compare the staged inventory with the files and directories staging promised."""
    files = {name: {"sha256": digest(data), "bytes": len(data)} for name, data in package_files.items()}
    files.update({name: {"sha256": before[name]["sha256"], "bytes": before[name]["bytes"]}
                  for name, kind in classification.items() if kind in PRESERVED})
    directories = {name for name, kind in classification.items() if kind == "directory"} | {"."}
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
            if entry["type"] != "dir" or (before.get(name, {}).get("type") == "dir" and entry["mode"] != before[name]["mode"]):
                problems.append({"path": name, "expected": before.get(name, "dir"), "actual": entry})
        else:
            problems.append({"path": name, "expected": None, "actual": entry})
    problems += [{"path": name, "expected": "dir", "actual": None} for name in sorted(directories - set(staged))]
    return problems


def command_rollback(options) -> dict:
    given = Path(os.path.abspath(options.receipt))
    # The receipt is read before Locations.check(), so an unsearchable directory above it is diagnosed here.
    receipt = read_json(given, "run rollback again")
    if receipt.get("receipt_format") != 1:
        raise Refused("Unsupported receipt format")
    locations = Locations(receipt["runtime"], receipt["home"], receipt.get("config_root"))
    if str(locations.target) != receipt["target"] or str(locations.state) != receipt["state_root"]:
        raise Refused("The receipt target does not match its runtime locations")
    # Only the receipt inside its transaction is authoritative; a copy elsewhere must be identical.
    transaction = Path(receipt["transaction"])
    receipt_path = transaction / "receipt.json"
    if transaction.parent != locations.state:
        raise Refused("The receipt's transaction is not in the runtime's installer state directory")
    if not same_path(str(given), str(receipt_path)) and canonical(read_json(receipt_path, "run rollback again")) != canonical(receipt):
        raise Refused(f"The receipt differs from the canonical receipt {receipt_path}; roll back with that one")
    if receipt.get("state") != "installed":
        raise Refused(f"The receipt state is {receipt.get('state')!r}; only an installed receipt can be rolled back")
    if not isinstance(receipt.get("after"), dict):
        raise Refused("The receipt has no after-inventory; rollback refused")
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check("run rollback again")
    with Lock(locations.state, "run rollback again") as lock:
        lock.require_writable()
        if exists(locations.state / "CURRENT"):
            raise Blocked(f"An interrupted transaction is recorded in {locations.state / 'CURRENT'}; run `recover` first")
        # The rollback creates its work folder and rewrites the receipt in the receipt's transaction directory, which
        # a sudo run of apply can leave owned by another user (#71).
        probe_writable(transaction, "so the installer cannot record its rollback in it", "run rollback again",
                         folders=True)
        # A receipt field is untrusted input: only an integer mode is quoted back in guidance.
        root = receipt["after"].get(".")
        recorded = root.get("mode") if isinstance(root, dict) else None
        recorded = recorded if type(recorded) is int else None
        # Each retired duplicate returns to its original root, or below its nearest existing ancestor (#54).
        # The retired copy itself is not checked: its inventory records its mode, so one that lost owner
        # write no longer matches and the verified backup is restored instead.
        # The parent the duplicate returns through must be searchable as well as writable.
        restoring = []
        for duplicate in receipt["duplicates"]:
            original = Path(duplicate["path"])
            parent = searchable_part(original, "run rollback again")
            if parent == original:
                parent = original.parent
            if parent is not None:
                restoring.append((parent, f"so the retired duplicate {original} cannot be moved back into it"))
            # The copy must go back to the directory it was retired from. A link or junction added or retargeted
            # on the way since apply (inside the skill root, at a secondary root, at its config root or above)
            # would send it elsewhere; a link that still resolves to the same place, as with dotfiles, is accepted.
            retired_from = duplicate.get("resolved")
            if "resolved" in duplicate and not isinstance(retired_from, str):
                raise Refused(f"The receipt records no valid location for the retired duplicate {original}; rollback refused")
            skill_root = next((path for path in locations.roots if path in original.parents), None)
            if retired_from is None and skill_root is not None:
                # Older receipts, such as those of installer 1.0.0, do not record where the copy was. Nothing in
                # them shows where a link at its skill root or at the directory holding that root (the config
                # root for <config>/skills) pointed at apply, so such a link blocks. Discovery never follows
                # links below a root, so the copy was retired from its path below the root's resolution.
                linked = next((path for path in (skill_root.parent, skill_root)
                               if os.path.isdir(str(path.parent)) and is_link(path)), None)
                if linked is not None:
                    raise Blocked(f"{linked} is a link or junction, and this receipt predates the record of where each "
                                  f"retired duplicate was, so rollback cannot confirm that {original} would return to "
                                  f"the folder it was retired from; replace {linked} with the folder it pointed to at "
                                  "apply, then run rollback again, or, if it still points there, roll back with the "
                                  "installer that wrote this receipt", {"path": str(original), "link": str(linked)})
                retired_from = os.path.join(os.path.realpath(str(skill_root)), os.path.relpath(str(original), str(skill_root)))
            resolved = os.path.realpath(str(original))
            if isinstance(retired_from, str) and os.path.normcase(resolved) != os.path.normcase(retired_from):
                raise Blocked(f"The retired duplicate {original} would be restored to {resolved}, not {retired_from} where "
                              "it was retired from: a link or junction on the way was added or retargeted after apply; "
                              "restore that folder, then run rollback again",
                              {"path": str(original), "resolves_to": resolved, "retired_from": retired_from})
        locations.require_movable(recorded, "run rollback again", restoring)
        active = inventory(locations.target, "run rollback again")
        origin = receipt["after"]
        if (recorded is not None and not recorded & stat.S_IWUSR and isinstance(active, dict)
                and isinstance(active.get("."), dict) and active["."].get("mode") == recorded | stat.S_IWUSR):
            # An older installer could record a root without owner write, which require_movable refuses.
            # The owner write it asks for is the only accepted difference, so its one fix also passes drift.
            origin = {**receipt["after"], ".": {**root, "mode": recorded | stat.S_IWUSR}}
        changed = differences(origin, active)
        if changed:
            message = "The active tree no longer matches the receipt's after-inventory; rollback refused"
            root = next((item for item in changed if item["path"] == "."), None)
            expected, actual = (root["expected"], root["actual"]) if root else (None, None)
            if (isinstance(expected, dict) and isinstance(actual, dict) and expected.get("type") == actual.get("type")
                    and type(expected.get("mode")) is int and type(actual.get("mode")) is int):
                message += f"; restore mode {expected['mode']:04o} on {locations.target} (now {actual['mode']:04o})"
            raise Refused(message, changed)
        work = transaction / ("rollback-" + uuid.uuid4().hex[:8])
        journal = work / "journal.json"
        work.mkdir(parents=True)
        begin(locations.state, journal)
        try:
            # A verified copy of the installed tree, so an interrupted rollback can restore it.
            origin_backup = work / "origin-backup" / SKILL
            copy_tree(locations.target, origin_backup)
            fsync_tree(origin_backup)
            if differences(origin, inventory(origin_backup)):
                raise Refused("The copy of the installed tree does not match the after-inventory")
            incoming_path = None
            if receipt["before"] is not None:
                retired = Path(receipt["retired"])
                if exists(retired) and not differences(receipt["before"], inventory(retired)):
                    incoming_path = retired
                else:
                    incoming_path = work / "restore" / SKILL
                    copy_tree(Path(receipt["backup"]), incoming_path)
                    fsync_tree(incoming_path)
                    if differences(receipt["before"], inventory(incoming_path)):
                        raise Refused("Neither the retired copy nor the backup matches the before-inventory")
            moves = []
            for index, duplicate in enumerate(receipt["duplicates"]):
                source, original = Path(duplicate["retired_to"]), Path(duplicate["path"])
                expected = duplicate["inventory"]
                if exists(original):
                    raise Refused(f"The retired duplicate {original} cannot be restored: its path is occupied")
                if differences(expected, inventory(source)):
                    # The retired copy is lost or changed: restore the verified backup and leave that copy
                    # where it is, outside every skill root.
                    source = work / "restore-duplicates" / str(index) / original.name
                    copy_tree(Path(duplicate["backup"]), source)
                    fsync_tree(source)
                    if differences(expected, inventory(source)):
                        raise Refused(f"Neither the retired copy nor the backup of {original} matches its inventory")
                moves.append({"from": str(source), "to": str(original), "inventory": expected})
        except OSError as error:
            finish(locations.state)
            raise Refused(f"Preparing the rollback failed before any rename: {error}; the target is unchanged")
        except BaseException:
            finish(locations.state)
            raise
        operation = Operation(journal, {
            "operation": "rollback",
            "receipt": str(receipt_path),
            "journal": str(journal),
            "target": str(locations.target),
            "origin": origin,
            "origin_backup": str(origin_backup),
            "incoming": receipt["before"],
            "incoming_path": str(incoming_path) if incoming_path else None,
            "parked": str(work / "parked" / SKILL),
            "moves": moves,
            "moves_last": True,
        })
        attempt(operation, locations.state)
        operation.record["rollback_evidence"] = {"maintenance_boundary": boundary, "test_hooks": active_test_hooks()}
        operation.save("committed")
        mark_rolled_back(operation.record)
        finish(locations.state)
        return {"result": "rolled back", "target": str(locations.target), "restored": "absent" if receipt["before"] is None else "before-inventory"}


def mark_rolled_back(record: dict, tolerant: bool = False) -> bool:
    receipt_path = Path(record["receipt"])
    try:
        receipt = read_json(receipt_path)
    except Refused:
        if tolerant:
            return False
        raise
    receipt["state"] = "rolled back"
    receipt["rollback"] = {"journal": record["journal"], "parked": record["parked"], **record.get("rollback_evidence", {})}
    write_json(receipt_path, receipt)
    return True


def command_recover(options) -> dict:
    if options.plan or options.receipt:
        # The recorded config root, so recovery finds the state whatever the environment now says.
        recorded = read_json(Path(options.plan or options.receipt), "run recover again")
        locations = Locations(recorded["runtime"], recorded["home"], recorded.get("config_root"))
    elif options.runtime:
        locations = Locations(options.runtime, options.home)
    else:
        raise Refused("recover needs --plan, --receipt or --runtime")
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check("run recover again")
    nothing = {"result": "nothing to recover", "config_root": str(locations.config), "state_root": str(locations.state)}
    if not (options.plan or options.receipt):
        # --runtime resolves the config root from this invocation only; a transaction planned under
        # another root keeps its CURRENT pointer there, so "nothing" here is not "all clear".
        # Claude keeps its state under the config root; Codex keeps it under the home directory whatever CODEX_HOME says.
        cause = ("another config root (--home or CLAUDE_CONFIG_DIR)" if locations.runtime == "claude"
                 else "another home directory (--home)")
        nothing["hint"] = (f"Only {locations.state} was checked. A transaction started with {cause} is recovered with "
                           "`recover --plan <plan>` or `recover --receipt <receipt>`, which reuse the recorded directories.")
    if not exists(locations.state):
        return nothing
    with Lock(locations.state, "run recover again") as lock:
        current = locations.state / "CURRENT"
        if not exists(current):
            return nothing
        # Every outcome below ends by removing CURRENT, possibly after restoring the target.
        lock.require_writable()
        journal = locations.state / current.read_text(encoding="utf-8").strip()
        # exists() reads a journal in a directory that cannot be searched as absent, which would drop CURRENT (#71).
        searchable_part(journal, "run recover again")
        if not exists(journal):
            # The journal is written durably before the first rename, so nothing was renamed.
            finish(locations.state)
            return {"result": "no rename happened; staging data kept", "transaction": str(journal.parent)}
        record = read_json(journal)
        operation = Operation(journal, record)
        if record.get("state") == "committed":
            result = {"result": "already committed", "journal": str(journal)}
            if record["operation"] == "rollback" and not mark_rolled_back(record, tolerant=True):
                result["receipt_not_updated"] = record["receipt"]
            if record["operation"] == "apply":
                # The apply output may never have been printed: name the receipt and what it retired.
                receipt = journal.parent / "receipt.json"
                result["receipt"] = str(receipt)
                try:
                    value = read_json(receipt)
                except Refused as error:
                    result["receipt_unreadable"] = str(error)
                else:
                    result.update({key: value[key] for key in ("retirements", "rollback_command") if key in value})
            finish(locations.state)
            return result
        # Undo records each step in the journal, and an apply's receipt beside it, so a directory that refuses
        # them blocks before the first step (#71).
        probe_writable(journal.parent, "so the installer cannot record its recovery in it", "run recover again")
        operation.record["recovery_boundary"] = boundary
        try:
            operation.undo()
        except (Failure, OSError) as error:
            raise Incomplete(f"Restoration incomplete: {error}{record_undo_error(operation, error)}. Recovery data kept "
                             f"in {operation.path.parent}; fix the cause and run `recover` again",
                             getattr(error, "details", None))
        result = {"result": "restored", "operation": record["operation"], "journal": str(journal), "target": record["target"]}
        for key in ("parked_drift", "moved_drift"):
            if key in operation.record:
                result[key] = operation.record[key]
        # An aside path is recorded before its rename; one whose rename never happened names nothing.
        stale = [path for path in operation.record.get("stale_restore_copies", ()) if exists(Path(path))]
        if stale:
            result["stale_restore_copies"] = stale
        receipt = journal.parent / "receipt.json"
        if record["operation"] == "apply" and exists(receipt):
            value = read_json(receipt)
            value["state"] = "recovered to the before-state; not installed"
            write_json(receipt, value)
        finish(locations.state)
        return result


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
    source = recover.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", help="the plan an interrupted apply used")
    source.add_argument("--receipt", help="the receipt an interrupted rollback used")
    source.add_argument("--runtime", choices=("claude", "codex"), help="default locations, or --home and the environment")
    recover.add_argument("--home")
    recover.add_argument("--maintenance-confirmed", action="store_true")
    return top


def hook_report() -> dict:
    hooks = active_test_hooks()
    return {"test_hooks": sorted(hooks)} if hooks else {}


def main(argv=None) -> int:
    options = parser().parse_args(argv)
    handlers = {"verify-package": command_verify, "plan": command_plan, "apply": command_apply,
                "rollback": command_rollback, "recover": command_recover}
    try:
        result = handlers[options.command](options)
    except Failure as error:
        print(json.dumps({"error": str(error), "exit": error.code, "details": error.details, **hook_report()}, indent=2), file=sys.stderr)
        return error.code
    except OSError as error:
        print(json.dumps({"error": f"Unexpected filesystem error: {error}", "exit": 3, **hook_report()}, indent=2), file=sys.stderr)
        return 3
    # ASCII output: a legacy console encoding cannot fail on a non-ASCII home path.
    print(json.dumps({**result, **hook_report()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
