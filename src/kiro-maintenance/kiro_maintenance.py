#!/usr/bin/env python3
"""Portable Kiro workaround maintenance with read-only defaults and guarded writes.

Configure paths with KIRO_FIX_DIR, CREW_VENV, KIRO_APP, and LLAMA_FIX_DIR,
or their matching command-line options. Run ``status`` or ``check`` to inspect,
``backup-cli`` or ``refresh-cli --candidate PATH`` for the CLI working copy,
``reapply-crew`` for the launcher override, and explicit ``lock``/``unlock``
commands on macOS with ``--acknowledge-file-flags``.
"""

import argparse
import ast
import datetime
import hashlib
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


MIN_CLI_BYTES = 50 * 1024 * 1024
CLI_NAME = "kiro-cli-chat"
CLI_SHIM_NAME = "kiro_stream_shim.dylib"
CLI_WRAPPER_NAMES = ("kiro-cli-chat-wrapper.sh", "kiro-cli-chat-wrapper")
CREW_LAUNCHER_NAME = "kirocrew"
CREW_IMPORT_ANCHOR = "from kiro_crew._bootstrap import main"
LLAMA_ENV_NAME = "LLAMA_CPP_LIB_PATH"
REQUIRED_LLAMA_FILES = (
    "libcxx-bigsur-shim.dylib",
    "libaccel-bigsur-shim.dylib",
    "libggml-base.0.dylib",
    "libggml-blas.0.dylib",
    "libggml-cpu.0.dylib",
    "libggml-metal.0.dylib",
    "libggml.0.dylib",
    "libllama.dylib",
)
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce": "Mach-O 32-bit",
    b"\xce\xfa\xed\xfe": "Mach-O 32-bit",
    b"\xfe\xed\xfa\xcf": "Mach-O 64-bit",
    b"\xcf\xfa\xed\xfe": "Mach-O 64-bit",
    b"\xca\xfe\xba\xbe": "Mach-O universal",
    b"\xbe\xba\xfe\xca": "Mach-O universal",
    b"\xca\xfe\xba\xbf": "Mach-O universal 64-bit",
    b"\xbf\xba\xfe\xca": "Mach-O universal 64-bit",
}
VERSION_PATTERN = re.compile(r"^kiro-cli-chat ([0-9]+(?:\.[0-9]+)+)$")


class MaintenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    kiro_fix_dir: Optional[Path]
    crew_venv: Optional[Path]
    kiro_app: Optional[Path]
    llama_fix_dir: Optional[Path]

    @property
    def cli_path(self) -> Optional[Path]:
        return self.kiro_fix_dir / CLI_NAME if self.kiro_fix_dir else None

    @property
    def cli_shim_path(self) -> Optional[Path]:
        return self.kiro_fix_dir / CLI_SHIM_NAME if self.kiro_fix_dir else None

    @property
    def cli_backup_dir(self) -> Optional[Path]:
        return self.kiro_fix_dir / "backups" if self.kiro_fix_dir else None

    @property
    def crew_launcher(self) -> Optional[Path]:
        return self.crew_venv / "bin" / CREW_LAUNCHER_NAME if self.crew_venv else None

    @property
    def crew_backup_dir(self) -> Optional[Path]:
        return self.crew_venv / ".kiro-maintenance-backups" if self.crew_venv else None


@dataclass(frozen=True)
class VersionResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)


def configured_path(value: Optional[str], require_absolute: bool = False) -> Optional[Path]:
    if not value:
        return None
    path = Path(value).expanduser()
    if require_absolute and not path.is_absolute():
        raise MaintenanceError("path must be absolute: {}".format(value))
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        configured_path(args.kiro_fix_dir),
        configured_path(args.crew_venv),
        configured_path(args.kiro_app),
        configured_path(args.llama_fix_dir, require_absolute=True),
    )


def require_path(path: Optional[Path], name: str) -> Path:
    if path is None:
        raise MaintenanceError("{} is not configured".format(name))
    return path


def lstat_path(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise MaintenanceError("cannot inspect {}: {}".format(path, exc))


def is_immutable(stats: os.stat_result) -> bool:
    flags = getattr(stats, "st_flags", 0)
    immutable = getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "SF_IMMUTABLE", 0)
    return bool(flags & immutable)


def flag_text(stats: os.stat_result) -> str:
    if not hasattr(stats, "st_flags"):
        return "n/a"
    value = stats.st_flags
    names = []
    for name in (
        "UF_NODUMP",
        "UF_IMMUTABLE",
        "UF_APPEND",
        "UF_OPAQUE",
        "UF_HIDDEN",
        "SF_ARCHIVED",
        "SF_IMMUTABLE",
        "SF_APPEND",
    ):
        bit = getattr(stat, name, 0)
        if bit and value & bit:
            names.append(name)
    suffix = ",".join(names) if names else "none"
    return "0x{:x} ({})".format(value, suffix)


def file_kind(path: Path, stats: Optional[os.stat_result] = None) -> str:
    if stats is None:
        try:
            stats = path.lstat()
        except OSError:
            return "missing"
    mode = stats.st_mode
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if not stat.S_ISREG(mode):
        return "special"
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return "regular (unreadable)"
    if head.startswith(b"#!"):
        return "script"
    return MACHO_MAGICS.get(head, "regular")


