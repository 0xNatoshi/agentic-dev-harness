#!/usr/bin/env python3
"""Verify a dev-harness package and install, roll back or recover its github-workflow skill.

Standard library only; Python 3.8+. Exit codes: 0 done, 1 refused or failed and restored,
2 blocked before any change, 3 restoration incomplete (recovery data kept; run `recover`).
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager, ExitStack
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
import struct
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
_TRACE = None
_UNBOUND_DIRECTORY = object()
REPARSE_POINT = 0x400
# IO_REPARSE_TAG_SYMLINK and IO_REPARSE_TAG_MOUNT_POINT (junctions). Other reparse points, such as
# cloud placeholders, hold their own content.
LINK_REPARSE_TAGS = {0xA000000C, 0xA0000003}
MEMBER_LIMIT = 64 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024
PACKAGE_ENTRY_LIMIT = 1024
PACKAGE_NAME_BYTES_LIMIT = 128 * 1024
CENTRAL_DIRECTORY_LIMIT = 512 * 1024
MANIFEST_LIMIT = 1024 * 1024
MANIFEST_ENTRY_LIMIT = 1024
CHECKSUM_LIMIT = 128 * 1024


class Failure(Exception):
    code = 1

    def __init__(self, message: str, details: object = None) -> None:
        super().__init__(message)
        self.details = details


class Refused(Failure):
    code = 1


class Blocked(Failure):
    code = 2


class UntrustedState(Blocked):
    """A control path lost its native owner or mutation protection."""


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


def require_test_home(locations: "Locations", checkpoints: bool = True) -> None:
    """Honour test hooks only when every location the command can touch is under the temporary directory."""
    global _TRACE
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
    if hooks.get(TEST_TRACE) and checkpoints:
        if _TRACE is not None:
            _TRACE.close()
        _TRACE = TraceBinding(Path(os.path.abspath(hooks[TEST_TRACE])))


def checkpoint(name: str) -> None:
    if _TRACE is not None:
        _TRACE.write(name)
    if os.environ.get(TEST_CRASH) == name:
        os._exit(70)


def rename(source: Path, destination: Path, point: str, source_parent=None, destination_parent=None) -> None:
    # DEV_HARNESS_INSTALL_TEST_FAULT: comma-separated points such as `apply:activate:permission`.
    for fault in filter(None, os.environ.get(TEST_FAULT, "").split(",")):
        permission = fault.endswith(":permission")
        if (fault[: -len(":permission")] if permission else fault) == point:
            if permission:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(source))
            raise OSError(5, "Injected rename failure", str(source))
    if source_parent is not None or destination_parent is not None:
        if os.name == "nt":
            source = source_parent.path / source.name if source_parent else source
            destination = destination_parent.path / destination.name if destination_parent else destination
            os.rename(source, destination)
        else:
            os.rename(source.name if source_parent else source, destination.name if destination_parent else destination,
                      src_dir_fd=source_parent.fd if source_parent else None,
                      dst_dir_fd=destination_parent.fd if destination_parent else None)
            for parent in (source_parent, destination_parent):
                if parent is not None:
                    try:
                        os.fsync(parent.fd)
                    except OSError:
                        pass  # As with sync_directory, some filesystems do not support directory fsync.
    else:
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


def _untrusted_state(path: Path, reason: str) -> None:
    raise UntrustedState(f"Installation state at {path} is not protected: {reason}; reconcile it manually from trusted evidence")


def _mac_acl(path: Path, future_child: bool = False) -> None:
    """Inspect effective (or inheritable) mutation grants; POSIX mode bits do not mask macOS ACLs."""
    if sys.platform != "darwin":
        return
    import ctypes

    libc = ctypes.CDLL("libc.dylib", use_errno=True)
    libc.acl_get_file.argtypes, libc.acl_get_file.restype = (ctypes.c_char_p, ctypes.c_int), ctypes.c_void_p
    libc.acl_get_entry.argtypes, libc.acl_get_entry.restype = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p), ctypes.c_int
    libc.acl_get_tag_type.argtypes, libc.acl_get_tag_type.restype = (ctypes.c_void_p, ctypes.c_void_p), ctypes.c_int
    libc.acl_get_flagset_np.argtypes, libc.acl_get_flagset_np.restype = (ctypes.c_void_p, ctypes.c_void_p), ctypes.c_int
    libc.acl_get_flag_np.argtypes, libc.acl_get_flag_np.restype = (ctypes.c_void_p, ctypes.c_int), ctypes.c_int
    libc.acl_get_permset_mask_np.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    libc.acl_get_permset_mask_np.restype = ctypes.c_int
    libc.acl_get_qualifier.argtypes, libc.acl_get_qualifier.restype = (ctypes.c_void_p,), ctypes.c_void_p
    libc.acl_free.argtypes, libc.acl_free.restype = (ctypes.c_void_p,), ctypes.c_int
    libc.mbr_uuid_to_id.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    libc.mbr_uuid_to_id.restype = ctypes.c_int
    try:
        original = os.lstat(str(path))
    except OSError:
        _untrusted_state(path, "the ACL object's identity cannot be inspected")
    acl = libc.acl_get_file(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED in the macOS SDK.
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:  # No extended ACL on an existing object.
            try:
                current = os.lstat(str(path))
            except OSError:
                _untrusted_state(path, "the ACL object disappeared during inspection")
            if (original.st_dev, original.st_ino) == (current.st_dev, current.st_ino):
                return
        _untrusted_state(path, "its extended ACL cannot be inspected")
    mutation = sum(1 << bit for bit in (2, 4, 5, 6, 8, 10, 12, 13))
    try:
        entry = ctypes.c_void_p()
        selector = 0  # ACL_FIRST_ENTRY; ACL_NEXT_ENTRY is -1.
        while True:
            found = libc.acl_get_entry(acl, selector, ctypes.byref(entry))
            if found == -1 and selector == -1 and ctypes.get_errno() == errno.EINVAL:
                break  # macOS reports the end of the extended ACL this way.
            if found != 0:
                _untrusted_state(path, "its extended ACL entries cannot be inspected")
            selector = -1
            tag = ctypes.c_int()
            flags = ctypes.c_void_p()
            mask = ctypes.c_uint64()
            if (libc.acl_get_tag_type(entry, ctypes.byref(tag)) != 0
                    or libc.acl_get_flagset_np(entry, ctypes.byref(flags)) != 0
                    or libc.acl_get_permset_mask_np(entry, ctypes.byref(mask)) != 0):
                _untrusted_state(path, "its extended ACL grant cannot be inspected")
            if tag.value not in (1, 2):  # ACL_EXTENDED_ALLOW / ACL_EXTENDED_DENY.
                _untrusted_state(path, "its extended ACL has an unknown grant type")
            if tag.value == 2 or not mask.value & mutation:
                continue
            only_inherit = libc.acl_get_flag_np(flags, 1 << 8)
            file_inherit = libc.acl_get_flag_np(flags, 1 << 5)
            dir_inherit = libc.acl_get_flag_np(flags, 1 << 6)
            if min(only_inherit, file_inherit, dir_inherit) < 0:
                _untrusted_state(path, "its extended ACL inheritance cannot be inspected")
            if (future_child and not (file_inherit or dir_inherit)) or (not future_child and only_inherit):
                continue
            qualifier = libc.acl_get_qualifier(entry)
            if not qualifier:
                _untrusted_state(path, "its extended ACL principal cannot be inspected")
            try:
                principal = ctypes.c_uint()
                kind = ctypes.c_int()
                if (libc.mbr_uuid_to_id(qualifier, ctypes.byref(principal), ctypes.byref(kind)) != 0
                        or kind.value != 0 or principal.value != os.geteuid()):
                    _untrusted_state(path, "another principal has an extended ACL mutation grant")
            finally:
                libc.acl_free(qualifier)
    finally:
        libc.acl_free(acl)


def _control_prefixes(path: Path):
    return reversed((path, *path.parents))


def _posix_control_component(path: Path, role: str, status) -> None:
    uid = os.geteuid()
    if stat.S_ISLNK(status.st_mode):
        if role != "ancestor" or status.st_uid not in (0, uid):
            _untrusted_state(path, "a control component is linked or has an untrusted owner")
        return  # The protected link's physical destination is checked separately.
    if role == "file":
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_uid != uid:
            _untrusted_state(path, "a control file is not a singly linked regular file owned by this user")
    elif not stat.S_ISDIR(status.st_mode) or status.st_uid not in ((uid,) if role == "container" else (0, uid)):
        _untrusted_state(path, "a control directory or ancestor has an untrusted owner or type")
    if status.st_mode & 0o022:
        # A root-owned sticky temporary directory protects an existing current-owned child from unlink.
        if not (role == "ancestor" and status.st_uid == 0 and status.st_mode & stat.S_ISVTX):
            _untrusted_state(path, "group or other users can mutate it")
    _mac_acl(path)


def _windows_security():
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD))
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.GetSecurityInfo.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetAce.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
    advapi.GetAce.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = (wintypes.HANDLE,), wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = (ctypes.c_void_p,), ctypes.c_void_p
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p)
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    return ctypes, wintypes, advapi, kernel


def _windows_sid_text(ctypes, advapi, kernel, pointer: int) -> str:
    if not pointer:
        raise OSError("A security descriptor has no owner SID")
    value = ctypes.c_void_p()
    if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(value)
    finally:
        kernel.LocalFree(value)


def _windows_trusted_sids(ctypes, wintypes, advapi, kernel) -> set:
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        privileged = {"S-1-5-18", "S-1-5-32-544"}  # SYSTEM and local Administrators.
        principals = {}
        for kind in (1, 4):  # TokenUser and TokenOwner; an elevated token may default to Administrators.
            needed = wintypes.DWORD()
            advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
            if not needed.value or needed.value > 65536:
                raise OSError("The current token owner cannot be inspected")
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi.GetTokenInformation(token, kind, buffer, needed.value, ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            principals[kind] = _windows_sid_text(ctypes, advapi, kernel, ctypes.c_void_p.from_buffer(buffer).value)
        if principals[4] not in {principals[1], *privileged}:
            raise OSError("The token's default owner is not an admitted control owner")
        return {principals[1], *privileged}
    finally:
        kernel.CloseHandle(token)


def _windows_control_component(path: Path, role: str) -> None:
    """Inspect a native owner/DACL; create-only ancestor grants cannot replace an existing child."""
    import ctypes

    try:
        ctypes, wintypes, advapi, kernel = _windows_security()
        trusted = _windows_trusted_sids(ctypes, wintypes, advapi, kernel)
        if role == "ancestor":
            # TrustedInstaller owns protected system ancestors; control state keeps its own owner policy.
            trusted = trusted | {"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}
        status = os.lstat(str(path))
        reparse = bool(getattr(status, "st_file_attributes", 0) & REPARSE_POINT)
        if role == "file" and (not stat.S_ISREG(status.st_mode) or reparse or status.st_nlink != 1):
            _untrusted_state(path, "a control file is linked or not regular")
        if role != "file" and (not (stat.S_ISDIR(status.st_mode) or
                                    (role == "ancestor" and stat.S_ISLNK(status.st_mode)))
                               or (role == "container" and reparse)):
            _untrusted_state(path, "a control directory has an unsupported type or link")
        handle = kernel.CreateFileW(str(path), 0x00020080, 0x7, None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
            result = advapi.GetSecurityInfo(handle, 1, 0x5, ctypes.byref(owner), None,
                                            ctypes.byref(dacl), None, ctypes.byref(descriptor))
            if result:
                raise OSError(result, "GetSecurityInfo failed")
            try:
                if _windows_sid_text(ctypes, advapi, kernel, owner.value) not in trusted:
                    _untrusted_state(path, "its owner SID is not trusted for this role")
                if not dacl.value:
                    _untrusted_state(path, "its DACL is null")
                acl = ctypes.string_at(dacl, 8)
                count = struct.unpack_from("<H", acl, 4)[0]
                # Generic write/all, delete, child deletion, data/append, security, attributes and EAs.
                control_mutation = 0x50000000 | 0x000D0156
                # Ancestors may permit creation, but may not permit replacing or changing existing children.
                ancestor_replacement = 0x10000000 | 0x000D0040
                if reparse:
                    # FSCTL_SET/DELETE_REPARSE_POINT can change a link with WRITE_DATA or WRITE_ATTRIBUTES.
                    ancestor_replacement |= 0x40000102
                for index in range(count):
                    ace = ctypes.c_void_p()
                    if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    header = ctypes.string_at(ace, 4)
                    kind, flags, length = struct.unpack("<BBH", header)
                    if length < 8:
                        _untrusted_state(path, "its DACL has a malformed ACE")
                    raw = ctypes.string_at(ace, length)
                    mask = struct.unpack_from("<I", raw, 4)[0]
                    if kind == 1 or flags & 0x08:  # Deny, or inherit-only on this object.
                        continue
                    if kind != 0 and mask & (ancestor_replacement if role == "ancestor" else control_mutation):
                        _untrusted_state(path, "its DACL has an unknown mutation grant")
                    if kind != 0 or not mask & (ancestor_replacement if role == "ancestor" else control_mutation):
                        continue
                    if length < 12 or _windows_sid_text(ctypes, advapi, kernel, ace.value + 8) not in trusted:
                        _untrusted_state(path, "another principal has a DACL mutation grant")
            finally:
                kernel.LocalFree(descriptor)
        finally:
            kernel.CloseHandle(handle)
    except Blocked:
        raise
    except (OSError, ValueError):
        _untrusted_state(path, "its native owner and DACL cannot be inspected")


def require_trusted_state(state: Path, *controls: tuple, directories=()) -> None:
    """Check native ownership and replacement rights before any control document becomes authority."""
    state = Path(os.path.abspath(state))
    requested = ((state, False, False), (state / "CURRENT", False, True),
                 (state / "LOCK", False, True), *[(Path(path), required, True) for path, required in controls],
                 *[(Path(path), False, False) for path in directories])
    checked = set()

    def inspect(path: Path, role: str) -> None:
        key = (str(path), role)
        if key in checked:
            return
        status = os.lstat(str(path))
        if os.name == "nt":
            _windows_control_component(path, role)
        else:
            _posix_control_component(path, role, status)
        checked.add(key)

    for candidate, required, is_file in requested:
        candidate = Path(os.path.abspath(candidate))
        if candidate != state and state not in candidate.parents:
            _untrusted_state(candidate, "a control path leaves the selected state")
        # Follow each link's raw target component by component. realpath(candidate) alone hides
        # writable directories crossed by intermediate links or by a relative target's '..'.
        original = candidate.parts
        root_depth = len(state.parts) - 1
        pending = deque((part, "file" if is_file and index == len(original) - 1 else
                         "container" if index >= root_depth else "ancestor", False)
                        for index, part in enumerate(original[1:], 1))
        current = Path(candidate.anchor)
        try:
            inspect(current, "ancestor")
        except OSError:
            _untrusted_state(current, "its owner and permissions cannot be inspected")
        expanded_links = 0
        visited = 0
        while pending:
            visited += 1
            if visited > 4096:
                _untrusted_state(current, "a control path has too many components or link expansions")
            part, role, expanded = pending.popleft()
            if part in ("", "."):
                continue
            if part == "..":
                current = current.parent
                continue
            selected = current / part
            try:
                status = os.lstat(str(selected))
            except (FileNotFoundError, NotADirectoryError):
                if expanded:
                    _untrusted_state(selected, "a linked ancestor has no inspectable destination")
                if required and not pending:
                    _untrusted_state(selected, "the required control file is missing")
                if os.name != "nt" and exists(current):
                    _mac_acl(current, future_child=True)
                current = selected
                continue
            except OSError:
                _untrusted_state(selected, "its owner and permissions cannot be inspected")
            try:
                inspect(selected, role)
            except OSError:
                _untrusted_state(selected, "its owner and permissions cannot be inspected")
            linked = stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & REPARSE_POINT)
            if not linked:
                current = selected
                continue
            if role != "ancestor":
                _untrusted_state(selected, "a control file or directory is linked")
            expanded_links += 1
            if expanded_links > 40:
                _untrusted_state(selected, "a control path has too many linked ancestors")
            try:
                target = Path(os.readlink(str(selected)))
            except OSError:
                _untrusted_state(selected, "a linked ancestor's destination cannot be inspected")
            if target.is_absolute():
                current = Path(target.anchor)
                try:
                    inspect(current, "ancestor")
                except OSError:
                    _untrusted_state(current, "its owner and permissions cannot be inspected")
                components = target.parts[1:]
            elif target.anchor or target.drive:
                _untrusted_state(selected, "a linked ancestor has an ambiguous destination")
            else:
                current = selected.parent
                components = target.parts
            pending.extendleft((component, "ancestor", True) for component in reversed(components))


def _windows_private_attributes():
    ctypes, wintypes, advapi, kernel = _windows_security()
    try:
        principals = _windows_trusted_sids(ctypes, wintypes, advapi, kernel)
    except OSError:
        _untrusted_state(Path("."), "the current Windows token cannot be inspected")
    sddl = "D:P" + "".join(f"(A;OICI;FA;;;{sid})" for sid in sorted(principals))
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        _untrusted_state(Path("."), "a private Windows security descriptor cannot be created")

    class SecurityAttributes(ctypes.Structure):
        _fields_ = (("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL))

    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
    return ctypes, kernel, descriptor, attributes


def _private_mkdir(path: Path) -> None:
    if os.name == "nt":
        ctypes, kernel, descriptor, attributes = _windows_private_attributes()
        try:
            if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)
    else:
        old_umask = os.umask(0o077)
        try:
            os.mkdir(str(path), 0o700)
        finally:
            os.umask(old_umask)


def private_mkdirs(path: Path, state: Path) -> None:
    missing = [part for part in _control_prefixes(path) if not exists(part)]
    for part in missing:
        internal = part == state or state in part.parents
        require_trusted_state(state, directories=(part,) if internal else ())
        if os.name != "nt":
            _mac_acl(part.parent, future_child=True)
        try:
            _private_mkdir(part)
        except OSError as error:
            if not isinstance(error, FileExistsError) and getattr(error, "winerror", None) not in (80, 183):
                raise
            # A concurrent installer may have created the directory first. Accept only its
            # freshly inspected protected directory; the normal Lock still serializes the work.
        require_trusted_state(state, directories=(part,) if internal else ())


def _private_file(path: Path):
    if os.name == "nt":
        import msvcrt

        ctypes, kernel, descriptor, attributes = _windows_private_attributes()
        try:
            handle = kernel.CreateFileW(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                descriptor_number = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
            return os.fdopen(descriptor_number, "wb")
        finally:
            kernel.LocalFree(descriptor)
    old_umask = os.umask(0o077)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally:
        os.umask(old_umask)
    return os.fdopen(descriptor, "wb")


def write_durable(path: Path, data: bytes, state: Path | None = None) -> None:
    if state is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        require_trusted_state(state, (path, False))
        private_mkdirs(path.parent, state)
        _mac_acl(path.parent, future_child=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with (_private_file(temporary) if state is not None else open(temporary, "wb")) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if state is not None:
        require_trusted_state(state, (temporary, True))
    os.replace(temporary, path)
    sync_directory(path.parent)
    if state is not None:
        require_trusted_state(state, (path, True))


def write_json(path: Path, value: object, state: Path | None = None) -> None:
    write_durable(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"), state)


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


def read_control_json(path: Path, state: Path, retry: str | None = None) -> dict:
    require_trusted_state(state, (path, False))
    return read_json(path, retry)


# Filesystem inventory.
def is_link(path: Path) -> bool:
    try:
        status = os.lstat(str(path))
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(status.st_mode):
        return True
    if getattr(status, "st_file_attributes", 0) & REPARSE_POINT:
        # Without a readable tag, the reparse point is treated as a link.
        tag = getattr(status, "st_reparse_tag", None)
        return tag is None or tag in LINK_REPARSE_TAGS
    return False


def exists(path: Path) -> bool:
    return os.path.lexists(str(path))


def inaccessible(path: Path, action: str, retry: str = "plan again") -> Blocked:
    """A directory the OS refused to search or list, named with the fix instead of an error on one of its children (#54).

    Read and search permission are both named: either alone leaves the directory uninspectable. On POSIX the
    fix follows the owner and mode: a directory another user owns (one created with sudo) or one whose owner
    already has both permissions (an ACL or a macOS privacy control) is not fixed by the owner's mode."""
    try:
        status = os.stat(str(path))
    except OSError:
        status = None
    mode = f"{stat.S_IMODE(status.st_mode):04o}" if status else None
    shown = f" (mode {mode})" if mode else ""
    fix = f"add owner read and search (execute) permission to {path}"
    if status and os.name != "nt":
        if status.st_uid != os.geteuid():
            fix = (f"{path} belongs to another user (uid {status.st_uid}): have its owner or an administrator give you "
                   "read and search (execute) access to it, or its ownership")
        elif status.st_mode & (stat.S_IRUSR | stat.S_IXUSR) == stat.S_IRUSR | stat.S_IXUSR:
            fix = (f"its owner already has read and search permission, so an access control list or system privacy "
                   f"setting denies access: allow this process to read and search {path}")
    return Blocked(f"{path} cannot be {action}{shown}, so the installer cannot inspect what it holds; {fix}, then {retry}",
                   {"path": str(path), "mode": mode})


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
        if len(manifest_bytes) > MANIFEST_LIMIT:
            raise Refused(f"MANIFEST.json exceeds {MANIFEST_LIMIT} bytes")
        self.source = source
        self.files = files
        self.manifest_bytes = manifest_bytes
        self.archive_sha256 = digest(archive) if archive is not None else None
        try:
            self.manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (ValueError, RecursionError) as error:
            raise Refused(f"MANIFEST.json is not valid JSON: {error}")
        if not isinstance(self.manifest, dict) or self.manifest.get("format_version") != 2 or not isinstance(self.manifest.get("files"), dict):
            raise Refused("MANIFEST.json is not a format 2 dev-harness manifest")
        if len(self.manifest["files"]) > MANIFEST_ENTRY_LIMIT:
            raise Refused(f"MANIFEST.json file count exceeds {MANIFEST_ENTRY_LIMIT}")
        # Manifest-only paths also need a budget before collision checks expand their parents.
        try:
            check_package_paths(("MANIFEST.json", *self.manifest["files"]), "MANIFEST.json")
        except ValueError as error:
            raise Refused("Package verification failed", [str(error)]) from None
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
    files = load_directory_files(source)
    if "MANIFEST.json" not in files:
        raise Refused(f"No MANIFEST.json in {source}")
    manifest = files.pop("MANIFEST.json")
    return Package(source, files, manifest, None)


def check_package_entries(count: int, name_bytes: int, context: str) -> None:
    if count > PACKAGE_ENTRY_LIMIT:
        raise Refused(f"{context} entry count exceeds {PACKAGE_ENTRY_LIMIT}")
    if name_bytes > PACKAGE_NAME_BYTES_LIMIT:
        raise Refused(f"{context} filename bytes exceed {PACKAGE_NAME_BYTES_LIMIT}")


def check_package_paths(names, context: str) -> None:
    """Apply entry and pathname budgets to files and their implied folders."""
    directories = set()
    count = name_bytes = 0
    for name in names:
        path = package_path(name)
        if len(path.parts) > PACKAGE_ENTRY_LIMIT:
            raise Refused(f"{context} entry count exceeds {PACKAGE_ENTRY_LIMIT}")
        count += 1
        try:
            name_bytes += len(name.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Package paths must be UTF-8") from None
        check_package_entries(count, name_bytes, context)
        for parent in path.parents:
            if parent == PurePosixPath("."):
                continue
            directory = parent.as_posix()
            if directory not in directories:
                directories.add(directory)
                count += 1
                name_bytes += len(directory.encode("utf-8"))
                check_package_entries(count, name_bytes, context)


def load_directory_files(source: Path) -> dict:
    """Read a package directory once, checking each entry before retaining its bytes."""
    files = {}
    pending = [(source, "")]
    count = name_bytes = total = 0
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(str(directory)) as listing:
            for item in listing:
                name = prefix + item.name
                path = directory / item.name
                count += 1
                try:
                    name_bytes += len(name.encode("utf-8"))
                except UnicodeEncodeError:
                    raise Refused("Package directory path is not UTF-8") from None
                check_package_entries(count, name_bytes, "package directory")
                if is_link(path):
                    raise Refused(f"Package directory: Link or junction inside the tree: {path}")
                status = os.lstat(str(path))
                if stat.S_ISDIR(status.st_mode):
                    pending.append((path, name + "/"))
                    continue
                if not stat.S_ISREG(status.st_mode):
                    raise Refused(f"Package directory: Special file inside the tree: {path}")
                try:
                    package_path(name)
                except ValueError as error:
                    raise Refused(str(error))
                if name == "MANIFEST.json" and status.st_size > MANIFEST_LIMIT:
                    raise Refused(f"MANIFEST.json exceeds {MANIFEST_LIMIT} bytes")
                if status.st_size > MEMBER_LIMIT:
                    raise Refused(f"package directory member exceeds {MEMBER_LIMIT} bytes: {name}")
                if total + status.st_size > ARCHIVE_LIMIT:
                    raise Refused(f"package directory expands beyond {ARCHIVE_LIMIT} bytes")
                remaining = ARCHIVE_LIMIT - total
                bound = min(MEMBER_LIMIT, remaining, MANIFEST_LIMIT if name == "MANIFEST.json" else MEMBER_LIMIT)
                with path.open("rb") as stream:
                    data = stream.read(bound + 1)
                if name == "MANIFEST.json" and len(data) > MANIFEST_LIMIT:
                    raise Refused(f"MANIFEST.json exceeds {MANIFEST_LIMIT} bytes")
                if len(data) > MEMBER_LIMIT:
                    raise Refused(f"package directory member exceeds {MEMBER_LIMIT} bytes: {name}")
                total += len(data)
                if total > ARCHIVE_LIMIT:
                    raise Refused(f"package directory expands beyond {ARCHIVE_LIMIT} bytes")
                files[name] = data
    return files


def preflight_archive_metadata(archive: bytes) -> int:
    """Bound the raw central directory before ZipFile allocates ZipInfo objects."""
    eocd = archive.rfind(b"PK\x05\x06", max(0, len(archive) - 65557))
    if eocd < 0 or eocd + 22 > len(archive):
        raise Refused("Not a valid ZIP archive: missing end record")
    _, disk, directory_disk, disk_entries, declared, size, offset, comment_size = struct.unpack_from(
        "<4s4H2LH", archive, eocd)
    if eocd + 22 + comment_size != len(archive):
        raise Refused("Not a valid ZIP archive: invalid end record length")
    # ZipFile honors this locator even when the classic record advertises zero members.
    if eocd >= 20 and archive[eocd - 20:eocd - 16] == b"PK\x06\x07":
        raise Refused("ZIP64 central directory is outside package limits")
    if disk or directory_disk or disk_entries != declared:
        raise Refused("Unsupported multi-disk ZIP archive")
    if declared == 0xffff or size == 0xffffffff or offset == 0xffffffff:
        raise Refused("ZIP64 central directory is outside package limits")
    check_package_entries(declared, 0, "archive")
    if size > CENTRAL_DIRECTORY_LIMIT:
        raise Refused(f"archive central directory exceeds {CENTRAL_DIRECTORY_LIMIT} bytes")
    start = eocd - size
    if start < 0 or offset > start:
        raise Refused("Not a valid ZIP archive: invalid central directory offset")
    position = start
    count = 0
    while position < eocd:
        if position + 46 > eocd or archive[position:position + 4] != b"PK\x01\x02":
            raise Refused("Not a valid ZIP archive: invalid central directory entry")
        method = struct.unpack_from("<H", archive, position + 10)[0]
        if method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise Refused(f"Unsupported ZIP compression method: {method}")
        filename_size, extra_size, entry_comment_size = struct.unpack_from("<3H", archive, position + 28)
        position += 46 + filename_size + extra_size + entry_comment_size
        if position > eocd:
            raise Refused("Not a valid ZIP archive: truncated central directory entry")
        count += 1
        # All raw filename bytes are already inside the capped central-directory region.
        check_package_entries(count, 0, "archive")
    if count != declared:
        raise Refused("archive central directory count differs from its end record")
    return count


def load_archive(source: Path) -> Package:
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise Refused(f"The archive is not a regular file: {source}")
        if opened.st_size > ARCHIVE_LIMIT:
            raise Refused(f"The archive exceeds {ARCHIVE_LIMIT} bytes")
        archive = stream.read(opened.st_size + 1)
        if len(archive) != opened.st_size or stream.read(1):
            raise Refused("The archive changed while being read")
    expected_members = preflight_archive_metadata(archive)
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != expected_members:
                raise Refused("archive central directory count differs after ZIP parsing")
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
            try:
                check_package_paths((member.filename.split("/", 1)[1] for member in members), "archive")
            except ValueError as error:
                raise Refused(str(error)) from None
            for member in members:
                name = member.filename.split("/", 1)[1]
                if name == "MANIFEST.json" and member.file_size > MANIFEST_LIMIT:
                    raise Refused(f"MANIFEST.json exceeds {MANIFEST_LIMIT} bytes")
                if member.file_size > MEMBER_LIMIT or total + member.file_size > ARCHIVE_LIMIT:
                    raise Refused(f"The archive expands beyond the size limits at {member.filename}")
                # Bounded reads: the declared size of a member is not trusted.
                remaining = ARCHIVE_LIMIT - total
                bound = min(MEMBER_LIMIT, remaining, MANIFEST_LIMIT if name == "MANIFEST.json" else MEMBER_LIMIT)
                with bundle.open(member) as stream:
                    data = stream.read(bound + 1)
                if name == "MANIFEST.json" and len(data) > MANIFEST_LIMIT:
                    raise Refused(f"MANIFEST.json exceeds {MANIFEST_LIMIT} bytes")
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


def read_checksums(path: Path) -> tuple[dict, bytes]:
    entries = {}
    try:
        # A path or link can change after this open; parse and record only these captured bytes.
        with Path(path).open("rb") as stream:
            data = stream.read(CHECKSUM_LIMIT + 1)
        if len(data) > CHECKSUM_LIMIT:
            raise Refused(f"Checksums exceed {CHECKSUM_LIMIT} bytes: {path}")
        lines = data.decode("utf-8").splitlines()
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
    return entries, data


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
    sums, checksum_bytes = read_checksums(checksums)
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
        "checksums_sha256": digest(checksum_bytes),
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
    duplicate_inventories, duplicate_physical = {}, {}
    for path in duplicates:
        with PhysicalDirectory(Path(path).parent, writable=False) as parent:
            duplicate_inventories[path] = parent.inventory(Path(path).name)
            duplicate_physical[path] = parent.describe()
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
                                      "give each retired file's retired_copy and backup location. The historical "
                                      "rollback_command names a retained installer copy; use independently verified "
                                      "installer code for rollback."},
        "duplicates": duplicates,
        "duplicate_inventories": duplicate_inventories,
        "duplicate_physical": duplicate_physical,
        "legacy_commands": [str(path) for path in locations.legacy if exists(path)],
        "interrupted": str(locations.state / "CURRENT") if exists(locations.state / "CURRENT") else None,
    }
    return plan, package


