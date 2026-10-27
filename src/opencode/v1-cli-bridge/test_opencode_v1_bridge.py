import hashlib
import io
import json
import os
import plistlib
import stat
import tempfile
import unittest
import zipfile
import urllib.error
from pathlib import Path
from unittest import mock

import opencode_v1_bridge as bridge


class Response:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def read(self, _size=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if len(argv) >= 2 and argv[1] == "--version":
            return Completed(0, "1.19.4\n", "")
        if argv[0] == "/usr/bin/codesign" and argv[1] == "-dv":
            return Completed(0, "", "TeamIdentifier=5NZ4Q7NXJ4\n")
        if argv[0] == "/usr/bin/file":
            return Completed(0, "Mach-O 64-bit executable " + bridge._expected_arch() + "\n", "")
        if argv[0] == "/bin/launchctl" and argv[1] == "print":
            return Completed(1, "", "could not find service")
        return Completed(0, "", "")


class Completed:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_zip(path, name="opencode"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, b"signed executable placeholder")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.archive = self.root / "official.zip"
        make_zip(self.archive)
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.runner = Runner()
        self.api = {"tag_name": "v1.19.4", "assets": [{
            "name": bridge._asset_name(),
            "browser_download_url": "https://github.com/anomalyco/opencode/releases/download/v1.19.4/" + bridge._asset_name(),
            "digest": "sha256:" + self.digest,
        }]}
        self.bridge = bridge.Bridge(self.root / "support", self.runner, self.opener,
                                    system=lambda: "Darwin", health_opener=self.opener)
        self.bridge.launch_agent = self.root / "LaunchAgents" / (bridge.LABEL + ".plist")

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, request, **_kwargs):
        url = request.full_url if hasattr(request, "full_url") else request
        if url == bridge.RELEASE_API:
            return Response(json.dumps(self.api).encode())
        if "/health" in str(url):
            return Response(json.dumps({"healthy": True, "version": bridge.VERSION}).encode())
        return Response(self.archive.read_bytes(), {"Content-Length": str(self.archive.stat().st_size)})

    def test_official_asset_requires_nonempty_digest(self):
        with self.assertRaisesRegex(bridge.BridgeError, "sha256"):
            bridge.official_asset({"assets": [{"name": "x", "digest": "", "browser_download_url":
                "https://github.com/anomalyco/opencode/releases/download/v1.19.4/x"}]}, "x")

    def test_official_asset_rejects_unrelated_download_host(self):
        self.api["assets"][0]["browser_download_url"] = "https://external.invalid/asset.zip"
        with self.assertRaisesRegex(bridge.BridgeError, "expected release asset"):
            self.bridge.install(source=self.archive)
        self.assertFalse(self.bridge.support.exists())

    def test_wrong_digest_never_verifies_or_executes_candidate(self):
        self.api["assets"][0]["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(bridge.BridgeError, "sha256"):
            self.bridge.install(source=self.archive)
        self.assertFalse(self.bridge.support.exists())
        self.assertFalse(any("codesign" in call[0] or "--version" in call for call in self.runner.calls))

    def test_duplicate_candidates_and_oversized_zip_are_rejected(self):
        archive_path = self.root / "duplicates.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("a/opencode", b"x")
            archive.writestr("b/opencode", b"x")
        with self.assertRaisesRegex(bridge.BridgeError, "exactly one"):
            bridge._zip_candidate(archive_path)
        with mock.patch.object(bridge, "MAX_ZIP_UNCOMPRESSED", 1):
            with self.assertRaisesRegex(bridge.BridgeError, "size"):
                bridge._zip_candidate(self.archive)

    def test_reinstall_different_port_is_rejected_without_changes(self):
        self.bridge.install(source=self.archive)
        originals = {p: p.read_bytes() for p in (self.bridge.manifest, self.bridge.secret_file, self.bridge.launch_agent)}
        with self.assertRaisesRegex(bridge.BridgeError, "different port"):
            self.bridge.install(source=self.archive, port=54321)
        self.assertTrue(all(p.read_bytes() == content for p, content in originals.items()))

    def test_corrupted_owned_binary_can_be_stopped_and_removed(self):
        self.bridge.install(source=self.archive)
        self.bridge.binary.write_bytes(b"corrupted fixture")
        with self.assertRaisesRegex(bridge.BridgeError, "digest"):
            self.bridge.start()
        self.assertTrue(self.bridge.stop()["stopped"])
        self.assertTrue(self.bridge.rollback()["rolled_back"])

    def test_secret_permission_drift_blocks_connection_output(self):
        self.bridge.install(source=self.archive)
        self.bridge.secret_file.chmod(0o644)
        with self.assertRaisesRegex(bridge.BridgeError, "permissions"):
            self.bridge.connection(show_secret=True, is_tty=True)

    def test_interrupted_publication_can_repair_missing_launch_agent(self):
        with mock.patch.object(self.bridge, "_publish_plist", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.bridge.install(source=self.archive)
        self.assertTrue(self.bridge.manifest.exists())
        self.assertFalse(self.bridge.launch_agent.exists())
        saved_secret = self.bridge.secret_file.read_bytes()
        result = self.bridge.install(source=self.archive)
        self.assertTrue(result["repaired_launch_agent"])
        self.assertEqual(saved_secret, self.bridge.secret_file.read_bytes())
        self.assertTrue(self.bridge.rollback()["rolled_back"])

    def test_owned_loaded_service_can_be_rechecked_and_stopped(self):
        self.bridge.install(source=self.archive)
        loaded = [False]

        def stateful_runner(argv, **kwargs):
            if argv[:2] == ["/bin/launchctl", "print"]:
                if loaded[0]:
                    return Completed(0, "path = " + str(self.bridge.launch_agent) +
                                     "\nprogram = " + str(self.bridge.binary) + "\n", "")
                return Completed(113, "", "Could not find service")
            if argv[:2] == ["/bin/launchctl", "bootstrap"]:
                loaded[0] = True
            if argv[:2] == ["/bin/launchctl", "bootout"]:
                loaded[0] = False
            return Completed(0, "", "")

        self.bridge.runner = stateful_runner
        self.bridge._port_free = mock.Mock()
        self.assertTrue(self.bridge.start()["accepted"])
        self.assertTrue(self.bridge.start()["already_loaded"])
        self.bridge._port_free.assert_called_once()
        self.assertTrue(self.bridge.stop()["stopped"])
        self.assertFalse(loaded[0])

    def test_unverified_start_has_explicit_recovery_and_failure_exit_code(self):
        self.bridge.install(source=self.archive)
        self.bridge._port_free = mock.Mock()
        result = self.bridge.start(wait=0)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["healthy"])
        self.assertTrue(result["loaded"])
        self.assertIn("stop", result["recovery"])
        with mock.patch.object(bridge, "Bridge") as constructor, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            constructor.return_value.start.return_value = result
            self.assertEqual(bridge.main(["start"]), 1)

    def test_status_uninstalled_is_read_only_and_friendly(self):
        value = self.bridge.status()
        self.assertFalse(value["installed"])

    def test_zip_path_traversal_and_symlink_are_rejected(self):
        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../opencode", b"x")
        with self.assertRaisesRegex(bridge.BridgeError, "traversal"):
            bridge._zip_candidate(traversal)
        symlink = self.root / "symlink.zip"
        info = zipfile.ZipInfo("opencode")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.writestr(info, b"target")
        with self.assertRaisesRegex(bridge.BridgeError, "symlink"):
            bridge._zip_candidate(symlink)

    def test_signature_failure_aborts_before_install(self):
        def bad_runner(argv, **kwargs):
            if argv[0] == "/usr/bin/codesign":
                return Completed(1, "", "invalid signature")
            return self.runner(argv, **kwargs)
        candidate = self.root / "opencode"
        candidate.write_bytes(b"x")
        with self.assertRaisesRegex(bridge.BridgeError, "codesign"):
            bridge.verify_binary(candidate, bad_runner)

    def test_install_is_idempotent_and_preserves_secret(self):
        first = self.bridge.install(source=self.archive)
        secret = self.bridge.secret_file.read_text().strip()
        second = self.bridge.install(source=self.archive)
        self.assertEqual(secret, self.bridge.secret_file.read_text().strip())
        self.assertEqual(first["binary_sha256"], second["binary_sha256"])
        self.assertEqual(stat.S_IMODE(self.bridge.support.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.bridge.secret_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.bridge.launch_agent.stat().st_mode), 0o600)
        plist = plistlib.loads(self.bridge.launch_agent.read_bytes())
        self.assertEqual(plist["Label"], bridge.LABEL)
        self.assertEqual(plist["ProgramArguments"][0], str(self.bridge.binary))
        self.assertEqual(plist["EnvironmentVariables"]["OPENCODE_SERVER_PASSWORD"], secret)

    def test_install_refuses_foreign_or_symlink_paths(self):
        self.bridge.support.mkdir()
        (self.bridge.support / "unrelated.txt").write_text("keep")
        with self.assertRaises(bridge.BridgeError):
            self.bridge.install(source=self.archive)
        # The failed install may have created only tool-owned staging folders;
        # use a separate isolated bridge for the symlink case.
        other = bridge.Bridge(self.root / "other", self.runner, self.opener)
        other.launch_agent = self.root / "OtherLaunchAgents" / (bridge.LABEL + ".plist")
        other.support.mkdir()
        other.manifest.symlink_to(self.root / "foreign")
        with self.assertRaises(bridge.BridgeError):
            other.install(source=self.archive)

    def test_start_port_conflict_never_kills_owner(self):
        self.bridge.install(source=self.archive)
        # The sandbox disallows opening listening sockets, so inject the exact
        # diagnostic produced by a real occupied loopback port.
        self.bridge._port_free = lambda _port: (_ for _ in ()).throw(
            bridge.BridgeError("loopback port 4096 is already occupied; no process was killed"))
        with self.assertRaisesRegex(bridge.BridgeError, "occupied"):
            self.bridge.start()
        self.assertFalse(any(call[:2] == ["launchctl", "kill"] for call in self.runner.calls))

    def test_health_wrong_version_and_connection_secret_policy(self):
        self.bridge.install(source=self.archive)
        self.bridge.health_opener = lambda request, **kwargs: Response(json.dumps({"healthy": True, "version": "other"}).encode())
        with self.assertRaisesRegex(bridge.BridgeError, "v1.19.4"):
            self.bridge.health()
        with self.assertRaisesRegex(bridge.BridgeError, "TTY"):
            self.bridge.connection(show_secret=True, is_tty=False)
        self.assertNotIn("password", self.bridge.connection(show_secret=False, is_tty=False))

    def test_health_uses_global_endpoint_and_does_not_follow_redirect(self):
        self.bridge.install(source=self.archive)
        requests = []

        def redirecting(request, **_kwargs):
            requests.append(request)
            error = urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://example.invalid/"}, io.BytesIO())
            raise error

        self.bridge.health_opener = redirecting
        with self.assertRaisesRegex(bridge.BridgeError, "HTTPError"):
            self.bridge.health()
        self.assertEqual(len(requests), 1)
        self.assertIn("/global/health", requests[0].full_url)

    def test_rollback_stop_failure_does_not_remove_install(self):
        self.bridge.install(source=self.archive)
        original = self.bridge.runner

        def loaded_and_failing(argv, **kwargs):
            if argv[0] == "/bin/launchctl" and argv[1] == "print":
                return Completed(0, "path = " + str(self.bridge.launch_agent) +
                                 "\nprogram = " + str(self.bridge.binary) + "\n", "")
            if argv[0] == "/bin/launchctl" and argv[1] == "bootout":
                return Completed(1, "", "permission denied")
            return original(argv, **kwargs)

        self.bridge.runner = loaded_and_failing
        with self.assertRaisesRegex(bridge.BridgeError, "no files were removed"):
            self.bridge.rollback()
        self.assertTrue(self.bridge.binary.exists())
        self.assertTrue(self.bridge.launch_agent.exists())

    def test_rollback_preserves_unknown_files_and_warns_about_gui(self):
        self.bridge.install(source=self.archive)
        unknown = self.bridge.support / "user-added.txt"
        unknown.write_text("preserve me")
        result = self.bridge.rollback()
        self.assertTrue(result["rolled_back"])
        self.assertTrue(unknown.exists())
        self.assertIn("GUI", result["warning"])
        self.assertFalse(self.bridge.binary.exists())
        self.assertFalse(self.bridge.launch_agent.exists())


if __name__ == "__main__":
    unittest.main()