def describe_path(label: str, path: Optional[Path]) -> None:
    if path is None:
        print("{}: not configured".format(label))
        return
    try:
        stats = path.lstat()
    except OSError as exc:
        print("{}: {} [missing: {}]".format(label, path, exc))
        return
    executable = bool(stats.st_mode & 0o111)
    print(
        "{}: {} [type={}, size={}, executable={}, flags={}]".format(
            label,
            path,
            file_kind(path, stats),
            stats.st_size,
            "yes" if executable else "no",
            flag_text(stats),
        )
    )


def ensure_plain_regular(path: Path, label: str, executable: bool = False) -> os.stat_result:
    stats = lstat_path(path)
    if stat.S_ISLNK(stats.st_mode):
        raise MaintenanceError("{} must not be a symlink: {}".format(label, path))
    if not stat.S_ISREG(stats.st_mode):
        raise MaintenanceError("{} must be a regular file: {}".format(label, path))
    if executable and not os.access(str(path), os.X_OK):
        raise MaintenanceError("{} is not executable: {}".format(label, path))
    return stats


def validate_cli_file(path: Path, label: str) -> os.stat_result:
    stats = ensure_plain_regular(path, label, executable=True)
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError as exc:
        raise MaintenanceError("cannot read {}: {}".format(path, exc))
    if head.startswith(b"#!"):
        raise MaintenanceError("{} is a script, not the real CLI: {}".format(label, path))
    if head not in MACHO_MAGICS:
        raise MaintenanceError("{} is not a recognized Mach-O binary: {}".format(label, path))
    if stats.st_size < MIN_CLI_BYTES:
        raise MaintenanceError(
            "{} is too small ({} bytes; minimum {}): {}".format(
                label, stats.st_size, MIN_CLI_BYTES, path
            )
        )
    return stats


def validate_macho_file(path: Path, label: str) -> os.stat_result:
    stats = ensure_plain_regular(path, label)
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError as exc:
        raise MaintenanceError("cannot read {}: {}".format(path, exc))
    if head not in MACHO_MAGICS:
        raise MaintenanceError("{} is not a recognized Mach-O file: {}".format(label, path))
    return stats


def ensure_owned_mutable(path: Path, label: str) -> os.stat_result:
    stats = lstat_path(path)
    if stat.S_ISLNK(stats.st_mode):
        raise MaintenanceError("{} must not be a symlink: {}".format(label, path))
    if stats.st_uid != os.geteuid():
        raise MaintenanceError(
            "{} is owned by uid {}, not current uid {}: {}".format(
                label, stats.st_uid, os.geteuid(), path
            )
        )
    if is_immutable(stats):
        raise MaintenanceError("{} is immutable; unlock it first: {}".format(label, path))
    return stats


def ensure_writable_directory(path: Path, label: str, create: bool = False) -> None:
    if path.exists() or path.is_symlink():
        stats = lstat_path(path)
        if stat.S_ISLNK(stats.st_mode):
            raise MaintenanceError("{} must not be a symlink: {}".format(label, path))
        if not stat.S_ISDIR(stats.st_mode):
            raise MaintenanceError("{} is not a directory: {}".format(label, path))
        if stats.st_uid != os.geteuid():
            raise MaintenanceError("{} is not owned by the current user: {}".format(label, path))
        if is_immutable(stats):
            raise MaintenanceError("{} is immutable: {}".format(label, path))
    elif create:
        parent = path.parent
        ensure_writable_directory(parent, "parent of {}".format(label), create=False)
        try:
            path.mkdir()
        except OSError as exc:
            raise MaintenanceError("cannot create {} {}: {}".format(label, path, exc))
    else:
        raise MaintenanceError("{} does not exist: {}".format(label, path))
    if not os.access(str(path), os.W_OK | os.X_OK):
        raise MaintenanceError("{} is not writable/searchable: {}".format(label, path))