DRIFT_KEYS = ("installer_version", "runtime", "home", "config_root", "target", "skill_roots", "state_root", "package", "retirement_list",
              "before", "classification", "retirements", "retirement_copies", "duplicates", "duplicate_inventories", "duplicate_physical")


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

    def __init__(self, state: Path) -> None:
        require_trusted_state(state)
        private_mkdirs(state, state)
        lock = state / "LOCK"
        require_trusted_state(state)
        if not exists(lock):
            _mac_acl(state, future_child=True)
            try:
                with _private_file(lock):
                    pass
            except OSError as error:
                if not isinstance(error, FileExistsError) and getattr(error, "winerror", None) not in (80, 183):
                    raise
        require_trusted_state(state, (lock, True))
        self.state = state
        self.stream = open(lock, "r+b")
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
        try:
            require_trusted_state(self.state, (self.state / "LOCK", True))
        except BaseException:
            self.__exit__(None, None, None)
            raise
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


class PhysicalDirectory:
    """An opened physical parent for duplicate moves, never a reusable alias pathname.

    POSIX renames use dir_fd. Windows captures no-reparse relative handles and holds
    no-delete-shared directories plus an auto-deleted guard child during mutations.
    This blocks replacement and in-place junction conversion while the handles live.
    Persisted identities prevent a later recovery from accepting a replacement directory.
    """
    def __init__(self, path: Path, expected=_UNBOUND_DIRECTORY, writable: bool = True) -> None:
        self.fd = None
        self.handles = []
        resolved = os.path.realpath(str(path))
        if expected is not _UNBOUND_DIRECTORY:
            identity = expected.get("identity") if isinstance(expected, dict) else None
            recorded = expected.get("path") if isinstance(expected, dict) else None
            if (not isinstance(recorded, str) or not os.path.isabs(recorded)
                    or not isinstance(identity, list) or len(identity) != 2
                    or any(type(value) is not int or value < 0 for value in identity)):
                raise Refused("Invalid physical duplicate-parent binding")
            if os.path.normcase(resolved) != os.path.normcase(recorded):
                raise Blocked(f"The duplicate parent {path} now resolves elsewhere; restore its recorded folder and retry")
            resolved = recorded
        self.path = Path(resolved)
        try:
            if os.name == "nt":
                self._open_windows()
                identity = self._windows_identity()
            else:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                self.fd = os.open(self.path.anchor, flags)
                for component in self.path.parts[1:]:
                    next_fd = os.open(component, flags, dir_fd=self.fd)
                    os.close(self.fd)
                    self.fd = next_fd
                status = os.fstat(self.fd)
                identity = [status.st_dev, status.st_ino]
            self.identity = identity
            if not identity[1]:
                raise Blocked(f"The filesystem does not expose a stable identity for {self.path}")
            if expected is not _UNBOUND_DIRECTORY and self.identity != expected["identity"]:
                raise Blocked(f"The duplicate parent {path} was replaced; restore its recorded folder and retry")
            if os.name == "nt" and writable:
                self._protect_windows()
        except BaseException:
            self.close()
            raise

    def _open_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.kernel = kernel

        # RootDirectory-relative opens with OBJ_DONT_REPARSE avoid resolving any replaced
        # pathname during capture and guard creation. Ordinary Win32 opens have no equivalent.
        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", wintypes.LPWSTR)]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                        ("name", ctypes.POINTER(UnicodeString)), ("attributes", wintypes.ULONG),
                        ("security", wintypes.LPVOID), ("quality", wintypes.LPVOID)]

        class StatusBlock(ctypes.Structure):
            _fields_ = [("status", wintypes.LPVOID), ("information", ctypes.c_size_t)]

        native = ctypes.WinDLL("ntdll")
        native.NtCreateFile.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                                       ctypes.POINTER(ObjectAttributes), ctypes.POINTER(StatusBlock),
                                       wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                                       wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG)
        native.NtCreateFile.restype = wintypes.LONG
        native.RtlNtStatusToDosError.argtypes = (wintypes.LONG,)
        native.RtlNtStatusToDosError.restype = wintypes.ULONG

        def open_relative(parent, name, access, disposition=1, options=1, sharing=3):
            if not name or name in (".", "..") or any(part in name for part in ("/", "\\", ":")):
                raise Blocked("A physical directory operation requires one ordinary filename")
            buffer = ctypes.create_unicode_buffer(name)
            size = len(name.encode("utf-16-le"))
            counted = UnicodeString(size, size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(counted),
                                          0x40 | 0x1000, None, None)  # CASE_INSENSITIVE | DONT_REPARSE
            status, handle = StatusBlock(), wintypes.HANDLE()
            result = native.NtCreateFile(ctypes.byref(handle), access | 0x100000, ctypes.byref(attributes),
                                         ctypes.byref(status), None, 0, sharing, disposition,
                                         options | 0x20 | 0x200000, None, 0)
            if result < 0:
                raise ctypes.WinError(native.RtlNtStatusToDosError(result))
            return handle.value

        self.open_relative = open_relative
        # Read/list access makes the no-delete sharing barrier effective. Allowing writes
        # is necessary for ordinary child renames and transaction-journal replacement.
        root = kernel.CreateFileW(self.path.anchor, 0x81, 3, None, 3, 0x02000000 | 0x00200000, None)
        if root == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(root)
        self._windows_directory(root)
        for component in self.path.parts[1:]:
            handle = open_relative(self.handles[-1], component, 0x81)
            self.handles.append(handle)
            self._windows_directory(handle)
        self.directory_handle = self.handles[-1]

    def _protect_windows(self) -> None:
        # Windows permits converting an open *empty* directory into a junction. A
        # no-delete-shared child makes the final parent nonempty until handles close;
        # each ancestor likewise retains its opened direct child. NTFS rejects reparse
        # conversion of nonempty directories. DELETE_ON_CLOSE also covers abrupt exit.
        guard = self.open_relative(self.directory_handle, ".harness-guard-" + uuid.uuid4().hex,
                                   0x10082, disposition=2, options=0x40 | 0x1000, sharing=1)
        self.handles.append(guard)
        self._windows_directory(self.directory_handle)

    def _windows_attributes(self, handle) -> tuple:
        import ctypes
        from ctypes import wintypes

        class Attributes(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

        attributes = Attributes()
        if not self.kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        return attributes.attributes, attributes.tag

    def _windows_directory(self, handle) -> None:
        attributes, tag = self._windows_attributes(handle)
        # Every name-surrogate tag redirects a namespace lookup, including tags other
        # than the ordinary symlink/junction tags that discovery already recognizes.
        if not attributes & 0x10 or tag & 0x20000000:
            raise Blocked("A canonical duplicate parent is not an ordinary physical directory")

    def _windows_identity(self, handle=None) -> list:
        import ctypes

        class FileIdentity(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]

        information = FileIdentity()
        # FileIdInfo retains the full 128-bit identifier, including on ReFS where
        # the older 64-bit BY_HANDLE_FILE_INFORMATION file index is not unique.
        if not self.kernel.GetFileInformationByHandleEx(self.directory_handle if handle is None else handle,
                                                       18, ctypes.byref(information),
                                                       ctypes.sizeof(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        return [information.volume, int.from_bytes(bytes(information.identifier), "little")]

    def describe(self) -> dict:
        return {"path": str(self.path), "identity": self.identity}

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        for handle in reversed(self.handles):
            self.kernel.CloseHandle(handle)
        self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def exists(self, name: str) -> bool:
        if self.fd is None:
            return exists(self.path / name)
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def inventory(self, name: str) -> dict | None:
        if self.fd is None:
            try:
                child = self.open_relative(self.directory_handle, name, 0x81)
            except FileNotFoundError:
                return None
            try:
                self._windows_directory(child)
                # Pin the direct child while inspecting the duplicate. This stabilizes
                # its parent namespace; inventories still reject links within the tree.
                return inventory(self.path / name)
            finally:
                self.kernel.CloseHandle(child)
        if not self.exists(name):
            return None
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        entries = {}

        def visit(parent: int, child: str, relative: str) -> None:
            descriptor = os.open(child, flags, dir_fd=parent)
            try:
                entries[relative or "."] = {"type": "dir", "mode": stat.S_IMODE(os.fstat(descriptor).st_mode)}
                for child_name in os.listdir(descriptor):
                    key = relative + "/" + child_name if relative else child_name
                    status = os.stat(child_name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISDIR(status.st_mode):
                        visit(descriptor, child_name, key)
                    elif stat.S_ISREG(status.st_mode):
                        child_fd = os.open(child_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                        with os.fdopen(child_fd, "rb") as stream:
                            opened = os.fstat(stream.fileno())
                            if not stat.S_ISREG(opened.st_mode):
                                raise Blocked("A duplicate inventory entry changed type")
                            data = stream.read()
                        entries[key] = {"type": "file", "mode": stat.S_IMODE(opened.st_mode),
                                        "sha256": digest(data), "bytes": len(data)}
                    else:
                        raise Blocked(f"Link or special file inside the duplicate: {self.path / name / key}")
            finally:
                os.close(descriptor)

        visit(self.fd, name, "")
        return dict(sorted(entries.items()))


class TraceBinding:
    """Keep the validated temporary parent and lazily open one trace file per command.

    Opening a FIFO is intentionally delayed until the first checkpoint, after staging.
    The opened leaf is checked before the first byte and never reopened by pathname.
    """
    def __init__(self, path: Path) -> None:
        self.descriptor = None
        self.validation_descriptor = None
        self.parent = PhysicalDirectory(path.parent, writable=False)
        self.name = path.name
        try:
            temporary = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
            physical = os.path.normcase(str(self.parent.path))
            if os.path.commonpath([temporary, physical]) != temporary:
                raise Blocked("The trace parent is outside the validated temporary directory")
            self.before, self.before_identity = None, None
            if self.parent.fd is None:
                self.parent._protect_windows()
                if self.parent.exists(self.name):
                    self.validation_descriptor, self.before_identity = self._windows_file(0x80, 1, 7, os.O_RDONLY)
                    self.before = os.fstat(self.validation_descriptor)
                    # Keep the validation handle until append opens: a deleted file's
                    # ID cannot be recycled while its original handle remains live.
            else:
                self.before = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False) if self.parent.exists(self.name) else None
                if self.before is not None:
                    self.before_identity = [self.before.st_dev, self.before.st_ino]
            if self.before is not None and not self._allowed(self.before):
                raise Blocked("The trace must be a single-link regular file or a POSIX FIFO, never a link or shared file")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _allowed(status) -> bool:
        return status.st_nlink == 1 and (stat.S_ISREG(status.st_mode) or (os.name != "nt" and stat.S_ISFIFO(status.st_mode)))

    def _windows_file(self, access, disposition, sharing, flags):
        import msvcrt

        handle = self.parent.open_relative(self.parent.directory_handle, self.name, access,
                                           disposition=disposition, options=0x40, sharing=sharing)
        try:
            _, tag = self.parent._windows_attributes(handle)
            if tag & 0x20000000:
                raise Blocked("The trace file is a name-surrogate reparse point")
            identity = self.parent._windows_identity(handle)
            return msvcrt.open_osfhandle(handle, flags), identity
        except BaseException:
            self.parent.kernel.CloseHandle(handle)
            raise

    def _open(self) -> None:
        if os.name == "nt":
            descriptor, identity = self._windows_file(0x84, 1 if self.before else 2, 3,
                                                      os.O_WRONLY | os.O_APPEND | os.O_BINARY)
        else:
            flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
            if self.before is None:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(self.name, flags, 0o600, dir_fd=self.parent.fd)
        try:
            status = os.fstat(descriptor)
            if os.name != "nt":
                identity = [status.st_dev, status.st_ino]
            if not self._allowed(status) or (self.before is not None and identity != self.before_identity):
                raise Blocked("The trace file changed after temporary-path validation")
            if self.validation_descriptor is not None:
                os.close(self.validation_descriptor)
                self.validation_descriptor = None
            self.descriptor = descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def write(self, name: str) -> None:
        if self.descriptor is None:
            self._open()
        if os.fstat(self.descriptor).st_nlink > 1:
            raise Blocked("The trace file acquired another hard link")
        data = (name + "\n").encode("utf-8")
        if os.write(self.descriptor, data) != len(data):
            raise OSError(errno.EIO, "Incomplete trace checkpoint write")

    def close(self) -> None:
        descriptor, self.descriptor = self.descriptor, None
        validation, self.validation_descriptor = self.validation_descriptor, None
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                if validation is not None:
                    os.close(validation)
            finally:
                self.parent.close()
# Journal and receipt paths are data until they match the locations this installer generates.
TRANSACTION_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z")
ROLLBACK_NAME = re.compile(r"rollback-[0-9a-f]{8}\Z")
RESTORE_STALE_NAME = re.compile(r"restore-stale-[0-9a-f]{8}\Z")


def plain_transaction_path(state: Path, candidate: Path) -> None:
    """Reject links or junctions in generated transaction components below the selected state."""
    try:
        relative = candidate.relative_to(state)
    except ValueError:
        raise Refused("A recorded transaction path leaves the selected state") from None
    if not relative.parts:
        raise Refused("A recorded transaction path names the state root")
    path = state
    for part in relative.parts:
        path /= part
        if is_link(path):
            raise Refused(f"A recorded transaction path crosses a link or junction at {path}")


def recorded_path(value: object, expected: Path, field: str, state: Path | None = None) -> None:
    if not isinstance(value, str) or value != str(expected):
        raise Refused(f"The recorded {field} does not match the selected transaction")
    if state is not None:
        plain_transaction_path(state, expected)


def normalized_absolute(value: object, field: str) -> Path:
    if (not isinstance(value, str) or not value or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
            or not os.path.isabs(value) or os.path.normpath(value) != value):
        raise Refused(f"The recorded {field} is not an absolute normalized path")
    return Path(value)


def recorded_physical(value: object, field: str) -> None:
    """Validate a persisted parent identity without binding it to today's pathname."""
    if not isinstance(value, dict) or set(value) != {"path", "identity"}:
        raise Refused(f"Invalid physical {field} binding")
    normalized_absolute(value["path"], f"physical {field} path")
    identity = value["identity"]
    if (not isinstance(identity, list) or len(identity) != 2
            or any(type(part) is not int or part < 0 for part in identity) or not identity[1]):
        raise Refused(f"Invalid physical {field} binding")


def recorded_move_physical(move: dict, external: str, field: str) -> None:
    if "physical" not in move:
        return  # Historical records are parsed, then refused at the physical move boundary.
    bindings = move["physical"]
    if not isinstance(bindings, dict) or set(bindings) - {"from", "to"}:
        raise Refused(f"Invalid physical {field} bindings")
    for side, binding in bindings.items():
        recorded_physical(binding, f"{field} {side}")
    if external not in bindings:
        raise Blocked("The journal lacks the original duplicate-parent identity; automatic recovery is refused. "
                      "Keep its backups and use manual restoration after verifying the original folder")


def selected_locations(record: dict, locations: Locations) -> None:
    for key, expected in (("runtime", locations.runtime), ("home", str(locations.home)),
                          ("target", str(locations.target)), ("state_root", str(locations.state))):
        if record.get(key) != expected:
            raise Refused(f"The recorded {key} does not match the selected runtime")
    if "config_root" in record and record["config_root"] != str(locations.config):
        raise Refused("The recorded config_root does not match the selected runtime")
    if "skill_roots" in record and record["skill_roots"] != [str(root) for root in locations.roots]:
        raise Refused("The recorded skill_roots do not match the selected runtime")


def locations_from_record(record: dict) -> Locations:
    if record.get("runtime") not in ("claude", "codex"):
        raise Refused("The selected record has no valid runtime")
    normalized_absolute(record.get("home"), "home")
    config = record.get("config_root")
    if config is not None:
        normalized_absolute(config, "config_root")
    locations = Locations(record["runtime"], record["home"], config)
    selected_locations(record, locations)
    return locations


def transaction_path(locations: Locations, value: object) -> Path:
    candidate = normalized_absolute(value, "transaction")
    if (candidate.parent != locations.state or not TRANSACTION_NAME.fullmatch(candidate.name)
            or is_link(candidate)):
        raise Refused("The recorded transaction is not a generated directory in the selected state")
    return candidate


def journal_kind(locations: Locations, journal: Path) -> tuple:
    if journal.name != "journal.json" or is_link(journal):
        raise Refused("The selected journal is not a regular generated journal path")
    if journal.parent.parent == locations.state:
        transaction = transaction_path(locations, str(journal.parent))
        plain_transaction_path(locations.state, journal)
        return transaction, "apply"
    work = journal.parent
    transaction = transaction_path(locations, str(work.parent))
    if not ROLLBACK_NAME.fullmatch(work.name) or is_link(work):
        raise Refused("The selected rollback journal is not in a generated work directory")
    plain_transaction_path(locations.state, journal)
    return transaction, "rollback"


def duplicate_path(locations: Locations, value: object) -> Path:
    candidate = normalized_absolute(value, "duplicate path")
    if candidate == locations.target:
        raise Refused("The recorded duplicate names the selected target")
    for root in locations.roots:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if relative.parts and candidate not in locations.target.parents and locations.target not in candidate.parents:
            return candidate
    raise Refused("The recorded duplicate is not a leaf below a selected skill root")


def require_discovered_duplicate_path(locations: Locations, original: Path, recovery: bool = False) -> None:
    """Discovery never follows a link inside a skill root, even when metadata names one."""
    root = next(path for path in locations.roots if path in original.parents)
    component = root
    for part in original.relative_to(root).parts:
        component /= part
        try:
            linked = is_link(component)
        except PermissionError:
            raise inaccessible(component.parent, "searched", "run recover again" if recovery else "run rollback again") from None
        if linked:
            raise Refused(f"The recorded duplicate crosses a link or junction below its skill root at {component}")


def verify_duplicate_restoration_path(locations: Locations, original: Path, entry: dict, recovery: bool = False) -> None:
    """Keep a retired duplicate bound to its recorded physical location across a restart."""
    retired_from = entry.get("resolved")
    if "resolved" in entry and not isinstance(retired_from, str):
        source = "journal" if recovery else "receipt"
        raise Refused(f"The {source} records no valid location for the retired duplicate {original}; "
                      f"{'recovery' if recovery else 'rollback'} refused")
    skill_root = next((path for path in locations.roots if path in original.parents), None)
    if retired_from is None and skill_root is not None:
        # Legacy records do not identify a linked root's original destination. Discovery did not
        # follow links below the root, so the root-relative path is otherwise unambiguous.
        linked = next((path for path in (skill_root.parent, skill_root)
                       if os.path.isdir(str(path.parent)) and is_link(path)), None)
        if linked is not None:
            if recovery:
                raise Blocked(f"{linked} is a link or junction, and this journal predates the record of where each "
                              f"retired duplicate was, so recovery cannot confirm where {original} belongs; "
                              f"restore the original folder, then run recover again",
                              {"path": str(original), "link": str(linked)})
            raise Blocked(f"{linked} is a link or junction, and this receipt predates the record of where each "
                          f"retired duplicate was, so rollback cannot confirm that {original} would return to "
                          f"the folder it was retired from; replace {linked} with the folder it pointed to at "
                          "apply, then run rollback again, or, if it still points there, roll back with the "
                          "installer that wrote this receipt", {"path": str(original), "link": str(linked)})
        retired_from = os.path.join(os.path.realpath(str(skill_root)), os.path.relpath(str(original), str(skill_root)))
    resolved = os.path.realpath(str(original))
    if isinstance(retired_from, str) and os.path.normcase(resolved) != os.path.normcase(retired_from):
        action = "recover" if recovery else "rollback"
        raise Blocked(f"The retired duplicate {original} would be restored to {resolved}, not {retired_from} where "
                      "it was retired from: a link or junction on the way was added or retargeted after apply; "
                      f"restore that folder, then run {action} again",
                      {"path": str(original), "resolves_to": resolved, "retired_from": retired_from})


def recorded_inventory(value: object, field: str, absent: bool = True) -> None:
    if value is None and absent:
        return
    if not isinstance(value, dict) or "." not in value:
        raise Refused(f"The recorded {field} is not an inventory")
    for name, entry in value.items():
        if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name
                or ".." in PurePosixPath(name).parts or PurePosixPath(name).as_posix() != name
                or not isinstance(entry, dict) or entry.get("type") not in ("dir", "file")
                or type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777):
            raise Refused(f"The recorded {field} has an invalid inventory entry")
        if name == "." and entry["type"] != "dir":
            raise Refused(f"The recorded {field} has no directory root")
        if entry["type"] == "dir" and set(entry) != {"type", "mode"}:
            raise Refused(f"The recorded {field} has an invalid directory entry")
        if entry["type"] == "file" and (set(entry) != {"type", "mode", "sha256", "bytes"}
                                        or not isinstance(entry["sha256"], str)
                                        or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                                        or type(entry["bytes"]) is not int or entry["bytes"] < 0):
            raise Refused(f"The recorded {field} has an invalid file entry")


def plan_metadata(plan: dict) -> tuple[Locations, Path]:
    """Check the saved plan's operative shape before opening the state lock.

    A fresh plan under the lock still owns value drift. This check only prevents an incomplete or
    malformed saved plan from creating state before its values can be compared with that fresh plan.
    """
    if type(plan.get("plan_format")) is not int or plan["plan_format"] != 1:
        raise Refused("Unsupported plan format")
    missing = [key for key in DRIFT_KEYS if key not in plan]
    if missing:
        raise Refused("The plan is missing operative fields; plan again", missing)

    def invalid(field: str) -> None:
        raise Refused(f"The plan has an invalid {field}; plan again", [field])

    def text_field(value: object, field: str) -> str:
        if (not isinstance(value, str) or not value
                or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            invalid(field)
        return value

    runtime, home, config = plan["runtime"], plan["home"], plan["config_root"]
    if runtime not in ("claude", "codex"):
        invalid("runtime")
    text_field(plan["installer_version"], "installer_version")
    for field in ("home", "config_root", "target", "state_root"):
        normalized_absolute(plan[field], f"plan {field}")
    roots = plan["skill_roots"]
    if not isinstance(roots, list) or not roots:
        invalid("skill_roots")
    for root in roots:
        normalized_absolute(root, "plan skill_roots")
    locations = Locations(runtime, home, config)

    package = plan["package"]
    if not isinstance(package, dict):
        invalid("package")
    if "archive_sha256" not in package:
        invalid("package.archive_sha256")
    source = normalized_absolute(package.get("source"), "plan package source")
    for field in ("package", "version", "manifest_sha256", "checksums_sha256", "archive_digest", "scope"):
        text_field(package.get(field), f"package.{field}")
    for field in ("manifest_sha256", "checksums_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", package[field]):
            invalid(f"package.{field}")
    archive_hash = package.get("archive_sha256")
    if archive_hash is not None and (not isinstance(archive_hash, str)
                                     or not re.fullmatch(r"[0-9a-f]{64}", archive_hash)):
        invalid("package.archive_sha256")
    if type(package.get("files_verified")) is not int or package["files_verified"] < 0:
        invalid("package.files_verified")

    retirement_list = plan["retirement_list"]
    if (not isinstance(retirement_list, dict) or type(retirement_list.get("version")) is not int
            or not isinstance(retirement_list.get("paths"), dict)):
        invalid("retirement_list")
    for name, version in retirement_list["paths"].items():
        try:
            package_path(name)
        except ValueError:
            invalid("retirement_list")
        text_field(version, "retirement_list")

    try:
        recorded_inventory(plan["before"], "plan before")
    except Refused:
        invalid("before")
    if plan["before"] is not None and any("\x00" in name for name in plan["before"]):
        invalid("before")
    classification = plan["classification"]
    if not isinstance(classification, dict) or any(
            not isinstance(name, str) or "\x00" in name or not isinstance(kind, str)
            for name, kind in classification.items()):
        invalid("classification")
    retirements = plan["retirements"]
    if not isinstance(retirements, list):
        invalid("retirements")
    for item in retirements:
        if not isinstance(item, dict):
            invalid("retirements")
        try:
            package_path(item.get("path"))
        except ValueError:
            invalid("retirements")
        text_field(item.get("listed_as"), "retirements")
        if (not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item.get("bytes")) is not int or item["bytes"] < 0):
            invalid("retirements")
    copies = plan["retirement_copies"]
    if not isinstance(copies, dict):
        invalid("retirement_copies")
    normalized_absolute(copies.get("state_root"), "plan retirement_copies.state_root")
    if "transaction" not in copies or copies["transaction"] is not None:
        invalid("retirement_copies")
    text_field(copies.get("note"), "retirement_copies")

    duplicates = plan["duplicates"]
    duplicate_inventories = plan["duplicate_inventories"]
    duplicate_physical = plan["duplicate_physical"]
    if not isinstance(duplicates, list) or not isinstance(duplicate_inventories, dict):
        invalid("duplicates" if not isinstance(duplicates, list) else "duplicate_inventories")
    for duplicate in duplicates:
        normalized_absolute(duplicate, "plan duplicates")
    if not isinstance(duplicate_physical, dict) or set(duplicate_physical) != set(duplicates):
        invalid("duplicate_physical")
    for duplicate, listed in duplicate_inventories.items():
        normalized_absolute(duplicate, "plan duplicate_inventories")
        try:
            recorded_inventory(listed, "plan duplicate", absent=False)
        except Refused:
            invalid("duplicate_inventories")
        if any("\x00" in name for name in listed):
            invalid("duplicate_inventories")
    for duplicate, physical in duplicate_physical.items():
        normalized_absolute(duplicate, "plan duplicate_physical")
        try:
            recorded_physical(physical, "plan duplicate")
        except Refused:
            invalid("duplicate_physical")
    return locations, source


def receipt_metadata(receipt: dict, locations: Locations) -> Path:
    if type(receipt.get("receipt_format")) is not int or receipt["receipt_format"] != 1:
        raise Refused("Unsupported receipt format")
    if receipt.get("state") not in ("installed", "rolled back", "recovered to the before-state; not installed"):
        raise Refused("The receipt has an unsupported state")
    selected_locations(receipt, locations)
    transaction = transaction_path(locations, receipt.get("transaction"))
    plain_transaction_path(locations.state, transaction / "receipt.json")
    recorded_path(receipt.get("journal"), transaction / "journal.json", "receipt journal", locations.state)
    before = receipt.get("before")
    recorded_inventory(before, "before")
    recorded_inventory(receipt.get("after"), "after-inventory", absent=False)
    for key, expected in (("backup", transaction / "backup" / SKILL),
                          ("retired", transaction / "retired" / SKILL)):
        if before is None:
            if receipt.get(key) is not None:
                raise Refused(f"The recorded {key} is unexpected without a before-state")
        else:
            recorded_path(receipt.get(key), expected, key, locations.state)
    duplicates = receipt.get("duplicates")
    if not isinstance(duplicates, list):
        raise Refused("The recorded duplicates are not a list")
    originals = set()
    for index, duplicate in enumerate(duplicates):
        if not isinstance(duplicate, dict):
            raise Refused("The recorded duplicate is not an object")
        original = duplicate_path(locations, duplicate.get("path"))
        if str(original) in originals:
            raise Refused("The recorded duplicate is repeated")
        originals.add(str(original))
        recorded_path(duplicate.get("retired_to"), transaction / "duplicates" / str(index) / original.name,
                      "retired duplicate", locations.state)
        recorded_path(duplicate.get("backup"), transaction / "backup" / f"duplicate-{index}" / original.name,
                      "duplicate backup", locations.state)
        recorded_inventory(duplicate.get("inventory"), "duplicate", absent=False)
        if "resolved" in duplicate:
            if not isinstance(duplicate["resolved"], str):
                raise Refused(f"The receipt records no valid location for the retired duplicate {original}; rollback refused")
            normalized_absolute(duplicate["resolved"], "resolved duplicate")
        if "physical" in duplicate:
            recorded_physical(duplicate["physical"], "duplicate")
    retirements = receipt.get("retirements", [])
    if not isinstance(retirements, list):
        raise Refused("The recorded retirements are not a list")
    for item in retirements:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise Refused("The recorded retirement is not an object with a path")
        try:
            name = package_path(item["path"])
        except ValueError:
            raise Refused("The recorded retirement path is invalid") from None
        if before is None or item["path"] not in before:
            raise Refused("The recorded retirement is absent from the before-state")
        for key, base in (("retired_copy", transaction / "retired" / SKILL),
                          ("backup", transaction / "backup" / SKILL)):
            if key in item:
                recorded_path(item[key], base.joinpath(*name.parts), key, locations.state)
    installer = receipt.get("installer_copy")
    if installer is not None:
        if not isinstance(installer, dict):
            raise Refused("The recorded installer copy is not an object")
        recorded_path(installer.get("path"), transaction / "installer" / PACKAGE_INSTALLER, "installer copy",
                      locations.state)
    command = receipt.get("rollback_command")
    if command is not None and (not isinstance(command, list) or len(command) != 5
                                or not isinstance(command[0], str) or not command[0]
                                or command[1:] != [str(transaction / "installer" / PACKAGE_INSTALLER), "rollback",
                                                   "--receipt", str(transaction / "receipt.json")]):
        raise Refused("The recorded rollback command is not bound to this receipt")
    if command is not None:
        plain_transaction_path(locations.state, transaction / "installer" / PACKAGE_INSTALLER)
    rollback = receipt.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict) or not isinstance(rollback.get("journal"), str):
            raise Refused("The recorded rollback evidence has no journal")
        rollback_journal = normalized_absolute(rollback["journal"], "rollback evidence journal")
        source, kind = journal_kind(locations, rollback_journal)
        if source != transaction or kind != "rollback":
            raise Refused("The recorded rollback evidence belongs to another transaction")
        recorded_path(rollback.get("parked"), rollback_journal.parent / "parked" / SKILL,
                      "rollback evidence parked target", locations.state)
    return transaction


def journal_metadata(record: dict, journal: Path, locations: Locations) -> dict | None:
    transaction, kind = journal_kind(locations, journal)
    installed_record = None
    if record.get("operation") != kind:
        raise Refused("The journal operation does not match its generated path")
    if "history" in record:
        history = record["history"]
        if (not isinstance(history, list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get("state"), str) or not entry["state"]
                or not isinstance(entry.get("time"), str) or not entry["time"] for entry in history)):
            raise Refused("The journal history is not a list of recorded states")
    if "rollback_evidence" in record:
        evidence = record["rollback_evidence"]
        if (kind != "rollback" or not isinstance(evidence, dict)
                or set(evidence) != {"maintenance_boundary", "test_hooks"}
                or not isinstance(evidence["maintenance_boundary"], dict)
                or not isinstance(evidence["test_hooks"], dict)
                or any(key not in (TEST_FAULT, TEST_CRASH, TEST_TRACE, TEST_PROCESSES)
                       or not isinstance(value, str) or not value for key, value in evidence["test_hooks"].items())):
            raise Refused("The rollback journal has invalid evidence")
        boundary = evidence["maintenance_boundary"]
        if (set(boundary) != {"confirmed_by_operator", "probe", "active_consumers", "limitations"}
                or boundary["confirmed_by_operator"] is not True
                or not isinstance(boundary["probe"], str) or not boundary["probe"]
                or boundary["active_consumers"] != []
                or not isinstance(boundary["limitations"], str)):
            raise Refused("The rollback journal has invalid maintenance evidence")
    recorded_path(record.get("target"), locations.target, "journal target")
    recorded_inventory(record.get("origin"), "origin")
    recorded_inventory(record.get("incoming"), "incoming")
    if kind == "apply":
        if record.get("incoming") is None:
            raise Refused("The apply journal has no incoming inventory")
        expected_backup = transaction / "backup" / SKILL
        expected_incoming = transaction / "staged" / SKILL
        expected_parked = transaction / "retired" / SKILL
        if record.get("moves_last") is not None and record["moves_last"] is not False:
            raise Refused("The apply journal has an invalid move order")
    else:
        if record.get("origin") is None:
            raise Refused("The rollback journal has no origin inventory")
        work = journal.parent
        expected_backup = work / "origin-backup" / SKILL
        expected_incoming = None
        expected_parked = work / "parked" / SKILL
        recorded_path(record.get("journal"), journal, "rollback journal", locations.state)
        recorded_path(record.get("receipt"), transaction / "receipt.json", "rollback receipt", locations.state)
        if record.get("moves_last") is not True:
            raise Refused("The rollback journal has an invalid move order")
        receipt = read_control_json(transaction / "receipt.json", locations.state, "run recover again")
        if receipt_metadata(receipt, locations) != transaction:
            raise Refused("The rollback receipt belongs to another transaction; CURRENT was kept")
        if canonical(record.get("incoming")) != canonical(receipt.get("before")):
            raise Refused("The rollback journal's incoming inventory differs from its receipt")
        rollback_receipt_matches_journal(record, receipt)
        terminal_receipt = record.get("state") == "committed" and receipt["state"] == "rolled back"
        installed_record = committed_apply_record_for_receipt(
            transaction, receipt, locations, allow_rolled_back=terminal_receipt)
    backup = record.get("origin_backup")
    if record.get("origin") is None:
        if backup is not None:
            raise Refused("The journal backup is unexpected without an origin")
    elif backup is not None:
        recorded_path(backup, expected_backup, "journal backup", locations.state)
    planned = record.get("planned_origin")
    if planned is not None:
        recorded_inventory(planned, "planned origin", absent=False)
        state = record.get("state")
        if record["origin"] is None or backup is not None or not isinstance(state, str) or (
                state not in {"parked-drift", "undoing", "restoring", "undone"}
                and not re.fullmatch(r"(?:restoring|restored)-move-[0-9]+", state)):
            raise Refused("The journal has an invalid parked-drift origin")
    elif record["origin"] is not None and backup is None:
        raise Refused("The journal has no backup for its recorded origin")
    recorded_path(record.get("parked"), expected_parked, "parked target", locations.state)
    incoming_path = record.get("incoming_path")
    if kind == "apply":
        recorded_path(incoming_path, expected_incoming, "staged target", locations.state)
    elif record.get("incoming") is None:
        if incoming_path is not None:
            raise Refused("The rollback journal has an unexpected incoming path")
    elif incoming_path not in (str(transaction / "retired" / SKILL), str(journal.parent / "restore" / SKILL)):
        raise Refused("The rollback incoming path is outside its transaction")
    elif incoming_path is not None:
        plain_transaction_path(locations.state, Path(incoming_path))
    moves = record.get("moves")
    if not isinstance(moves, list):
        raise Refused("The journal moves are not a list")
    for index, move in enumerate(moves):
        if not isinstance(move, dict):
            raise Refused("The journal move is not an object")
        recorded_inventory(move.get("inventory"), "move", absent=False)
        if kind == "apply":
            source = duplicate_path(locations, move.get("from"))
            recorded_path(move.get("to"), transaction / "duplicates" / str(index) / source.name, "retired move",
                          locations.state)
            recorded_path(move.get("backup"), transaction / "backup" / f"duplicate-{index}" / source.name,
                          "move backup", locations.state)
            if "resolved" in move:
                normalized_absolute(move["resolved"], "resolved move")
            recorded_move_physical(move, "from", "apply move")
        else:
            if index >= len(receipt["duplicates"]):
                raise Refused("The rollback journal has an unrecorded duplicate")
            if set(move) not in ({"from", "to", "inventory"}, {"from", "to", "inventory", "physical"}):
                raise Refused("The rollback journal move has unexpected fields")
            original = duplicate_path(locations, move.get("to"))
            recorded_path(str(original), Path(receipt["duplicates"][index]["path"]), "restored duplicate")
            if move.get("from") not in (receipt["duplicates"][index]["retired_to"],
                                        str(journal.parent / "restore-duplicates" / str(index) / original.name)):
                raise Refused("The rollback duplicate source is outside its transaction")
            plain_transaction_path(locations.state, Path(move["from"]))
            if canonical(move["inventory"]) != canonical(receipt["duplicates"][index]["inventory"]):
                raise Refused("The rollback journal move inventory differs from its receipt")
            recorded_move_physical(move, "to", "rollback move")
            authoritative = installed_record["moves"][index].get("physical", {}).get("from")
            operative = move.get("physical", {}).get("to")
            if authoritative is None or receipt["duplicates"][index].get("physical") is None or operative is None:
                raise Blocked("The rollback lacks the original duplicate-parent identity; automatic recovery is "
                              "refused. Keep its backups and use manual restoration after verifying the original folder")
            if canonical(operative) != canonical(authoritative):
                raise Refused("The rollback move's physical destination differs from its committed apply journal; CURRENT was kept")
    if kind == "rollback" and len(moves) != len(receipt["duplicates"]):
        raise Refused("The rollback journal omits a recorded duplicate")
    # Undo creates these copies from the journal's parked and move paths before renaming them.
    plain_transaction_path(locations.state, expected_parked.parent / "restore" / SKILL)
    if kind == "apply":
        for index, move in enumerate(moves):
            destination = Path(move["to"])
            plain_transaction_path(locations.state,
                                   destination.parent.parent / f"restore-{index}" / Path(move["from"]).name)
    for key, allowed in (("parked_drift", {str(expected_parked)}),
                         ("moved_drift", {move["to"] for move in moves})):
        if key not in record:
            continue
        if key == "moved_drift" and not isinstance(record[key], list):
            raise Refused("The journal moved_drift is not a list")
        values = [record[key]] if key == "parked_drift" else record[key]
        if not isinstance(values, list) or any(not isinstance(item, str) or item not in allowed for item in values):
            raise Refused(f"The journal {key} is not a generated path")
    stale = record.get("stale_restore_copies", [])
    stale_parents = {expected_parked.parent, transaction / "duplicates"}
    if not isinstance(stale, list) or any(not isinstance(item, str) or Path(item).parent not in stale_parents
                                          or not RESTORE_STALE_NAME.fullmatch(Path(item).name) for item in stale):
        raise Refused("The journal has an invalid stale restoration path")
    for item in stale:
        plain_transaction_path(locations.state, Path(item))
    state = record.get("state")
    ordinary = {"staged", "prepared", "parking", "parked", "parked-drift", "activating", "activated", "undoing",
                "restoring", "undone", "committed"}
    if state is not None and (not isinstance(state, str) or (state not in ordinary and not re.fullmatch(
            r"(?:moving|moved|restoring-move|restored-move)-[0-9]+", state))):
        raise Refused("The journal has an invalid state")
    return installed_record


def current_journal(locations: Locations) -> Path:
    current = locations.state / "CURRENT"
    require_trusted_state(locations.state, (current, True))
    if is_link(current) or not current.is_file():
        raise Refused("CURRENT is not a regular selector file")
    try:
        with open(current, "rb") as stream:
            data = stream.read(512)
            if stream.read(1):
                raise Refused("CURRENT is too long for a generated journal selector")
        selection = data.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise Refused(f"Cannot read CURRENT as a UTF-8 journal selector: {error}") from None
    if not selection.endswith("\n") or selection.count("\n") != 1:
        raise Refused("CURRENT is not one generated journal selector line")
    relative = Path(selection[:-1])
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts) or str(relative) != selection[:-1]:
        raise Refused("CURRENT is not a generated relative journal selector")
    journal = locations.state / relative
    journal_kind(locations, journal)
    if not journal.parent.is_dir():
        raise Refused("CURRENT does not select an existing transaction directory")
    return journal


