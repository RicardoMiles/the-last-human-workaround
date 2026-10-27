#!/usr/bin/env python3
"""A small, independent OpenCode v1.19.4 CLI bridge.
This module deliberately uses only the Python standard library.  The actual
OpenCode executable remains the process owner of the server; this program only
installs it and manages a per-user LaunchAgent.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import hashlib
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
VERSION = "1.19.4"
REPOSITORY = "anomalyco/opencode"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/v{VERSION}"
LABEL = "local.opencode.v1-bridge"
TEAM_IDENTIFIER = "5NZ4Q7NXJ4"
DEFAULT_PORT = 4096
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED = 600 * 1024 * 1024
MAX_ZIP_MEMBERS = 64
CLI_NAME = "opencode"


class BridgeError(RuntimeError):
    """A user-actionable bridge failure."""


def _runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("timeout", 30)
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False, **kwargs)


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return False


def _ensure_mode(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise BridgeError("port must be between 1 and 65535")


def _reject_symlink_components(path: Path) -> None:
    # macOS provides these two system aliases. User-controlled symlink parents
    # (including a redirected Library or LaunchAgents directory) are refused.
    aliases = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
    for component in (path, *path.parents):
        if component.is_symlink():
            if component in aliases and component.resolve() == aliases[component]:
                continue
            raise BridgeError("refusing a symlink in a managed path")


def _publish_file(source: Path, target: Path) -> None:
    """Publish a private staged file without overwriting an existing path."""
    os.link(source, target)


def _runtime_environment() -> dict[str, str]:
    # launchd does not source interactive shell profiles. Include common user
    # tool locations without importing arbitrary agent/process environment.
    return {
        "PATH": ":".join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                          "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        "SHELL": "/bin/zsh",
        "HOME": str(Path.home()),
    }


def _asset_name() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "opencode-darwin-arm64.zip"
    if machine in {"x86_64", "amd64"}:
        return "opencode-darwin-x64.zip"
    raise BridgeError(f"unsupported macOS architecture: {machine}")


def _expected_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    raise BridgeError(f"unsupported macOS architecture: {machine}")


def _urlopen(req: urllib.request.Request | str, timeout: float = 20):
    # Unauthenticated release downloads may use the user's proxy. Authenticated
    # health checks use a separate, proxy-free, redirect-free opener below.
    opener = urllib.request.build_opener()
    return opener.open(req, timeout=timeout)


class _NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _loopback_urlopen(req: urllib.request.Request | str, timeout: float = 2):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(req, timeout=timeout)


def fetch_release(opener: Callable[..., Any] = _urlopen) -> dict[str, Any]:
    request = urllib.request.Request(RELEASE_API, headers={"Accept": "application/vnd.github+json"})
    try:
        with opener(request, timeout=20) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_METADATA_BYTES:
                raise BridgeError("official release metadata exceeds the safety size limit")
            raw = response.read(MAX_METADATA_BYTES + 1)
            if len(raw) > MAX_METADATA_BYTES:
                raise BridgeError("official release metadata exceeds the safety size limit")
    except Exception as exc:  # urllib's error types vary between platforms
        raise BridgeError(f"could not read the official release metadata: {exc}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("official release metadata was not valid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        raise BridgeError("official release metadata did not contain assets")
    if result.get("tag_name") != f"v{VERSION}":
        raise BridgeError("official release metadata was not for v1.19.4")
    return result


def official_asset(release: Mapping[str, Any], name: str) -> tuple[str, str]:
    for item in release.get("assets", []):
        if isinstance(item, dict) and item.get("name") == name:
            url = item.get("browser_download_url")
            digest = item.get("digest")
            expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{name}"
            if url != expected_url:
                raise BridgeError("official asset download URL was not the expected release asset")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                raise BridgeError("official asset has no usable sha256 digest; refusing install")
            return url, digest.split(":", 1)[1].lower()
    raise BridgeError(f"official release has no {name} asset")


def download_asset(url: str, destination: Path,
                   opener: Callable[..., Any] = _urlopen) -> None:
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    try:
        with opener(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise BridgeError("download exceeds the safety size limit")
            total = 0
            with destination.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise BridgeError("download exceeds the safety size limit")
                    output.write(block)
    except BridgeError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise BridgeError(f"download failed: {exc}") from exc


def _zip_candidate(zip_path: Path) -> str:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise BridgeError("ZIP contains too many entries")
            total = 0
            candidates: list[str] = []
            for member in members:
                name = member.filename
                parts = Path(name).parts
                if not name or name.startswith("/") or ".." in parts:
                    raise BridgeError("ZIP contains a path traversal entry")
                mode = (member.external_attr >> 16) & 0o177777
                if stat.S_ISLNK(mode):
                    raise BridgeError("ZIP symlinks are not permitted")
                if not name.endswith("/"):
                    total += member.file_size
                    if total > MAX_ZIP_UNCOMPRESSED:
                        raise BridgeError("ZIP uncompressed size exceeds the safety limit")
                if Path(name).name == CLI_NAME and not name.endswith("/"):
                    candidates.append(name)
            if len(candidates) != 1:
                raise BridgeError("ZIP must contain exactly one opencode binary")
            return candidates[0]
    except zipfile.BadZipFile as exc:
        raise BridgeError("source is not a valid ZIP archive") from exc


def _run_checked(runner: Callable[..., subprocess.CompletedProcess[str]], argv: list[str],
                 label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(argv)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"{label} timed out") from exc
    except OSError as exc:
        raise BridgeError(f"{label} could not execute: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "failed").strip().splitlines()[0]
        raise BridgeError(f"{label} failed: {detail[:240]}")
    return result


def verify_binary(binary: Path, runner: Callable[..., subprocess.CompletedProcess[str]] = _runner) -> None:
    if _is_symlink(binary) or not binary.is_file():
        raise BridgeError("candidate CLI is missing or is a symlink")
    _run_checked(runner, ["/usr/bin/codesign", "--verify", "--strict", str(binary)], "codesign verification")
    details = _run_checked(runner, ["/usr/bin/codesign", "-dv", "--verbose=4", str(binary)], "codesign identity")
    match = re.search(r"TeamIdentifier=([^\s\r\n]+)", details.stderr or "")
    if not match or match.group(1) != TEAM_IDENTIFIER:
        raise BridgeError("candidate CLI is signed by an unexpected team")
    arch = _run_checked(runner, ["/usr/bin/file", str(binary)], "CLI architecture check").stdout.lower()
    if "mach-o" not in arch or _expected_arch() not in arch:
        raise BridgeError("candidate CLI is not a supported macOS Mach-O binary")
    version = _run_checked(runner, [str(binary), "--version"], "CLI version check")
    if (version.stdout or "").strip() != VERSION:
        raise BridgeError(f"candidate CLI did not report exactly {VERSION}")


def _write_json(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    _ensure_mode(temporary, mode)
    os.replace(temporary, path)


class Bridge:

    def __init__(self, support: Optional[Path] = None,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = _runner,
                 opener: Callable[..., Any] = _urlopen,
                 system: Callable[[], str] = platform.system,
                 health_opener: Callable[..., Any] = _loopback_urlopen):
        self.support = Path(os.path.abspath(Path(support or Path.home() / "Library/Application Support/OpenCodeV1Bridge").expanduser()))
        self.launch_agent = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        self.runner = runner
        self.opener = opener
        self.health_opener = health_opener
        self.system = system
        self.binary = self.support / "bin" / CLI_NAME
        self.manifest = self.support / "manifest.json"
        self.secret_file = self.support / "connection.secret"
        self.marker = self.support / ".v1-bridge"

    def _validate_paths(self) -> None:
        _reject_symlink_components(self.support)
        _reject_symlink_components(self.launch_agent)
        if _is_symlink(self.support) or (self.support.exists() and not self.support.is_dir()):
            raise BridgeError("support directory is foreign or a symlink")
        for path in (self.support / "bin", self.binary, self.manifest,
                     self.secret_file, self.marker, self.launch_agent):
            if _is_symlink(path):
                raise BridgeError(f"refusing symlink at {path}")
        if self.support.exists() and any(self.support.iterdir()) and not self.marker.exists():
            raise BridgeError("support directory contains unknown files; refusing to overwrite it")
        if self.marker.exists():
            try:
                marker_value = self.marker.read_text(encoding="ascii")
            except (OSError, UnicodeError) as exc:
                raise BridgeError("support marker is unreadable") from exc
            if not self.marker.is_file() or marker_value != "OpenCode V1 Bridge\n":
                raise BridgeError("support marker is foreign or invalid")
        if self.manifest.exists():
            try:
                manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise BridgeError("existing bridge manifest is foreign or invalid") from exc
            if not isinstance(manifest, dict) or manifest.get("label") != LABEL or manifest.get("version") != VERSION:
                raise BridgeError("existing bridge manifest is foreign or unsupported")

    def _load_manifest(self) -> dict[str, Any]:
        self._validate_paths()
        if not self.manifest.is_file():
            raise BridgeError("bridge is not installed")
        try:
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError("bridge manifest is unreadable") from exc
        if not isinstance(value, dict) or value.get("label") != LABEL or value.get("version") != VERSION:
            raise BridgeError("bridge manifest is foreign or unsupported")
        return value

    def _require_darwin(self) -> None:
        if self.system() != "Darwin":
            raise BridgeError("install and service operations are supported only on macOS (Darwin)")
        if os.getuid() == 0:
            raise BridgeError("run this per-user helper without sudo")

    def _secure_file(self, path: Path, mode: int, label: str) -> None:
        if _is_symlink(path) or not path.is_file():
            raise BridgeError(f"{label} is missing or is a symlink")
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
            raise BridgeError(f"{label} has unsafe owner or permissions")

    def _validate_plist(self, plist: Mapping[str, Any], manifest: Mapping[str, Any],
                        secret: str) -> None:
        # Reject extra Program, injection environment, sockets, or launch rules;
        # checking just ProgramArguments would miss launchd's Program override.
        if plist != self._plist(secret, manifest["port"]):
            raise BridgeError("LaunchAgent plist does not match the owned loopback bridge")

    def _validate_installation(self, require_darwin: bool = False,
                               check_binary: bool = True,
                               check_plist: bool = True) -> dict[str, Any]:
        if require_darwin:
            self._require_darwin()
        manifest = self._load_manifest()
        _safe_port(manifest.get("port"))
        if (manifest.get("created_by") != "opencode_v1_bridge.py" or
                not all(isinstance(manifest.get(k), str) and
                        re.fullmatch(r"[0-9a-f]{64}", manifest[k])
                        for k in ("zip_sha256", "binary_sha256"))):
            raise BridgeError("bridge manifest provenance is invalid")
        if (not self.support.is_dir() or self.support.stat().st_uid != os.getuid() or
                stat.S_IMODE(self.support.stat().st_mode) != 0o700):
            raise BridgeError("support directory has unsafe owner or permissions")
        bindir = self.support / "bin"
        if (_is_symlink(bindir) or not bindir.is_dir() or bindir.stat().st_uid != os.getuid() or
                stat.S_IMODE(bindir.stat().st_mode) != 0o700):
            raise BridgeError("support/bin has unsafe type or permissions")
        if check_binary or self.binary.exists():
            self._secure_file(self.binary, 0o700, "installed CLI")
        self._secure_file(self.manifest, 0o600, "manifest")
        self._secure_file(self.marker, 0o600, "ownership marker")
        self._secure_file(self.secret_file, 0o600, "connection secret")
        if self.marker.read_text(encoding="ascii") != "OpenCode V1 Bridge\n":
            raise BridgeError("ownership marker is invalid")
        secret = self.secret_file.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,}", secret):
            raise BridgeError("connection secret is invalid")
        if check_binary and _sha256(self.binary) != manifest.get("binary_sha256"):
            raise BridgeError("installed CLI digest does not match the manifest")
        expected_asset = _asset_name()
        expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{expected_asset}"
        if manifest.get("asset") != expected_asset or manifest.get("asset_url") != expected_url:
            raise BridgeError("installed CLI provenance does not match the official release")
        if check_plist:
            self._secure_file(self.launch_agent, 0o600, "LaunchAgent plist")
            try:
                with self.launch_agent.open("rb") as stream:
                    plist = plistlib.load(stream)
            except Exception as exc:
                raise BridgeError("LaunchAgent plist is unreadable") from exc
            self._validate_plist(plist, manifest, secret)
        return manifest

    def _existing_secret(self) -> str:
        if self.secret_file.exists():
            if not self.secret_file.is_file() or stat.S_IMODE(self.secret_file.stat().st_mode) != 0o600:
                raise BridgeError("existing connection secret has unsafe ownership or permissions")
            secret = self.secret_file.read_text(encoding="ascii").strip()
            if re.fullmatch(r"[A-Za-z0-9_-]{40,}", secret):
                return secret
            raise BridgeError("existing connection secret is invalid")
        return secrets.token_urlsafe(32)

    def install(self, source: Optional[Path] = None, download: bool = False,
                port: int = DEFAULT_PORT) -> dict[str, Any]:
        self._require_darwin()
        _safe_port(port)
        if (source is None) == (not download):
            raise BridgeError("choose exactly one of --source ZIP or --download")
        self._validate_paths()
        if self.support.exists() and not self.manifest.exists():
            raise BridgeError("support directory is incomplete or foreign; refusing install")
        if self.launch_agent.exists() and not self.manifest.exists():
            raise BridgeError("LaunchAgent plist exists without a complete owned install; refusing overwrite")
        if self.launch_agent.exists() and not self.launch_agent.is_file():
            raise BridgeError("LaunchAgent plist is foreign or a symlink")
        if not self.manifest.exists() and self._foreign_job():
            raise BridgeError("a job with this label is loaded without a complete owned installation")
        release = fetch_release(self.opener)
        asset_name = _asset_name()
        asset_url, expected_digest = official_asset(release, asset_name)
        if self.manifest.exists():
            try:
                existing = self._validate_installation(require_darwin=True,
                                                      check_plist=self.launch_agent.exists())
            except BridgeError:
                raise BridgeError("existing bridge is incomplete or unsafe; run rollback before reinstalling")
            if int(existing.get("port", DEFAULT_PORT)) != port:
                raise BridgeError("different port requires rollback before reinstall")
            if existing.get("asset") != asset_name or existing.get("zip_sha256") != expected_digest:
                raise BridgeError("existing bridge provenance differs; run rollback before reinstall")
            if source is not None:
                if _is_symlink(source) or not source.is_file():
                    raise BridgeError("source ZIP is missing or is a symlink")
                if source.stat().st_size > MAX_DOWNLOAD_BYTES:
                    raise BridgeError("source ZIP exceeds the safety size limit")
                if _sha256(source) != expected_digest:
                    raise BridgeError("ZIP sha256 does not match the official release digest")
            repaired = not self.launch_agent.exists()
            if repaired:
                # A complete support tree can survive interruption between its
                # atomic publication and publication of the LaunchAgent file.
                self._foreign_job()
                self._publish_plist(self._plist(self._existing_secret(), port))
            return {"installed": True, "unchanged": not repaired,
                    "repaired_launch_agent": repaired, "version": VERSION,
                    "port": port, "binary_sha256": existing["binary_sha256"],
                    "launch_agent": str(self.launch_agent)}
        secret = self._existing_secret()
        self.support.parent.mkdir(parents=True, exist_ok=True)
        published = False
        with tempfile.TemporaryDirectory(prefix=".v1-bridge-", dir=str(self.support.parent)) as temp:
            tempdir = Path(temp)
            archive = tempdir / asset_name
            if source is not None:
                if _is_symlink(source) or not source.is_file():
                    raise BridgeError("source ZIP is missing or is a symlink")
                if source.stat().st_size > MAX_DOWNLOAD_BYTES:
                    raise BridgeError("source ZIP exceeds the safety size limit")
                shutil.copyfile(source, archive)
            else:
                download_asset(asset_url, archive, self.opener)
            actual_digest = _sha256(archive)
            if actual_digest != expected_digest:
                raise BridgeError("ZIP sha256 does not match the official release digest")
            candidate_name = _zip_candidate(archive)
            candidate = tempdir / CLI_NAME
            with zipfile.ZipFile(archive) as zf, zf.open(candidate_name) as inp, candidate.open("wb") as out:
                shutil.copyfileobj(inp, out, 1024 * 1024)
            _ensure_mode(candidate, 0o700)
            verify_binary(candidate, self.runner)
            digest = _sha256(candidate)
            manifest = {"label": LABEL, "version": VERSION, "port": port,
                        "asset": asset_name, "asset_url": asset_url,
                        "zip_sha256": expected_digest, "binary_sha256": digest,
                        "created_by": "opencode_v1_bridge.py"}
            plist = self._plist(secret, port)
            staged_support = tempdir / "installation"
            staged_support.mkdir(mode=0o700)
            (staged_support / "bin").mkdir(mode=0o700)
            staged_binary = staged_support / "bin" / CLI_NAME
            shutil.copyfile(candidate, staged_binary)
            _ensure_mode(staged_binary, 0o700)
            staged_secret = staged_support / self.secret_file.name
            staged_secret.write_text(secret + "\n", encoding="ascii")
            _ensure_mode(staged_secret, 0o600)
            staged_marker = staged_support / self.marker.name
            staged_marker.write_text("OpenCode V1 Bridge\n", encoding="ascii")
            _ensure_mode(staged_marker, 0o600)
            staged_manifest = staged_support / self.manifest.name
            _write_json(staged_manifest, manifest, 0o600)
            try:
                self._validate_paths()
                if self.support.exists() or self.launch_agent.exists():
                    raise BridgeError("managed destination appeared during installation; refusing overwrite")
                # Publishing the complete directory avoids marker/manifest/
                # binary partial states. Its parent is on the same filesystem.
                os.rename(staged_support, self.support)
                published = True
                self._publish_plist(plist)
            except Exception:
                if published:
                    for path in (self.binary, self.secret_file, self.marker, self.manifest):
                        if path.is_file() and not path.is_symlink():
                            path.unlink()
                    bindir = self.support / "bin"
                    if bindir.is_dir() and not any(bindir.iterdir()):
                        bindir.rmdir()
                    if self.support.is_dir() and not any(self.support.iterdir()):
                        self.support.rmdir()
                raise
        return {"installed": True, "version": VERSION, "port": port,
                "binary_sha256": digest, "launch_agent": str(self.launch_agent)}

    def _publish_plist(self, plist: Mapping[str, Any]) -> None:
        _reject_symlink_components(self.launch_agent)
        self.launch_agent.parent.mkdir(parents=True, exist_ok=True)
        # Stage on the destination filesystem, mode 0600 from creation.
        fd, name = tempfile.mkstemp(prefix=".opencode-v1-", dir=str(self.launch_agent.parent))
        staged = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                plistlib.dump(plist, stream, sort_keys=True)
            _publish_file(staged, self.launch_agent)
        finally:
            staged.unlink(missing_ok=True)

    def _plist(self, secret: str, port: int) -> dict[str, Any]:
        return {"Label": LABEL,
                "ProgramArguments": [str(self.binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                "EnvironmentVariables": {**_runtime_environment(),
                                          "OPENCODE_SERVER_PASSWORD": secret,
                                          "OPENCODE_SERVER_USERNAME": "opencode",
                                          },
                "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30}

    def inspect(self, source: Optional[Path] = None) -> dict[str, Any]:
        self._validate_paths()
        result: dict[str, Any] = {"version": VERSION, "asset": _asset_name(), "label": LABEL,
                                  "os": self.system(), "arch": platform.machine(),
                                  "supported_host": self.system() == "Darwin",
                                  "gui_version": None}
        info_path = Path("/Applications/OpenCode.app/Contents/Info.plist")
        if self.system() == "Darwin" and info_path.is_file():
            try:
                with info_path.open("rb") as stream:
                    result["gui_version"] = plistlib.load(stream).get("CFBundleShortVersionString")
            except (OSError, plistlib.InvalidFileException):
                result["gui_version_unreadable"] = True
        if source:
            if _is_symlink(source) or not source.is_file():
                raise BridgeError("source ZIP is missing or is a symlink")
            release = fetch_release(self.opener)
            _, expected = official_asset(release, result["asset"])
            result.update({"source": str(source), "zip_sha256": _sha256(source),
                           "official_sha256_match": _sha256(source) == expected})
            _zip_candidate(source)
        elif self.manifest.exists():
            manifest = self._load_manifest()
            result.update({"installed": True, "port": manifest.get("port"),
                           "binary_sha256": manifest.get("binary_sha256")})
        else:
            result["installed"] = False
        return result

    def _check_integrity(self) -> dict[str, Any]:
        return self._validate_installation(require_darwin=True)

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(["/bin/launchctl", *args])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError("launchctl could not complete (" + type(exc).__name__ + ")") from exc

    def _foreign_job(self) -> bool:
        result = self._launchctl("print", f"gui/{os.getuid()}/{LABEL}")
        if result.returncode == 0:
            def field(key: str) -> Optional[str]:
                value = re.search(r"^\s*" + re.escape(key) + r" = (.+)$", result.stdout, re.MULTILINE)
                return value.group(1).strip().strip('"') if value else None
            if field("path") != str(self.launch_agent) or field("program") != str(self.binary):
                raise BridgeError("loaded LaunchAgent belongs to a different path or program; refusing to manage it")
            return True
        detail = (result.stderr or result.stdout or "").lower()
        if ("could not find service" in detail or "no such process" in detail or
                "service not found" in detail):
            return False
        raise BridgeError("could not determine whether the bridge LaunchAgent is loaded")

    def _port_free(self, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise BridgeError(f"loopback port {port} is already occupied; no process was killed") from exc
        finally:
            sock.close()

    def start(self, wait: float = 8.0) -> dict[str, Any]:
        manifest = self._check_integrity()
        port = int(manifest.get("port", DEFAULT_PORT))
        if self._foreign_job():
            try:
                health = self.health()
                return {"started": True, "accepted": True, "already_loaded": True, **health}
            except BridgeError as exc:
                return self._pending_health(str(exc), already_loaded=True)
        self._port_free(port)
        result = self._launchctl("bootstrap", f"gui/{os.getuid()}", str(self.launch_agent))
        if result.returncode != 0:
            detail = (result.stderr or "launchctl bootstrap failed").strip().splitlines()[0]
            raise BridgeError(f"could not start bridge: {detail[:240]}")
        deadline = time.monotonic() + wait
        last_error = "health check did not complete"
        while time.monotonic() < deadline:
            try:
                health = self.health()
                return {"started": True, "accepted": True, **health}
            except BridgeError as exc:
                last_error = str(exc)
                time.sleep(0.25)
        return self._pending_health(last_error)

    def _pending_health(self, diagnostic: str, already_loaded: bool = False) -> dict[str, Any]:
        # Leave a clearly identified, recoverable state while the user handles
        # a first-run permission prompt. CLI exit status is 1, never success.
        return {"started": True, "loaded": True, "healthy": False,
                "accepted": False, "already_loaded": already_loaded,
                "diagnostic": diagnostic,
                "recovery": "The owned job remains loaded. Check required Documents permission, then retry status/start; use stop to unload it."}

    def health(self) -> dict[str, Any]:
        manifest = self._check_integrity()
        if not self.secret_file.is_file():
            raise BridgeError("connection secret is unavailable; cannot authenticate health check")
        secret = self.secret_file.read_text(encoding="ascii").strip()
        token = base64.b64encode(f"opencode:{secret}".encode("ascii")).decode("ascii")
        port = int(manifest.get("port", DEFAULT_PORT))
        request = urllib.request.Request(f"http://127.0.0.1:{port}/global/health",
                                         headers={"Authorization": "Basic " + token})
        try:
            with self.health_opener(request, timeout=2) as response:
                body = response.read(1024 * 1024)
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                # Cleanup must not replace the original transport failure;
                # older urllib versions can also fail while closing an error.
                with contextlib.suppress(Exception):
                    exc.close()
            # Never echo a transport exception: an injected opener or upstream
            # library could include the request's Authorization header in it.
            raise BridgeError("health check failed (" + type(exc).__name__ +
                              "); check startup, credentials, or Documents permission") from exc
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("health check returned invalid JSON") from exc
        if not isinstance(data, dict) or data.get("healthy") is not True or data.get("version") != VERSION:
            raise BridgeError("health check did not confirm healthy v1.19.4")
        return {"healthy": True, "version": VERSION, "port": port}

    def status(self) -> dict[str, Any]:
        self._validate_paths()
        if not self.manifest.exists():
            return {"installed": False, "version": VERSION, "label": LABEL,
                    "os": platform.system(), "arch": platform.machine()}
        manifest = self._validate_installation(require_darwin=False)
        if self.system() != "Darwin":
            return {"installed": True, "loaded": None, "version": manifest.get("version"),
                    "port": manifest.get("port"), "diagnostic": "LaunchAgent status requires macOS"}
        loaded = self._foreign_job()
        result: dict[str, Any] = {"installed": True, "loaded": loaded,
                                  "version": manifest.get("version"), "port": manifest.get("port")}
        if loaded:
            try:
                result.update(self.health())
            except BridgeError as exc:
                result["healthy"] = False
                result["diagnostic"] = str(exc)
        return result

    def stop(self) -> dict[str, Any]:
        # Recovery must still be possible if an owned binary was corrupted or
        # removed. Ownership and service configuration are checked separately.
        self._validate_installation(require_darwin=True, check_binary=False,
                                    check_plist=self.launch_agent.exists())
        if not self._foreign_job():
            return {"stopped": True, "loaded": False, "label": LABEL}
        result = self._launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
        if result.returncode not in (0, 113):
            detail = (result.stderr or "launchctl bootout failed").strip().splitlines()[0]
            raise BridgeError(f"could not stop bridge: {detail[:240]}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self._foreign_job():
                return {"stopped": True, "label": LABEL}
            time.sleep(0.1)
        raise BridgeError("LaunchAgent is still unloading; no files were removed")

    def foreground(self) -> int:
        manifest = self._check_integrity()
        if self._foreign_job():
            raise BridgeError("bridge LaunchAgent is already loaded; stop it before foreground mode")
        port = int(manifest.get("port", DEFAULT_PORT))
        self._port_free(port)
        args = [str(self.binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)]
        secret = self.secret_file.read_text(encoding="ascii").strip()
        env = os.environ.copy()
        env.update({**_runtime_environment(), "OPENCODE_SERVER_PASSWORD": secret,
                    "OPENCODE_SERVER_USERNAME": "opencode"})
        return subprocess.call(args, env=env)

    def connection(self, show_secret: bool = False, is_tty: Optional[bool] = None) -> dict[str, Any]:
        manifest = self._validate_installation(require_darwin=False)
        result: dict[str, Any] = {"url": f"http://127.0.0.1:{manifest.get('port', DEFAULT_PORT)}",
                                  "username": "opencode", "version": VERSION}
        if show_secret:
            if is_tty is None:
                is_tty = sys.stdout.isatty()
            if not is_tty:
                raise BridgeError("--show-secret requires an interactive TTY to avoid CI log leakage")
            result["password"] = self.secret_file.read_text(encoding="ascii").strip()
        return result

    def rollback(self) -> dict[str, Any]:
        self._require_darwin()
        self._validate_installation(require_darwin=True, check_binary=False,
                                    check_plist=self.launch_agent.exists())
        try:
            self.stop()
        except BridgeError as exc:
            raise BridgeError("could not stop the owned LaunchAgent; no files were removed") from exc
        # The complete validation above proves that these paths belong to this
        # installation.  Remove only those exact files, never arbitrary user data.
        removed: list[str] = [str(self.launch_agent)] if self.launch_agent.exists() else []
        self.launch_agent.unlink(missing_ok=True)
        for path in (self.binary, self.manifest, self.secret_file, self.marker):
            if path.exists():
                path.unlink()
                removed.append(str(path))
        unknown = []
        if self.support.exists():
            for item in self.support.iterdir():
                if item.name not in {"bin"}:
                    unknown.append(item.name)
            bindir = self.support / "bin"
            if bindir.exists() and not any(bindir.iterdir()):
                bindir.rmdir()
            elif bindir.exists():
                unknown.extend("bin/" + item.name for item in bindir.iterdir())
            if not any(self.support.iterdir()):
                self.support.rmdir()
        return {"rolled_back": True, "removed": removed, "preserved_unknown": unknown,
                "warning": "rollback cannot undo GUI connections or TCC permissions"}


def _json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent OpenCode v1.19.4 CLI bridge")
    parser.add_argument("--support", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--source", type=Path)
    install = sub.add_parser("install")
    group = install.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--download", action="store_true")
    install.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("rollback")
    sub.add_parser("foreground")
    connection = sub.add_parser("connection")
    connection.add_argument("--show-secret", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    bridge = Bridge(args.support)
    try:
        if args.command == "inspect":
            value = bridge.inspect(args.source)
        elif args.command == "install":
            value = bridge.install(args.source, args.download, args.port)
        elif args.command == "status":
            value = bridge.status()
        elif args.command == "start":
            value = bridge.start()
        elif args.command == "stop":
            value = bridge.stop()
        elif args.command == "rollback":
            value = bridge.rollback()
        elif args.command == "foreground":
            return bridge.foreground()
        elif args.command == "connection":
            value = bridge.connection(args.show_secret)
        else:
            raise BridgeError("unknown command")
        _json_print(value)
        if isinstance(value, dict) and (value.get("accepted") is False or
                                       value.get("healthy") is False or
                                       value.get("official_sha256_match") is False):
            return 1
        return 0
    except BridgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError, plistlib.InvalidFileException) as exc:
        print("error: operation could not complete (" + type(exc).__name__ + ")", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; inspect the installation before retrying", file=sys.stderr)
        return 130
if __name__ == "__main__":
    raise SystemExit(main())