def preflight_output_directory(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        ensure_writable_directory(path, label)
    else:
        ensure_writable_directory(path.parent, "parent of {}".format(label))


def fingerprint(stats: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        stats.st_dev,
        stats.st_ino,
        stats.st_size,
        getattr(stats, "st_mtime_ns", int(stats.st_mtime * 1000000000)),
        getattr(stats, "st_ctime_ns", int(stats.st_ctime * 1000000000)),
    )


def ensure_unchanged(path: Path, original: Tuple[int, int, int, int, int], label: str) -> None:
    current = lstat_path(path)
    if fingerprint(current) != original:
        raise MaintenanceError("{} changed during validation: {}".format(label, path))


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise MaintenanceError("cannot hash {}: {}".format(path, exc))
    return digest.hexdigest()


def fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError as exc:
        raise MaintenanceError("cannot sync {}: {}".format(path, exc))


def fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def unique_backup_path(directory: Path, source: Path) -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = "{}.{}".format(source.name, stamp)
    candidate = directory / base
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = directory / "{}.{}".format(base, counter)
        counter += 1
    return candidate


def copy_to_backup(source: Path, directory: Path) -> Path:
    ensure_writable_directory(directory, "backup directory", create=True)
    destination = unique_backup_path(directory, source)
    descriptor, temp_name = tempfile.mkstemp(prefix=".backup-", dir=str(directory))
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        os.chmod(str(temporary), stat.S_IMODE(source.lstat().st_mode))
        fsync_file(temporary)
        os.replace(str(temporary), str(destination))
        fsync_directory(directory)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    if file_digest(source) != file_digest(destination):
        raise MaintenanceError("backup content mismatch: {}".format(destination))
    return destination


def stage_copy(source: Path, directory: Path, prefix: str) -> Path:
    descriptor, temp_name = tempfile.mkstemp(prefix=prefix, dir=str(directory))
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        os.chmod(str(temporary), stat.S_IMODE(source.lstat().st_mode))
        fsync_file(temporary)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return temporary


def run_version(binary: Path, shim: Path, timeout: float) -> VersionResult:
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("DYLD_"):
            del environment[name]
    environment["DYLD_FORCE_FLAT_NAMESPACE"] = "1"
    environment["DYLD_INSERT_LIBRARIES"] = str(shim)
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise MaintenanceError("{} --version timed out after {} seconds".format(binary, timeout))
    except OSError as exc:
        raise MaintenanceError("cannot run {} --version: {}".format(binary, exc))
    return VersionResult(completed.returncode, completed.stdout, completed.stderr)


def validate_version(result: VersionResult, label: str, expected: Optional[str]) -> str:
    if result.returncode != 0:
        raise MaintenanceError(
            "{} --version failed with return code {}: {}".format(
                label, result.returncode, result.combined or "no output"
            )
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    match = VERSION_PATTERN.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None:
        raise MaintenanceError(
            "{} stdout must contain exactly one 'kiro-cli-chat <version>' line: {}".format(
                label, result.stdout.strip() or "no stdout"
            )
        )
    version = match.group(1)
    if expected and expected != version:
        raise MaintenanceError(
            "{} version {!r} does not exactly match expected {!r}".format(
                label, version, expected
            )
        )
    return version


def restore_from_backup(backup: Path, destination: Path) -> None:
    rollback = stage_copy(backup, destination.parent, ".rollback-")
    backup_digest = file_digest(backup)
    try:
        os.replace(str(rollback), str(destination))
        fsync_directory(destination.parent)
    except Exception:
        try:
            rollback.unlink()
        except OSError:
            pass
        raise
    if file_digest(destination) != backup_digest:
        raise MaintenanceError("rollback content does not match retained backup {}".format(backup))


def backup_cli(config: Config) -> int:
    cli = require_path(config.cli_path, "KIRO_FIX_DIR")
    backup_dir = require_path(config.cli_backup_dir, "KIRO_FIX_DIR")
    validate_cli_file(cli, "CLI working copy")
    preflight_output_directory(backup_dir, "CLI backup directory")
    backup = copy_to_backup(cli, backup_dir)
    print("CLI backup created: {}".format(backup))
    return 0


def refresh_cli(config: Config, candidate: Path, expected: Optional[str], timeout: float) -> int:
    cli = require_path(config.cli_path, "KIRO_FIX_DIR")
    shim = require_path(config.cli_shim_path, "KIRO_FIX_DIR")
    backup_dir = require_path(config.cli_backup_dir, "KIRO_FIX_DIR")
    validate_macho_file(shim, "CLI shim")
    current_stats = validate_cli_file(cli, "CLI working copy")
    candidate_stats = validate_cli_file(candidate, "CLI candidate")
    ensure_owned_mutable(cli, "CLI working copy")
    ensure_owned_mutable(candidate, "CLI candidate")
    ensure_writable_directory(cli.parent, "CLI working directory")
    preflight_output_directory(backup_dir, "CLI backup directory")
    if os.path.samefile(str(candidate), str(cli)):
        raise MaintenanceError("CLI candidate and destination are the same file")
    current_fingerprint = fingerprint(current_stats)
    candidate_fingerprint = fingerprint(candidate_stats)
    candidate_digest = file_digest(candidate)
    staged = stage_copy(candidate, cli.parent, ".candidate-")
    backup = None
    replaced = False
    try:
        if file_digest(staged) != candidate_digest:
            raise MaintenanceError("staged CLI does not match candidate")
        ensure_unchanged(candidate, candidate_fingerprint, "CLI candidate")
        staged_result = run_version(staged, shim, timeout)
        staged_version = validate_version(staged_result, "candidate under configured shim", expected)
        ensure_unchanged(candidate, candidate_fingerprint, "CLI candidate")
        if file_digest(candidate) != candidate_digest:
            raise MaintenanceError("CLI candidate content changed during validation")
        backup = copy_to_backup(cli, backup_dir)
        if file_digest(backup) != file_digest(cli):
            raise MaintenanceError("retained backup does not match current CLI")
        ensure_unchanged(cli, current_fingerprint, "CLI working copy")
        ensure_owned_mutable(cli, "CLI working copy")
        os.replace(str(staged), str(cli))
        replaced = True
        fsync_directory(cli.parent)
        staged = None
        if file_digest(cli) != candidate_digest:
            raise MaintenanceError("final CLI content does not match validated candidate")
        final_result = run_version(cli, shim, timeout)
        final_version = validate_version(final_result, "installed CLI under configured shim", expected)
        if final_version != staged_version:
            raise MaintenanceError("installed CLI version differs from validated candidate")
        if final_result != staged_result:
            raise MaintenanceError("installed CLI --version result differs from validated candidate")
    except Exception as exc:
        if backup is not None and replaced:
            try:
                restore_from_backup(backup, cli)
            except Exception as rollback_exc:
                raise MaintenanceError(
                    "refresh failed ({}) and rollback failed ({}); retained backup: {}".format(
                        exc, rollback_exc, backup
                    )
                )
            raise MaintenanceError(
                "refresh failed and previous CLI was restored; retained backup: {}; cause: {}".format(
                    backup, exc
                )
            )
        if backup is not None:
            raise MaintenanceError(
                "refresh failed before replacement; working copy unchanged and backup retained: {}; cause: {}".format(
                    backup, exc
                )
            )
        if isinstance(exc, MaintenanceError):
            raise
        raise MaintenanceError("refresh failed before replacement: {}".format(exc))
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass
    print("CLI refreshed atomically from: {}".format(candidate))
    print("Validated --version return code: {}".format(staged_result.returncode))
    print("Validated version: {}".format(staged_version))
    print("Retained previous CLI backup: {}".format(backup))
    print("Final SHA-256: {}".format(candidate_digest))
    return 0


def discover_crew_package(venv: Path) -> Tuple[Optional[Path], Optional[str]]:
    pattern = venv / "lib" / "python*" / "site-packages" / "kiro_crew"
    matches = sorted(Path(item) for item in pattern.parent.parent.parent.glob("python*/site-packages/kiro_crew"))
    real_matches = []
    for path in matches:
        try:
            stats = path.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(stats.st_mode) and not stat.S_ISLNK(stats.st_mode):
            real_matches.append(path)
    if not real_matches:
        return None, "no kiro_crew package found under {}".format(venv / "lib")
    if len(real_matches) != 1:
        return None, "multiple kiro_crew packages found: {}".format(
            ", ".join(str(path) for path in real_matches)
        )
    return real_matches[0], None


def discover_cli_wrapper(fix_dir: Path) -> Tuple[Optional[Path], Optional[str]]:
    matches = []
    for name in CLI_WRAPPER_NAMES:
        path = fix_dir / name
        if path.exists() or path.is_symlink():
            matches.append(path)
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "no CLI wrapper found (accepted names: {})".format(
            ", ".join(CLI_WRAPPER_NAMES)
        )
    return None, "multiple CLI wrappers found: {}".format(
        ", ".join(str(path) for path in matches)
    )


def validate_llama_payload(path: Path) -> None:
    if not path.is_absolute():
        raise MaintenanceError("LLAMA_FIX_DIR must be absolute: {}".format(path))
    stats = lstat_path(path)
    if stat.S_ISLNK(stats.st_mode) or not stat.S_ISDIR(stats.st_mode):
        raise MaintenanceError("LLAMA_FIX_DIR must be a real directory: {}".format(path))
    for name in REQUIRED_LLAMA_FILES:
        validate_macho_file(path / name, "required llama payload {}".format(name))


def override_line(llama_fix_dir: Path, newline: str) -> str:
    return 'os.environ.setdefault("{}", {!r}){}'.format(
        LLAMA_ENV_NAME, str(llama_fix_dir), newline
    )


def launcher_override_lines(text: str) -> List[Tuple[int, str]]:
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if LLAMA_ENV_NAME in line
    ]


def parsed_launcher_override(text: str) -> Tuple[List[Tuple[int, str]], List[int]]:
    try:
        tree = ast.parse(text, filename="<kirocrew launcher>")
    except SyntaxError as exc:
        raise MaintenanceError("Crew launcher is not valid Python: {}".format(exc))
    calls = []
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(alias.name == "os" and alias.asname is None for alias in node.names):
                imports.append(node.lineno)
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if call.keywords or len(call.args) != 2:
            continue
        function = call.func
        if not (
            isinstance(function, ast.Attribute)
            and function.attr == "setdefault"
            and isinstance(function.value, ast.Attribute)
            and function.value.attr == "environ"
            and isinstance(function.value.value, ast.Name)
            and function.value.value.id == "os"
        ):
            continue
        key, value = call.args
        if (
            isinstance(key, ast.Constant)
            and key.value == LLAMA_ENV_NAME
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            calls.append((node.lineno, value.value))
    return calls, imports


def prepare_launcher_text(text: str, llama_fix_dir: Path) -> Tuple[str, bool]:
    lines = text.splitlines(True)
    if not lines or not lines[0].startswith("#!"):
        raise MaintenanceError("Crew launcher must begin with a shebang")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    anchors = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == CREW_IMPORT_ANCHOR]
    if len(anchors) != 1:
        raise MaintenanceError(
            "Crew launcher must contain exactly one exact import anchor {!r}".format(
                CREW_IMPORT_ANCHOR
            )
        )
    anchor = anchors[0]
    calls, imports = parsed_launcher_override(text)
    textual = launcher_override_lines(text)
    if calls or textual:
        if (
            len(calls) == 1
            and len(textual) == 1
            and calls[0][1] == str(llama_fix_dir)
            and calls[0][0] < anchor + 1
            and any(line <= calls[0][0] for line in imports)
        ):
            return text, False
        raise MaintenanceError("Crew launcher contains a conflicting or unknown LLAMA override")
    insertion = override_line(llama_fix_dir, newline)
    if not any(line <= anchor + 1 for line in imports):
        insertion = "import os{}{}".format(newline, insertion)
    lines.insert(anchor, insertion)
    updated = "".join(lines)
    if updated.splitlines(True)[0] != lines[0]:
        raise MaintenanceError("Crew launcher shebang would change")
    if sum(1 for line in updated.splitlines() if line == CREW_IMPORT_ANCHOR) != 1:
        raise MaintenanceError("Crew launcher import anchor validation failed")
    if len(launcher_override_lines(updated)) != 1:
        raise MaintenanceError("Crew launcher override validation failed")
    calls, imports = parsed_launcher_override(updated)
    if len(calls) != 1 or calls[0][1] != str(llama_fix_dir):
        raise MaintenanceError("Crew launcher override validation failed")
    return updated, True


def read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MaintenanceError("cannot read {} as UTF-8: {}".format(label, exc))


def atomic_write_text(path: Path, text: str, source_stats: os.stat_result) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=".launcher-", dir=str(path.parent))
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), stat.S_IMODE(source_stats.st_mode))
        os.replace(str(temporary), str(path))
        fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def reapply_crew(config: Config) -> int:
    launcher = require_path(config.crew_launcher, "CREW_VENV")
    backup_dir = require_path(config.crew_backup_dir, "CREW_VENV")
    llama_fix_dir = require_path(config.llama_fix_dir, "LLAMA_FIX_DIR")
    validate_llama_payload(llama_fix_dir)
    launcher_stats = ensure_plain_regular(launcher, "Crew launcher", executable=True)
    original = read_utf8(launcher, "Crew launcher")
    updated, changed = prepare_launcher_text(original, llama_fix_dir)
    if not changed:
        print("Crew launcher already has the exact configured LLAMA override: {}".format(launcher))
        return 0
    ensure_owned_mutable(launcher, "Crew launcher")
    ensure_writable_directory(launcher.parent, "Crew launcher directory")
    preflight_output_directory(backup_dir, "Crew backup directory")
    original_fingerprint = fingerprint(launcher_stats)
    backup = copy_to_backup(launcher, backup_dir)
    replaced = False
    try:
        ensure_unchanged(launcher, original_fingerprint, "Crew launcher")
        ensure_owned_mutable(launcher, "Crew launcher")
        atomic_write_text(launcher, updated, launcher_stats)
        replaced = True
        final = read_utf8(launcher, "updated Crew launcher")
        if final != updated:
            raise MaintenanceError("final Crew launcher does not match validated content")
        compile(final, str(launcher), "exec")
    except Exception as exc:
        if replaced:
            try:
                restore_from_backup(backup, launcher)
            except Exception as rollback_exc:
                raise MaintenanceError(
                    "launcher update failed ({}) and rollback failed ({}); retained backup: {}".format(
                        exc, rollback_exc, backup
                    )
                )
            raise MaintenanceError(
                "launcher update failed and prior launcher was restored; retained backup: {}; cause: {}".format(
                    backup, exc
                )
            )
        raise MaintenanceError(
            "launcher update failed before replacement; launcher unchanged and backup retained: {}; cause: {}".format(
                backup, exc
            )
        )
    print("Crew launcher override applied atomically: {}".format(launcher))
    print("Retained launcher backup: {}".format(backup))
    print("Gateway was not started or restarted.")
    return 0