def require_trusted_selection(locations: Locations) -> None:
    """Preflight the existing CURRENT authority before Lock can create or open its control file."""
    state = locations.state
    require_trusted_state(state)
    if not exists(state / "CURRENT"):
        return
    journal = current_journal(locations)
    if not exists(journal):
        raise Incomplete(f"The selected journal {journal} is missing; rename state cannot be established. "
                         "CURRENT and staging data were kept for inspection")
    require_trusted_state(state, (journal, True))
    transaction, kind = journal_kind(locations, journal)
    if kind == "rollback":
        require_trusted_state(state, (transaction / "receipt.json", True), (transaction / "journal.json", True))
    elif exists(journal.parent / "receipt.json"):
        require_trusted_state(state, (journal.parent / "receipt.json", True))


# The swap engine. An operation moves `moves` (from -> to), parks the target and activates an
# incoming tree. Undo decides from the filesystem, checked against recorded inventories, never
# from the journal state alone.
class Operation:
    def __init__(self, journal_path: Path, record: dict, locations: Locations) -> None:
        self.installed_record = journal_metadata(record, journal_path, locations)
        self.locations = locations
        self.path = journal_path
        self.record = record
        self._move_parents = {}

    @contextmanager
    def bound_moves(self, create_internal: bool = True, writable: bool = True):
        if self._move_parents:
            yield
            return
        with ExitStack() as opened:
            parents = {}
            unbound = []
            try:
                for index, move in enumerate(self.record["moves"]):
                    physical = move.get("physical", {})
                    if not isinstance(physical, dict) or set(physical) - {"from", "to"}:
                        raise Refused("Invalid physical duplicate-move bindings")
                    external = "to" if self.record.get("moves_last") else "from"
                    # A pathname from an older journal cannot prove which physical
                    # directory occupied it before the crash. Never bind history to
                    # the current directory merely because its spelling is unchanged.
                    if external not in physical:
                        raise Blocked("The journal lacks the original duplicate-parent identity; automatic recovery "
                                      "is refused. Keep its backups and use manual restoration after verifying the original folder")
                    for side in ("from", "to"):
                        path = Path(move[side])
                        if side not in physical:
                            unbound.append((index, side, path))
                            continue
                        if not path.parent.is_dir():
                            raise Blocked(f"The recorded duplicate-move parent {path.parent} is missing; restore it and retry")
                        parents[index, side] = opened.enter_context(
                            PhysicalDirectory(path.parent, physical[side], writable=writable))
                # Every historical binding is checked while its handle stays open. In particular,
                # a later bad binding cannot leave an earlier generated transaction parent behind.
                for index, side, path in unbound:
                    if create_internal:
                        private_mkdirs(path.parent, self.locations.state)
                    elif not path.parent.is_dir():
                        raise Refused("The committed move's transaction parent is missing; CURRENT was kept")
                    parents[index, side] = opened.enter_context(
                        PhysicalDirectory(path.parent, _UNBOUND_DIRECTORY, writable=writable))
                for index, move in enumerate(self.record["moves"]):
                    physical = move.get("physical", {})
                    for side in ("from", "to"):
                        physical[side] = parents[index, side].describe()
                    move["physical"] = physical
                self._move_parents = parents
                yield
            finally:
                self._move_parents = {}

    def move_inventory(self, index: int, side: str) -> dict | None:
        return self._move_parents[index, side].inventory(Path(self.record["moves"][index][side]).name)

    def move_exists(self, index: int, side: str) -> bool:
        return self._move_parents[index, side].exists(Path(self.record["moves"][index][side]).name)

    def rename_move(self, index: int, source_side: str, destination_side: str, point: str) -> None:
        move = self.record["moves"][index]
        rename(Path(move[source_side]), Path(move[destination_side]), point,
               self._move_parents[index, source_side], self._move_parents[index, destination_side])

    def save(self, state: str) -> None:
        self.record["state"] = state
        self.record.setdefault("history", []).append({"state": state, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        write_json(self.path, self.record, self.locations.state)
        checkpoint(self.record["operation"] + ":" + state)

    @property
    def target(self) -> Path:
        return Path(self.record["target"])

    def run(self) -> None:
        with self.bound_moves():
            self.run_bound()

    def run_bound(self) -> None:
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
        for index, move in enumerate(self.record["moves"]):
            problems += differences(move["inventory"], self.move_inventory(index, "to"))
        if problems:
            raise Refused("The activated state does not match its verified inventory", problems)

    def run_moves(self) -> None:
        for index, move in enumerate(self.record["moves"]):
            self.save(f"moving-{index}")
            self.rename_move(index, "from", "to", self.record["operation"] + f":move-{index}")
            self.save(f"moved-{index}")

    def undo_moves(self) -> None:
        op = self.record["operation"]
        for index, move in reversed(list(enumerate(self.record["moves"]))):
            source, destination = Path(move["from"]), Path(move["to"])
            if self.move_exists(index, "from"):
                if differences(move["inventory"], self.move_inventory(index, "from")) or (
                        self.move_exists(index, "to") and str(destination) not in self.record.get("moved_drift", [])):
                    raise Incomplete("A moved copy cannot be located unambiguously", {"from": str(source), "to": str(destination)})
                continue
            if self.move_exists(index, "to") and not differences(move["inventory"], self.move_inventory(index, "to")):
                self.rename_move(index, "to", "from", f"{op}:undo-move-{index}")
                continue
            # The moved copy is lost or changed. Apply kept a verified backup of each retired duplicate.
            backup = move.get("backup")
            if not backup or differences(move["inventory"], inventory(Path(backup))):
                raise Incomplete("A moved copy is missing or changed and no verified backup exists",
                                 {"from": str(source), "to": str(destination)})
            if self.move_exists(index, "to") and str(destination) not in self.record.get("moved_drift", []):
                # Keep the differing copy where it is, as for the target. Saved before the restoring rename,
                # so a rerun after a kill recognizes the kept copy.
                self.record.setdefault("moved_drift", []).append(str(destination))
            self.save(f"restoring-move-{index}")
            self.restore_verified(Path(backup), move["inventory"], destination.parent.parent / f"restore-{index}" / source.name,
                                  source, f"{op}:undo-move-restore-{index}", self._move_parents[index, "from"])
            self.save(f"restored-move-{index}")

    def undo(self) -> None:
        with self.bound_moves():
            self.undo_bound()

    def undo_bound(self) -> None:
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
        for index, move in enumerate(self.record["moves"]):
            problems += differences(move["inventory"], self.move_inventory(index, "from"))
        if problems:
            raise Incomplete("The restored state does not match the origin inventory", problems)
        self.save("undone")

    def restore_from_backup(self) -> None:
        backup = self.record.get("origin_backup")
        if not backup or differences(self.record["origin"], inventory(Path(backup))):
            raise Incomplete("The origin copy is missing and no verified backup exists", {"target": str(self.target)})
        self.restore_verified(Path(backup), self.record["origin"], Path(self.record["parked"]).parent / "restore" / SKILL,
                              self.target, self.record["operation"] + ":undo-restore")

    def restore_verified(self, backup: Path, expected: dict, copy: Path, destination: Path, label: str, destination_parent=None) -> None:
        """Copy a verified backup beside the transaction, check it, then rename it into place."""
        if exists(copy):
            # A copy left by an interrupted restoration; kept aside, never deleted. The journal names the aside
            # path before the rename, so a kill right after the rename still leaves it in the final report.
            stale = copy.parent.parent / ("restore-stale-" + uuid.uuid4().hex[:8])
            self.record.setdefault("stale_restore_copies", []).append(str(stale))
            write_json(self.path, self.record, self.locations.state)
            rename(copy.parent, stale, label + ":set-aside")
        copy_tree(backup, copy)
        fsync_tree(copy)
        if differences(expected, inventory(copy)):
            raise Incomplete("The restoration copy does not match its inventory", {"path": str(copy)})
        rename(copy, destination, label, destination_parent=destination_parent)


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
    require_trusted_state(state)
    if exists(current):
        raise Blocked(f"An interrupted transaction is recorded in {current}; run `recover` first")
    write_durable(current, (str(journal.relative_to(state)) + "\n").encode("utf-8"), state)


def incomplete_state(error: object, state: Path, data: Path) -> Incomplete:
    return Incomplete(f"State protection or finalization failed during an active transaction: {error}. "
                      f"CURRENT at {state / 'CURRENT'} and recovery data at {data} were kept; reconcile the state "
                      "from trusted evidence, then run `recover` with the original --plan or --receipt")


@contextmanager
def active_recovery(state: Path):
    try:
        yield
    except UntrustedState as error:
        raise incomplete_state(error, state, state) from None


def finish(state: Path) -> None:
    try:
        require_trusted_state(state)
    except UntrustedState as error:
        raise incomplete_state(error, state, state) from None
    try:
        (state / "CURRENT").unlink()
    except OSError as error:
        raise incomplete_state(error, state, state) from None
    sync_directory(state)


def record_undo_error(operation: Operation, error: Exception) -> str:
    """Journal why an undo failed; when the journal cannot record it either, say so in the report instead.

    The same cause (a full disk, an unwritable state directory) can stop both, and the report must still
    name the original failure and `recover` (#54)."""
    operation.record["undo_error"] = str(error)
    try:
        write_json(operation.path, operation.record, operation.locations.state)
    except OSError as unrecorded:
        return f" (the journal could not record it: {unrecorded})"
    return ""


def undo_or_report(operation: Operation, failure: object) -> None:
    """Restore the origin state after failure, or record why not and report an incomplete restoration."""
    try:
        operation.undo()
    except UntrustedState as error:
        raise incomplete_state(f"{failure}; restoration could not safely proceed: {error}", operation.locations.state,
                               operation.path.parent) from None
    except (Failure, OSError) as error:
        try:
            note = record_undo_error(operation, error)
        except UntrustedState as trust:
            raise incomplete_state(f"{failure}; restoration incomplete: {error}; journal update refused: {trust}",
                                   operation.locations.state, operation.path.parent) from None
        raise Incomplete(f"{failure}; restoration incomplete: {error}{note}. Recovery "
                         f"data kept in {operation.path.parent}; run `recover` with the same --plan or --receipt before "
                         "any other step", getattr(error, "details", None))


def attempt(operation: Operation, state: Path) -> None:
    """Run an operation; on failure restore the origin state or report an incomplete restoration."""
    try:
        operation.run()
    except UntrustedState as error:
        raise incomplete_state(error, state, operation.path.parent) from None
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
    # A read-only plan emits no checkpoints, so it only validates the hook paths.
    require_test_home(Locations(options.runtime, options.home), checkpoints=False)
    plan, _ = make_plan(options.runtime, options.home, None, Path(options.package), Path(options.checksums))
    if options.output:
        write_json(Path(options.output), plan)
    return plan


def command_apply(options) -> dict:
    plan = read_json(Path(options.plan), "run apply again")
    locations, package_source = plan_metadata(plan)
    runtime, home, config = plan["runtime"], plan["home"], plan["config_root"]
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check()
    require_trusted_selection(locations)
    with Lock(locations.state):
        fresh, package = make_plan(runtime, home, config, package_source,
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


def keep_installer(package: Package, transaction: Path, state: Path) -> dict:
    """Preserve the package's installer as historical evidence for existing receipt contracts.

    Its receipt-supplied path and hash do not establish code provenance for a later rollback.
    """
    listed = package.manifest["files"].get(PACKAGE_INSTALLER)
    data = package.files.get(PACKAGE_INSTALLER)
    if data is None or not isinstance(listed, dict) or listed.get("sha256") != digest(data):
        raise Refused(f"The verified package holds no {PACKAGE_INSTALLER} matching its manifest")
    # Named like the original: the maintenance boundary recognizes this installer's launchers by that name.
    path = transaction / "installer" / PACKAGE_INSTALLER
    write_durable(path, data, state)
    if digest(path.read_bytes()) != listed["sha256"]:
        raise Refused(f"The installer copy {path} does not match the package manifest")
    return {"path": str(path), "sha256": listed["sha256"]}


def install(plan: dict, package: Package, locations: Locations, boundary: dict) -> dict:
    # The package bytes verified by the fresh plan, not a second read of the source.
    package_files = package.skill_files()
    before = plan["before"]
    transaction = locations.state / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    journal = transaction / "journal.json"
    private_mkdirs(transaction, locations.state)
    begin(locations.state, journal)
    try:
        installer = keep_installer(package, transaction, locations.state)
        backup = transaction / "backup" / SKILL
        if before is not None:
            copy_tree(locations.target, backup)
            fsync_tree(backup)
            if differences(before, inventory(backup)):
                raise Refused("The backup copy does not match the target inventory")
        moves = []
        for index, duplicate in enumerate(plan["duplicates"]):
            path = Path(duplicate)
            duplicate_backup = transaction / "backup" / f"duplicate-{index}" / path.name
            with PhysicalDirectory(path.parent, plan["duplicate_physical"][duplicate], writable=False) as parent:
                duplicate_inventory = parent.inventory(path.name)
                if differences(plan["duplicate_inventories"][duplicate], duplicate_inventory):
                    raise Refused(f"The duplicate {path} changed after it was planned")
                copy_tree(parent.path / path.name, duplicate_backup)
                fsync_tree(duplicate_backup)
                if differences(duplicate_inventory, inventory(duplicate_backup)):
                    raise Refused(f"The backup copy of {path} does not match its inventory")
                physical = parent.describe()
                resolved = str(parent.path / path.name)
            # Where the copy physically was, so rollback can refuse a link added or retargeted since (#54).
            moves.append({"from": str(path), "to": str(transaction / "duplicates" / str(index) / path.name),
                          "inventory": duplicate_inventory, "backup": str(duplicate_backup),
                          "resolved": resolved, "physical": {"from": physical}})
        staged = transaction / "staged" / SKILL
        stage(staged, package_files, locations.target, before or {}, plan["classification"])
        fsync_tree(staged)
        after = inventory(staged)
        problems = staging_problems(after, package_files, before or {}, plan["classification"])
        if problems:
            raise Refused("The staged tree does not match its expected inventory", problems)
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
            "state": "staged",
        }, locations)
        write_json(journal, operation.record, locations.state)
        checkpoint("apply:staged")
    except UntrustedState as error:
        raise incomplete_state(error, locations.state, transaction) from None
    except OSError as error:
        finish(locations.state)
        raise Refused(f"Backup or staging failed before any rename: {error}; the target is unchanged")
    except BaseException:
        finish(locations.state)
        raise
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
                        "resolved": move["resolved"], "physical": move["physical"]["from"]} for move in moves],
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
        # names the historical installer copy for compatibility; secure execution requires an
        # independently authenticated installer, as the guides explain.
        "rollback_command": [sys.executable, installer["path"], "rollback", "--receipt", str(receipt_path)],
    }
    try:
        write_json(receipt_path, receipt, locations.state)
        operation.save("committed")
        finish(locations.state)
    except UntrustedState as error:
        raise incomplete_state(error, locations.state, transaction) from None
    except (Failure, OSError) as error:
        if isinstance(error, Incomplete):
            raise
        raise incomplete_state(error, locations.state, transaction) from None
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
    if is_link(given):
        raise Refused("The selected receipt is a link or junction")
    if receipt.get("receipt_format") != 1:
        raise Refused("Unsupported receipt format")
    locations = locations_from_record(receipt)
    transaction = receipt_metadata(receipt, locations)
    # Only the receipt inside its transaction is authoritative; a copy elsewhere must be identical.
    receipt_path = transaction / "receipt.json"
    require_trusted_state(locations.state, (receipt_path, False), (transaction / "journal.json", False))
    if not same_path(str(given), str(receipt_path)) and canonical(read_control_json(receipt_path, locations.state,
                                                                                  "run rollback again")) != canonical(receipt):
        raise Refused(f"The receipt differs from the canonical receipt {receipt_path}; roll back with that one")
    if receipt.get("state") != "installed":
        raise Refused(f"The receipt state is {receipt.get('state')!r}; only an installed receipt can be rolled back")
    if not isinstance(receipt.get("after"), dict):
        raise Refused("The receipt has no after-inventory; rollback refused")
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check("run rollback again")
    require_trusted_selection(locations)
    with Lock(locations.state):
        require_trusted_state(locations.state, (receipt_path, False), (transaction / "journal.json", False))
        current_receipt = read_control_json(receipt_path, locations.state, "run rollback again")
        if canonical(current_receipt) != canonical(receipt):
            raise Refused("The canonical receipt changed before rollback; retry with its current contents")
        receipt = current_receipt
        receipt_metadata(receipt, locations)
        if exists(locations.state / "CURRENT"):
            raise Blocked(f"An interrupted transaction is recorded in {locations.state / 'CURRENT'}; run `recover` first")
        installed_record = committed_apply_record_for_receipt(transaction, receipt, locations)
        # A receipt field is untrusted input: only an integer mode is quoted back in guidance.
        root = receipt["after"].get(".")
        recorded = root.get("mode") if isinstance(root, dict) else None
        recorded = recorded if type(recorded) is int else None
        # Each retired duplicate returns to its original root, or below its nearest existing ancestor (#54).
        # The retired copy itself is not checked: its inventory records its mode, so one that lost owner
        # write no longer matches and the verified backup is restored instead.
        # The parent the duplicate returns through must be searchable as well as writable.
        restoring, physical_duplicates = [], {}
        for index, duplicate in enumerate(receipt["duplicates"]):
            original = Path(duplicate["path"])
            parent = searchable_part(original, "run rollback again")
            if parent == original:
                parent = original.parent
            if parent is not None:
                restoring.append((parent, f"so the retired duplicate {original} cannot be moved back into it"))
            # Check both the receipt and its committed apply journal. The latter is the
            # retained authority for the physical parent from which apply moved the copy.
            verify_duplicate_restoration_path(locations, original, duplicate)
            verify_duplicate_restoration_path(locations, original, installed_record["moves"][index])
            require_discovered_duplicate_path(locations, original)
            physical = installed_record["moves"][index]["physical"]["from"]
            with PhysicalDirectory(original.parent, physical, writable=False) as bound_parent:
                physical_duplicates[str(original)] = bound_parent.describe()
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
        private_mkdirs(work, locations.state)
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
                with PhysicalDirectory(original.parent, physical_duplicates[str(original)], writable=False) as parent:
                    physical = parent.describe()
                moves.append({"from": str(source), "to": str(original), "inventory": expected,
                              "physical": {"to": physical}})
        except UntrustedState as error:
            raise incomplete_state(error, locations.state, work) from None
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
        }, locations)
        attempt(operation, locations.state)
        try:
            operation.record["rollback_evidence"] = {"maintenance_boundary": boundary, "test_hooks": active_test_hooks()}
            operation.save("committed")
            mark_rolled_back(operation.record, locations)
            finish(locations.state)
        except UntrustedState as error:
            raise incomplete_state(error, locations.state, work) from None
        except (Failure, OSError) as error:
            if isinstance(error, Incomplete):
                raise
            raise incomplete_state(error, locations.state, work) from None
        return {"result": "rolled back", "target": str(locations.target), "restored": "absent" if receipt["before"] is None else "before-inventory"}


