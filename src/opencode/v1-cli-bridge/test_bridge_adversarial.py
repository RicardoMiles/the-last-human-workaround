import email.message
import hashlib
import io
import json
import os
import plistlib
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

import opencode_v1_bridge as bridge


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingRunner:
    def __init__(self, print_result=None):
        self.calls = []
        self.print_result = print_result or Completed(1, "", "could not find service")

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ["/bin/launchctl", "print"]:
            return self.print_result
        if argv[:2] == ["/bin/launchctl", "bootout"]:
            self.print_result = Completed(1, "", "could not find service")
        return Completed()


class BridgeAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_bridge(self, runner=None, opener=None, system="Darwin", support=None):
        runner = runner or RecordingRunner()
        kwargs = {"runner": runner, "system": lambda: system}
        if opener is not None:
            kwargs["opener"] = opener
        support_path = support or self.root / "support"
        b = bridge.Bridge(support_path, **kwargs)
        b.launch_agent = self.root / ("LaunchAgents-" + support_path.name) / (bridge.LABEL + ".plist")
        return b, runner

    def write_owned_install(self, b, port=54321, plist_mutator=None):
        b.support.mkdir(parents=True)
        os.chmod(b.support, 0o700)
        (b.support / "bin").mkdir()
        os.chmod(b.support / "bin", 0o700)
        b.binary.write_bytes(b"candidate")
        os.chmod(b.binary, 0o700)
        secret = "A" * 48
        b.secret_file.write_text(secret + "\n", encoding="ascii")
        os.chmod(b.secret_file, 0o600)
        b.marker.write_text("OpenCode V1 Bridge\n", encoding="ascii")
        os.chmod(b.marker, 0o600)
        asset = bridge._asset_name()
        manifest = {
            "label": bridge.LABEL,
            "version": bridge.VERSION,
            "port": port,
            "asset": asset,
            "asset_url": f"https://github.com/{bridge.REPOSITORY}/releases/download/v{bridge.VERSION}/{asset}",
            "zip_sha256": "0" * 64,
            "binary_sha256": bridge._sha256(b.binary),
            "created_by": "opencode_v1_bridge.py",
        }
        b.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(b.manifest, 0o600)
        plist = b._plist(secret, port)
        if plist_mutator:
            plist_mutator(plist)
        b.launch_agent.parent.mkdir(parents=True)
        with b.launch_agent.open("wb") as stream:
            plistlib.dump(plist, stream, sort_keys=True)
        os.chmod(b.launch_agent, 0o600)

    def make_archive(self):
        archive = self.root / "official.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("opencode", b"candidate")
        return archive

    def official_api(self, archive):
        asset = bridge._asset_name()
        return {
            "tag_name": f"v{bridge.VERSION}",
            "assets": [{
                "name": asset,
                "browser_download_url": f"https://github.com/{bridge.REPOSITORY}/releases/download/v{bridge.VERSION}/{asset}",
                "digest": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            }],
        }

    def test_foreign_loaded_job_same_label_refuses_stop_and_rollback(self):
        foreign_output = "path = /tmp/foreign.plist\nprogram = /tmp/foreign-opencode\n"
        for command in ("stop", "rollback"):
            with self.subTest(command=command):
                runner = RecordingRunner(Completed(0, foreign_output, ""))
                b, _ = self.make_bridge(runner=runner, support=self.root / command)
                self.write_owned_install(b)
                with self.assertRaises(bridge.BridgeError):
                    getattr(b, command)()
                self.assertFalse(
                    any(call[:2] == ["/bin/launchctl", "bootout"] for call in runner.calls)
                )

    def test_unsafe_plist_program_or_environment_refuses_bootstrap(self):
        for kind in ("program", "environment"):
            with self.subTest(kind=kind):
                runner = RecordingRunner()

                def mutate(plist):
                    if kind == "program":
                        plist["ProgramArguments"].append("--unexpected")
                    else:
                        plist["EnvironmentVariables"]["DYLD_INSERT_LIBRARIES"] = "/tmp/inject.dylib"

                b, _ = self.make_bridge(runner=runner, support=self.root / kind)
                self.write_owned_install(b, plist_mutator=mutate)
                b._port_free = lambda _port: None
                b.health = lambda: (_ for _ in ()).throw(bridge.BridgeError("should not health"))
                with self.assertRaises(bridge.BridgeError):
                    b.start(wait=0)
                self.assertFalse(
                    any(call[:2] == ["/bin/launchctl", "bootstrap"] for call in runner.calls)
                )

    def test_support_parent_symlink_is_rejected_before_writing_target(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        link = self.root / "support-parent"
        link.symlink_to(foreign, target_is_directory=True)
        b, _ = self.make_bridge(support=link / "support")
        archive = self.make_archive()
        api = self.official_api(archive)
        with mock.patch.object(bridge, "fetch_release", return_value=api), \
             mock.patch.object(bridge, "verify_binary"):
            with self.assertRaises(bridge.BridgeError):
                b.install(source=archive)
        self.assertFalse((foreign / "support").exists())
        self.assertFalse(b.launch_agent.exists())

    def test_bad_team_or_macho_never_executes_candidate_version(self):
        for kind in ("team", "macho"):
            with self.subTest(kind=kind):
                candidate = self.root / ("candidate-" + kind)
                candidate.write_bytes(b"candidate")
                calls = []

                def runner(argv, **_kwargs):
                    calls.append(argv)
                    if argv[0].endswith("codesign") and argv[1] == "--verify":
                        return Completed()
                    if argv[0].endswith("codesign") and argv[1] == "-dv":
                        team = "BADTEAM" if kind == "team" else bridge.TEAM_IDENTIFIER
                        return Completed(0, "", "TeamIdentifier=" + team + "\n")
                    if argv[0].endswith("/file"):
                        output = "ASCII text\n" if kind == "macho" else "Mach-O " + bridge._expected_arch() + "\n"
                        return Completed(0, output, "")
                    if argv[0] == str(candidate):
                        self.fail("candidate --version was executed after a failed preflight")
                    return Completed()

                with self.assertRaises(bridge.BridgeError):
                    bridge.verify_binary(candidate, runner)
                self.assertFalse(any(argv[0] == str(candidate) for argv in calls))

    def test_partial_install_commit_failure_removes_all_generated_artifacts(self):
        b, _ = self.make_bridge()
        archive = self.make_archive()
        api = self.official_api(archive)
        original_publish = bridge._publish_file

        def fail_plist_publish(source, target):
            if Path(target) == b.launch_agent:
                raise OSError("injected commit failure")
            return original_publish(source, target)

        with mock.patch.object(bridge, "fetch_release", return_value=api), \
             mock.patch.object(bridge, "verify_binary"), \
             mock.patch.object(bridge, "_publish_file", side_effect=fail_plist_publish):
            with self.assertRaises(Exception):
                b.install(source=archive)
        self.assertFalse(b.binary.exists())
        self.assertFalse(b.secret_file.exists())
        self.assertFalse(b.manifest.exists())
        self.assertFalse(b.launch_agent.exists())
        self.assertFalse(b.support.exists())

    def test_health_redirect_handler_makes_no_second_request(self):
        b, _ = self.make_bridge()
        self.write_owned_install(b)
        made = []

        class FakeOpener:
            def __init__(self, handlers):
                self.handlers = handlers
                self.calls = []

            def open(self, request, timeout=2):
                self.calls.append(request)
                if len(self.calls) == 1:
                    no_redirect = any(isinstance(h, bridge._NoRedirect) for h in self.handlers)
                    if no_redirect:
                        raise urllib.error.HTTPError(
                            request.full_url, 302, "redirect blocked", email.message.Message(), io.BytesIO()
                        )
                    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
                        request, None, 302, "Found", email.message.Message(), "https://external.invalid/"
                    )
                    return self.open(redirected, timeout=timeout)
                raise AssertionError("health followed an external redirect")

        def fake_build_opener(*handlers):
            opener = FakeOpener(handlers)
            made.append(opener)
            return opener

        with mock.patch.object(urllib.request, "build_opener", side_effect=fake_build_opener):
            with self.assertRaisesRegex(bridge.BridgeError, "HTTPError"):
                b.health()
        self.assertEqual(len(made), 1)
        self.assertEqual(len(made[0].calls), 1)
        self.assertTrue(made[0].calls[0].full_url.startswith("http://127.0.0.1:"))

    def test_health_error_does_not_echo_fixture_secret(self):
        b, _ = self.make_bridge()
        self.write_owned_install(b)
        b.health_opener = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fixturesecret"))
        with self.assertRaises(bridge.BridgeError) as caught:
            b.health()
        self.assertNotIn("fixturesecret", str(caught.exception))

    def test_non_darwin_install_rejects_before_any_mutation_or_io(self):
        calls = []

        def runner(*_args, **_kwargs):
            calls.append("runner")
            raise AssertionError("runner called before non-Darwin rejection")

        def opener(*_args, **_kwargs):
            calls.append("opener")
            raise AssertionError("network called before non-Darwin rejection")

        b, _ = self.make_bridge(runner=runner, opener=opener, system="Linux")
        with self.assertRaisesRegex(bridge.BridgeError, "macOS"):
            b.install(source=self.root / "missing.zip")
        self.assertEqual(calls, [])
        self.assertFalse(b.support.exists())
        self.assertFalse(b.launch_agent.exists())

    def test_invalid_manifest_port_is_rejected_before_launchctl(self):
        for value in ("not-a-port", 0, 65536):
            with self.subTest(value=value):
                runner = RecordingRunner()
                b, _ = self.make_bridge(runner=runner, support=self.root / str(value).replace("-", "_"))
                self.write_owned_install(b, port=value)
                with self.assertRaises(bridge.BridgeError):
                    b.start(wait=0)
                self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