def app_metadata(app: Path) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    plist = app / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as handle:
            value = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        return None, "cannot read {}: {}".format(plist, exc)
    if not isinstance(value, dict):
        return None, "{} is not a plist dictionary".format(plist)
    return value, None


def app_executable(app: Path, metadata: Dict[str, object]) -> Tuple[Optional[Path], Optional[str]]:
    name = metadata.get("CFBundleExecutable")
    if not isinstance(name, str) or not name or Path(name).name != name:
        return None, "CFBundleExecutable is missing or unsafe"
    return app / "Contents" / "MacOS" / name, None


def show_app_metadata(config: Config) -> None:
    if config.kiro_app is None:
        print("app.minimum-system-version: not configured")
        return
    metadata, error = app_metadata(config.kiro_app)
    if error:
        print("app.minimum-system-version: unavailable ({})".format(error))
        return
    print("app.minimum-system-version: {}".format(metadata.get("LSMinimumSystemVersion", "not declared")))
    executable, executable_error = app_executable(config.kiro_app, metadata)
    if executable_error:
        print("app.executable: unavailable ({})".format(executable_error))
    else:
        describe_path("app.executable", executable)


def show_launcher_override(config: Config) -> None:
    launcher = config.crew_launcher
    if launcher is None:
        print("crew.actual-llama-override: not configured")
        return
    try:
        text = read_utf8(launcher, "Crew launcher")
    except MaintenanceError as exc:
        print("crew.actual-llama-override: unavailable ({})".format(exc))
        return
    entries = launcher_override_lines(text)
    if not entries:
        print("crew.actual-llama-override: absent")
        return
    for line_number, line in entries:
        print("crew.actual-llama-override: line {}: {}".format(line_number, line.strip()))