def mark_rolled_back(record: dict, locations: Locations, tolerant: bool = False) -> bool:
    journal_metadata(record, Path(record.get("journal", "")), locations)
    receipt_path = Path(record["receipt"])
    require_trusted_state(locations.state, (receipt_path, False))
    try:
        receipt = read_control_json(receipt_path, locations.state)
    except Refused:
        if tolerant:
            return False
        raise
    receipt_metadata(receipt, locations)
    receipt["state"] = "rolled back"
    receipt["rollback"] = {"journal": record["journal"], "parked": record["parked"]}
    if "rollback_evidence" in record:
        receipt["rollback"].update({key: record["rollback_evidence"][key]
                                    for key in ("maintenance_boundary", "test_hooks")})
    write_json(receipt_path, receipt, locations.state)
    return True


def owner_write_only_inventory_repair(recorded: dict, actual: dict) -> bool:
    """A legacy read-only root may have gained only owner write for rollback."""
    root = recorded.get(".")
    if not isinstance(root, dict) or type(root.get("mode")) is not int or root["mode"] & stat.S_IWUSR:
        return False
    repaired = {**recorded, ".": {**root, "mode": root["mode"] | stat.S_IWUSR}}
    return canonical(repaired) == canonical(actual)


def apply_receipt_matches_journal(record: dict, receipt: dict, journal: Path,
                                  allow_rolled_back: bool = False) -> None:
    """Bind a receipt written before apply's final journal save to that operation."""
    if receipt["transaction"] != str(journal.parent) or receipt["journal"] != str(journal):
        raise Refused("The apply receipt belongs to another transaction; CURRENT was kept")
    if record["state"] == "committed":
        valid_states = ("installed", "rolled back") if allow_rolled_back else ("installed",)
    else:
        valid_states = ("installed", "recovered to the before-state; not installed")
    if receipt["state"] == "recovered to the before-state; not installed" and record["state"] != "undone":
        raise Refused("The apply receipt state differs from its journal; CURRENT was kept")
    if receipt["state"] not in valid_states or canonical(record["origin"]) != canonical(receipt["before"]):
        raise Refused("The apply receipt differs from its journal before-state; CURRENT was kept")
    if (canonical(record["incoming"]) != canonical(receipt["after"])
            and not owner_write_only_inventory_repair(receipt["after"], record["incoming"])):
        raise Refused("The apply receipt differs from its journal after-state; CURRENT was kept")
    duplicates = [{"path": move["from"], "retired_to": move["to"], "backup": move["backup"],
                   "inventory": move["inventory"],
                   **({"resolved": move["resolved"]} if "resolved" in move else {})}
                  for move in record["moves"]]
    recorded = [{**{key: duplicate[key] for key in ("path", "retired_to", "backup", "inventory")},
                 **({"resolved": duplicate["resolved"]} if "resolved" in duplicate else {})}
                for duplicate in receipt["duplicates"]]
    if len(duplicates) == len(recorded):
        for expected, actual, move, duplicate in zip(duplicates, recorded, record["moves"], receipt["duplicates"]):
            # Legacy receipts omit this optional field; their rollback still checks the journal's
            # recorded location and applies the legacy linked-root rule to the receipt.
            if "resolved" not in actual:
                expected.pop("resolved", None)
            apply_physical = move.get("physical", {}).get("from")
            receipt_physical = duplicate.get("physical")
            if apply_physical is None or receipt_physical is None:
                raise Blocked("The receipt or committed apply journal lacks the original duplicate-parent identity; "
                              "automatic duplicate restoration is refused. Keep its backups and use manual "
                              "restoration after verifying the original folder")
            if canonical(apply_physical) != canonical(receipt_physical):
                raise Refused("The receipt's physical duplicate parent differs from its committed apply journal; CURRENT was kept")
    if canonical(duplicates) != canonical(recorded):
        raise Refused("The apply receipt differs from its journal duplicates; CURRENT was kept")


def committed_apply_record_for_receipt(transaction: Path, receipt: dict, locations: Locations,
                                       allow_rolled_back: bool = False) -> dict:
    """Use the retained committed apply journal as the receipt's restoration authority."""
    journal = transaction / "journal.json"
    record = read_control_json(journal, locations.state, "run rollback again")
    journal_metadata(record, journal, locations)
    if record.get("state") != "committed":
        raise Refused("The installed receipt has no committed apply journal; rollback refused")
    apply_receipt_matches_journal(record, receipt, journal, allow_rolled_back=allow_rolled_back)
    return record


def rollback_receipt_matches_journal(record: dict, receipt: dict) -> None:
    """The parked rollback origin is the installed tree, including the supported owner-write repair."""
    state = record.get("state")
    receipt_state = receipt["state"]
    if receipt_state != "installed" and (state != "committed" or receipt_state != "rolled back"):
        raise Refused("The rollback receipt state differs from its journal; CURRENT was kept")
    if receipt_state == "rolled back":
        evidence = receipt.get("rollback")
        if (not isinstance(evidence, dict) or evidence.get("journal") != record["journal"]
                or evidence.get("parked") != record.get("parked")):
            raise Refused("The rollback receipt evidence differs from its journal; CURRENT was kept")
    planned = record.get("planned_origin")
    origin = planned if planned is not None else record["origin"]
    expected = receipt["after"]
    if canonical(origin) == canonical(expected):
        return
    if owner_write_only_inventory_repair(expected, origin):
        return
    raise Refused("The rollback journal's origin inventory differs from its receipt; CURRENT was kept")