def status(config: Config) -> int:
    print("Configured paths")
    describe_path("kiro.fix-dir", config.kiro_fix_dir)
    describe_path("crew.venv", config.crew_venv)
    describe_path("kiro.app", config.kiro_app)
    describe_path("llama.fix-dir", config.llama_fix_dir)
    print("CLI payload")
    describe_path("cli.working-copy", config.cli_path)
    describe_path("cli.actual-shim", config.cli_shim_path)
    if config.kiro_fix_dir:
        wrapper, wrapper_error = discover_cli_wrapper(config.kiro_fix_dir)
        if wrapper_error:
            print("cli.wrapper: unavailable ({})".format(wrapper_error))
        else:
            describe_path("cli.wrapper", wrapper)
    else:
        describe_path("cli.wrapper", None)
    describe_path("cli.backups", config.cli_backup_dir)
    print("Crew payload")
    describe_path("crew.launcher", config.crew_launcher)
    if config.crew_venv:
        package, package_error = discover_crew_package(config.crew_venv)
        if package_error:
            print("crew.package: unavailable ({})".format(package_error))
        else:
            describe_path("crew.package", package)
    else:
        describe_path("crew.package", None)
    for name in REQUIRED_LLAMA_FILES:
        describe_path(
            "llama.required.{}".format(name),
            config.llama_fix_dir / name if config.llama_fix_dir else None,
        )
    show_launcher_override(config)
    show_app_metadata(config)
    return 0


def show_otool_dependencies(path: Path, otool: str, issues: List[str]) -> None:
    try:
        completed = subprocess.run(
            [otool, "-L", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        issues.append("cannot inspect dependencies for {}: {}".format(path, exc))
        return
    if completed.returncode != 0:
        issues.append(
            "otool -L failed for {}: {}".format(
                path, completed.stderr.strip() or "return code {}".format(completed.returncode)
            )
        )
        return
    print("dependencies: {}".format(path))
    dependencies = completed.stdout.splitlines()[1:]
    for line in dependencies:
        if line.strip():
            print("  {}".format(line.strip()))
    expected = {
        "libllama.dylib": "libcxx-bigsur-shim.dylib",
        "libggml-blas.0.dylib": "libaccel-bigsur-shim.dylib",
    }.get(path.name)
    if expected and not any(
        Path(line.strip().split(" (", 1)[0]).name == expected
        for line in dependencies
    ):
        issues.append("{} does not depend on {}".format(path, expected))


def check(config: Config) -> int:
    status(config)
    issues = []
    configured = False
    if config.kiro_fix_dir:
        configured = True
        try:
            stats = lstat_path(config.kiro_fix_dir)
            if stat.S_ISLNK(stats.st_mode) or not stat.S_ISDIR(stats.st_mode):
                raise MaintenanceError("KIRO_FIX_DIR must be a real directory")
            validate_cli_file(require_path(config.cli_path, "KIRO_FIX_DIR"), "CLI working copy")
            validate_macho_file(require_path(config.cli_shim_path, "KIRO_FIX_DIR"), "CLI shim")
            wrapper, wrapper_error = discover_cli_wrapper(config.kiro_fix_dir)
            if wrapper_error:
                raise MaintenanceError(wrapper_error)
            ensure_plain_regular(require_path(wrapper, "CLI wrapper"), "CLI wrapper", executable=True)
        except MaintenanceError as exc:
            issues.append(str(exc))
    if config.crew_venv:
        configured = True
        try:
            launcher = require_path(config.crew_launcher, "CREW_VENV")
            ensure_plain_regular(launcher, "Crew launcher", executable=True)
            package, package_error = discover_crew_package(config.crew_venv)
            if package_error:
                raise MaintenanceError(package_error)
            text = read_utf8(launcher, "Crew launcher")
            anchors = sum(1 for line in text.splitlines() if line == CREW_IMPORT_ANCHOR)
            if anchors != 1:
                raise MaintenanceError("Crew launcher does not have exactly one exact import anchor")
            if config.llama_fix_dir:
                unused, changed = prepare_launcher_text(text, config.llama_fix_dir)
                if changed:
                    raise MaintenanceError("Crew launcher LLAMA override is absent")
        except MaintenanceError as exc:
            issues.append(str(exc))
    if config.llama_fix_dir:
        configured = True
        try:
            validate_llama_payload(config.llama_fix_dir)
        except MaintenanceError as exc:
            issues.append(str(exc))
    if config.kiro_app:
        configured = True
        try:
            stats = lstat_path(config.kiro_app)
            if config.kiro_app.suffix != ".app" or stat.S_ISLNK(stats.st_mode) or not stat.S_ISDIR(stats.st_mode):
                raise MaintenanceError("KIRO_APP must be a real .app directory")
            metadata, metadata_error = app_metadata(config.kiro_app)
            if metadata_error:
                raise MaintenanceError(metadata_error)
            executable, executable_error = app_executable(config.kiro_app, metadata)
            if executable_error:
                raise MaintenanceError(executable_error)
            ensure_plain_regular(executable, "app executable", executable=True)
        except MaintenanceError as exc:
            issues.append(str(exc))
    if not configured:
        issues.append("no maintenance paths are configured")
    otool = shutil.which("otool") if platform.system() == "Darwin" else None
    if otool:
        dependency_targets = []
        if config.cli_shim_path and config.cli_shim_path.exists() and not config.cli_shim_path.is_symlink():
            dependency_targets.append(config.cli_shim_path)
        if config.llama_fix_dir:
            dependency_targets.extend(
                path
                for path in (config.llama_fix_dir / name for name in REQUIRED_LLAMA_FILES)
                if path.exists() and not path.is_symlink()
            )
        for target in dependency_targets:
            show_otool_dependencies(target, otool, issues)
    else:
        print("otool: unavailable; dependency inspection skipped")
    if issues:
        print("Check failed with {} issue(s):".format(len(issues)), file=sys.stderr)
        for issue in issues:
            print("- {}".format(issue), file=sys.stderr)
        return 1
    print("Check passed without launching Kiro, the gateway, or a model.")
    return 0


def has_symlink_component(path: Path) -> Optional[Path]:
    current = path
    while True:
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return current
        except OSError:
            return None
        if current.parent == current:
            return None
        current = current.parent


def lock_targets(config: Config) -> Tuple[List[Tuple[str, Path, bool]], List[str]]:
    targets = []
    errors = []
    if config.kiro_fix_dir:
        wrapper, wrapper_error = discover_cli_wrapper(config.kiro_fix_dir)
        if wrapper_error:
            errors.append(wrapper_error)
        targets.extend(
            (
                ("CLI working copy", require_path(config.cli_path, "KIRO_FIX_DIR"), False),
                ("CLI shim", require_path(config.cli_shim_path, "KIRO_FIX_DIR"), False),
            )
        )
        if wrapper:
            targets.append(("CLI wrapper", wrapper, False))
    if config.crew_venv:
        targets.append(("Crew launcher", require_path(config.crew_launcher, "CREW_VENV"), False))
        package, package_error = discover_crew_package(config.crew_venv)
        if package_error:
            errors.append(package_error)
        elif package:
            targets.append(("Crew package", package, True))
    if config.llama_fix_dir:
        targets.append(("llama payload", config.llama_fix_dir, True))
    if config.kiro_app:
        if config.kiro_app.suffix != ".app":
            errors.append("KIRO_APP must end in .app: {}".format(config.kiro_app))
        targets.append(("Kiro app bundle", config.kiro_app, True))
    forbidden = set()
    if config.kiro_fix_dir:
        forbidden.add(str(config.kiro_fix_dir))
        forbidden.add(str(config.cli_backup_dir))
    if config.crew_backup_dir:
        forbidden.add(str(config.crew_backup_dir))
    unique = []
    seen = set()
    for label, path, recursive in targets:
        key = str(path)
        inside_forbidden = any(
            recursive and os.path.commonpath((key, protected)) in (key, protected)
            for protected in forbidden
        )
        if key in forbidden or inside_forbidden:
            errors.append("refusing to flag protected root or backup directory: {}".format(path))
            continue
        if key not in seen:
            unique.append((label, path, recursive))
            seen.add(key)
    return unique, errors


def physical_tree(label: str, root: Path) -> Tuple[List[Tuple[str, Path, os.stat_result]], List[Path]]:
    found = []
    skipped = []
    pending = [(label, root)]
    while pending:
        item_label, path = pending.pop()
        try:
            stats = path.lstat()
        except OSError as exc:
            raise MaintenanceError("cannot inspect {}: {}".format(path, exc))
        if stat.S_ISLNK(stats.st_mode):
            skipped.append(path)
            continue
        if not stat.S_ISREG(stats.st_mode) and not stat.S_ISDIR(stats.st_mode):
            raise MaintenanceError("unsupported special file in {}: {}".format(label, path))
        found.append((item_label, path, stats))
        if stat.S_ISDIR(stats.st_mode):
            try:
                with os.scandir(str(path)) as scan:
                    entries = sorted(scan, key=lambda entry: entry.name, reverse=True)
            except OSError as exc:
                raise MaintenanceError("cannot enumerate {}: {}".format(path, exc))
            pending.extend((label, Path(entry.path)) for entry in entries)
    return found, skipped


def change_lock_state(config: Config, lock: bool, acknowledged: bool) -> int:
    action = "lock" if lock else "unlock"
    if platform.system() != "Darwin":
        raise MaintenanceError("{} is supported only on macOS".format(action))
    if not acknowledged:
        raise MaintenanceError("{} requires --acknowledge-file-flags".format(action))
    if not hasattr(os, "chflags") or not getattr(stat, "UF_IMMUTABLE", 0):
        raise MaintenanceError("this Python runtime does not provide macOS user file flags")
    missing = [
        name
        for name, value in (
            ("KIRO_FIX_DIR", config.kiro_fix_dir),
            ("CREW_VENV", config.crew_venv),
            ("LLAMA_FIX_DIR", config.llama_fix_dir),
        )
        if value is None
    ]
    if missing:
        raise MaintenanceError("{} requires complete core configuration: {}".format(action, ", ".join(missing)))
    validate_cli_file(require_path(config.cli_path, "KIRO_FIX_DIR"), "CLI working copy")
    validate_macho_file(require_path(config.cli_shim_path, "KIRO_FIX_DIR"), "CLI shim")
    validate_llama_payload(require_path(config.llama_fix_dir, "LLAMA_FIX_DIR"))
    targets, errors = lock_targets(config)
    if not targets:
        errors.append("no lock targets are configured")
    preflight = []
    skipped = []
    for label, path, recursive in targets:
        try:
            symlink = has_symlink_component(path)
            if symlink:
                raise MaintenanceError("symlink component is not followed: {}".format(symlink))
            entries, ignored = physical_tree(label, path) if recursive else (
                [(label, path, lstat_path(path))], []
            )
            skipped.extend(ignored)
            for entry_label, entry_path, stats in entries:
                if stats.st_uid != os.geteuid():
                    raise MaintenanceError(
                        "{} is owned by uid {}, not current uid {}".format(
                            entry_path, stats.st_uid, os.geteuid()
                        )
                    )
                preflight.append((entry_label, entry_path, stats))
        except MaintenanceError as exc:
            errors.append("{} {}: {}".format(label, path, exc))
    if errors:
        print("{} preflight failed; no flags were changed:".format(action), file=sys.stderr)
        for error in errors:
            print("- {}".format(error), file=sys.stderr)
        return 1
    for path in skipped:
        print("skipped symlink without following: {}".format(path))
    preflight.sort(key=lambda item: len(item[1].parts), reverse=lock)
    failures = []
    changed = 0
    immutable = stat.UF_IMMUTABLE
    for label, path, stats in preflight:
        old_flags = stats.st_flags
        new_flags = old_flags | immutable if lock else old_flags & ~immutable
        if new_flags == old_flags:
            print("{} unchanged: {} [{}]".format(label, path, flag_text(stats)))
            continue
        try:
            os.chflags(str(path), new_flags, follow_symlinks=False)
            after = path.lstat()
            if after.st_flags != new_flags:
                raise OSError("resulting flags differ from requested flags")
            changed += 1
            print("{} {}: {} [{}]".format(action, label, path, flag_text(after)))
        except OSError as exc:
            failures.append("{} {}: {}".format(label, path, exc))
    if failures:
        print(
            "{} completed partially: {} changed, {} failed:".format(action, changed, len(failures)),
            file=sys.stderr,
        )
        for failure in failures:
            print("- {}".format(failure), file=sys.stderr)
        return 1
    print(
        "{} complete: {} physical target(s) changed; {} symlink(s) skipped.".format(
            action, changed, len(skipped)
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and safely maintain local Kiro CLI/Crew workaround payloads.",
        epilog=(
            "Path options precede the command. With no command, status is used. "
            "status/check are read-only and never launch Kiro, the gateway, or a model."
        ),
    )
    parser.add_argument("--kiro-fix-dir", default=os.environ.get("KIRO_FIX_DIR"))
    parser.add_argument("--crew-venv", default=os.environ.get("CREW_VENV"))
    parser.add_argument("--kiro-app", default=os.environ.get("KIRO_APP"))
    parser.add_argument("--llama-fix-dir", default=os.environ.get("LLAMA_FIX_DIR"))
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="show paths, file types, flags, payloads, and override")
    subparsers.add_parser("check", help="validate configured payloads without launching them")
    subparsers.add_parser("backup-cli", help="retain a new backup of the current CLI working copy")
    refresh = subparsers.add_parser("refresh-cli", help="validate and atomically install a prepared CLI")
    refresh.add_argument("--candidate", required=True, help="separately prepared non-hardened CLI binary")
    refresh.add_argument("--expected-version", help="exact version expected after 'kiro-cli-chat '")
    refresh.add_argument("--timeout", type=float, default=30.0, help="--version timeout in seconds")
    subparsers.add_parser("reapply-crew", help="idempotently restore the launcher LLAMA override")
    lock_parser = subparsers.add_parser("lock", help="set macOS user-immutable flags on bounded payload trees")
    lock_parser.add_argument("--acknowledge-file-flags", action="store_true")
    unlock_parser = subparsers.add_parser("unlock", help="clear only user-immutable flags on bounded payload trees")
    unlock_parser.add_argument("--acknowledge-file-flags", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "status"
    try:
        config = config_from_args(args)
        if command == "status":
            return status(config)
        if command == "check":
            return check(config)
        if command == "backup-cli":
            return backup_cli(config)
        if command == "refresh-cli":
            if args.timeout <= 0:
                raise MaintenanceError("--timeout must be positive")
            candidate = configured_path(args.candidate)
            return refresh_cli(
                config,
                require_path(candidate, "--candidate"),
                args.expected_version,
                args.timeout,
            )
        if command == "reapply-crew":
            return reapply_crew(config)
        if command == "lock":
            return change_lock_state(config, True, args.acknowledge_file_flags)
        if command == "unlock":
            return change_lock_state(config, False, args.acknowledge_file_flags)
        parser.error("unknown command {}".format(command))
    except MaintenanceError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