def command_recover(options) -> dict:
    canonical_receipt = None
    if options.plan or options.receipt:
        # The recorded config root, so recovery finds the state whatever the environment now says.
        recorded = read_json(Path(options.plan or options.receipt), "run recover again")
        locations = locations_from_record(recorded)
        if options.plan and (type(recorded.get("plan_format")) is not int or recorded["plan_format"] != 1):
            raise Refused("Unsupported plan format")
        if options.receipt:
            transaction = receipt_metadata(recorded, locations)
            canonical_receipt = transaction / "receipt.json"
    elif options.runtime:
        locations = Locations(options.runtime, options.home)
    else:
        raise Refused("recover needs --plan, --receipt or --runtime")
    require_test_home(locations)
    boundary = maintenance_boundary(options.maintenance_confirmed)
    locations.check("run recover again")
    controls = ((canonical_receipt, True), (canonical_receipt.parent / "journal.json", True)) if canonical_receipt else ()
    require_trusted_selection(locations)
    require_trusted_state(locations.state, *controls)
    if canonical_receipt and canonical(read_control_json(canonical_receipt, locations.state, "run recover again")) != canonical(recorded):
        raise Refused("The selected receipt differs from the protected canonical receipt")
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
    with Lock(locations.state), active_recovery(locations.state):
        require_trusted_state(locations.state, *controls)
        if canonical_receipt and canonical(read_control_json(canonical_receipt, locations.state, "run recover again")) != canonical(recorded):
            raise Refused("The selected receipt differs from the protected canonical receipt")
        current = locations.state / "CURRENT"
        if not exists(current):
            return nothing
        journal = current_journal(locations)
        if not exists(journal):
            raise Incomplete(f"The selected journal {journal} is missing; rename state cannot be established. "
                             "CURRENT and staging data were kept for inspection")
        record = read_control_json(journal, locations.state)
        operation = Operation(journal, record, locations)
        apply_receipt_path = journal.parent / "receipt.json"
        if record["operation"] == "apply":
            if record["origin"] is None and exists(Path(record["parked"])):
                raise Refused("The apply journal has a parked target without a recorded origin; CURRENT was kept")
            for move in record["moves"]:
                original = Path(move["from"])
                verify_duplicate_restoration_path(locations, original, move, recovery=True)
                require_discovered_duplicate_path(locations, original, recovery=True)
            if record.get("state") != "committed" and exists(apply_receipt_path):
                receipt = read_control_json(apply_receipt_path, locations.state, "run recover again")
                receipt_metadata(receipt, locations)
                apply_receipt_matches_journal(record, receipt, journal)
        else:
            receipt = read_control_json(Path(record["receipt"]), locations.state, "run recover again")
            receipt_metadata(receipt, locations)
            for index, move in enumerate(record["moves"]):
                original = Path(move["to"])
                verify_duplicate_restoration_path(locations, original, receipt["duplicates"][index], recovery=True)
                verify_duplicate_restoration_path(locations, original, operation.installed_record["moves"][index],
                                                   recovery=True)
                require_discovered_duplicate_path(locations, original, recovery=True)
        if record.get("state") == "committed":
            with operation.bound_moves(create_internal=False):
                changed = differences(record["incoming"], inventory(locations.target))
                for index, move in enumerate(record["moves"]):
                    changed += differences(move["inventory"], operation.move_inventory(index, "to"))
                if changed:
                    raise Refused("The committed journal does not match the installed state; CURRENT was kept")
                result = {"result": "already committed", "journal": str(journal)}
                if record["operation"] == "rollback" and not mark_rolled_back(record, locations, tolerant=True):
                    result["receipt_not_updated"] = record["receipt"]
                if record["operation"] == "apply":
                    # The apply output may never have been printed: name the receipt and what it retired.
                    receipt = apply_receipt_path
                    result["receipt"] = str(receipt)
                    try:
                        value = read_control_json(receipt, locations.state)
                    except Refused as error:
                        raise Incomplete(f"The committed apply receipt {receipt} is unavailable: {error}; "
                                         "CURRENT was kept for inspection") from None
                    else:
                        receipt_metadata(value, locations)
                        apply_receipt_matches_journal(record, value, journal)
                        result.update({key: value[key] for key in ("retirements", "rollback_command") if key in value})
                finish(locations.state)
                return result
        with operation.bound_moves():
            operation.record["recovery_boundary"] = boundary
            try:
                operation.undo_bound()
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
                value = read_control_json(receipt, locations.state)
                value["state"] = "recovered to the before-state; not installed"
                write_json(receipt, value, locations.state)
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
    global _TRACE
    options = parser().parse_args(argv)
    handlers = {"verify-package": command_verify, "plan": command_plan, "apply": command_apply,
                "rollback": command_rollback, "recover": command_recover}
    report = None
    try:
        result = handlers[options.command](options)
    except Failure as error:
        report = {"error": str(error), "exit": error.code, "details": error.details}
    except OSError as error:
        report = {"error": f"Unexpected filesystem error: {error}", "exit": 3}
    finally:
        trace, _TRACE = _TRACE, None
        if trace is not None:
            try:
                trace.close()
            except OSError as error:
                if report is None:
                    report = {"error": f"Trace cleanup failed: {error}", "exit": 3}
                else:
                    report["trace_cleanup_error"] = str(error)
    if report is not None:
        print(json.dumps({**report, **hook_report()}, indent=2), file=sys.stderr)
        return report["exit"]
    # ASCII output: a legacy console encoding cannot fail on a non-ASCII home path.
    print(json.dumps({**result, **hook_report()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
