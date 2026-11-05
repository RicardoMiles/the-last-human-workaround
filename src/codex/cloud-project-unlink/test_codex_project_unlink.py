import contextlib
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import codex_project_unlink as u


PROJECT_SQL = """CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
)"""
THREAD_SQL = """CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    model_provider TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    has_user_event INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at INTEGER,
    git_sha TEXT,
    git_branch TEXT,
    git_origin_url TEXT,
    cli_version TEXT NOT NULL DEFAULT '', first_user_message TEXT NOT NULL DEFAULT '',
    agent_nickname TEXT, agent_role TEXT, memory_mode TEXT NOT NULL DEFAULT 'enabled',
    model TEXT, reasoning_effort TEXT, agent_path TEXT, created_at_ms INTEGER,
    updated_at_ms INTEGER, thread_source TEXT, preview TEXT NOT NULL DEFAULT '',
    recency_at INTEGER NOT NULL DEFAULT 0, recency_at_ms INTEGER NOT NULL DEFAULT 0,
    history_mode TEXT NOT NULL DEFAULT 'legacy', name TEXT, is_pinned INTEGER NOT NULL DEFAULT 0,
    thread_section_id TEXT REFERENCES thread_sections(id) ON DELETE SET NULL,
    section_position INTEGER, section_entered_at_ms INTEGER,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    originator TEXT, daybreak_enabled BOOLEAN, creator_user_id TEXT,
    creator_account_id TEXT, attached_thread_parent_id TEXT
)"""
TRIGGERS = ["""CREATE TRIGGER threads_created_at_ms_after_insert
AFTER INSERT ON threads
WHEN NEW.created_at_ms IS NULL
BEGIN
    UPDATE threads
    SET created_at_ms = NEW.created_at * 1000
    WHERE id = NEW.id;
END""", """CREATE TRIGGER threads_created_at_ms_after_update
AFTER UPDATE OF created_at ON threads
WHEN NEW.created_at != OLD.created_at
 AND NEW.created_at_ms IS OLD.created_at_ms
BEGIN
    UPDATE threads
    SET created_at_ms = NEW.created_at * 1000
    WHERE id = NEW.id;
END""", """CREATE TRIGGER threads_recency_at_after_insert
AFTER INSERT ON threads
WHEN NEW.recency_at_ms = 0
BEGIN
    UPDATE threads
    SET recency_at = NEW.updated_at,
        recency_at_ms = COALESCE(NEW.updated_at_ms, NEW.updated_at * 1000)
    WHERE id = NEW.id;
END""", """CREATE TRIGGER threads_updated_at_ms_after_insert
AFTER INSERT ON threads
WHEN NEW.updated_at_ms IS NULL
BEGIN
    UPDATE threads
    SET updated_at_ms = NEW.updated_at * 1000
    WHERE id = NEW.id;
END""", """CREATE TRIGGER threads_updated_at_ms_after_update
AFTER UPDATE OF updated_at ON threads
WHEN NEW.updated_at != OLD.updated_at
 AND NEW.updated_at_ms IS OLD.updated_at_ms
BEGIN
    UPDATE threads
    SET updated_at_ms = NEW.updated_at * 1000
    WHERE id = NEW.id;
END"""]


@contextlib.contextmanager
def database(root):
    conn = sqlite3.connect(str(root / u.DATABASE))
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class SimulatedCrash(BaseException):
    pass


class WorkaroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "codex-home"
        self.root.mkdir()
        (self.root / u.JOURNALS).mkdir()
        self.bundle = self.base / "Fixture.app"
        (self.bundle / "Contents").mkdir(parents=True)
        self.app = {"CFBundleIdentifier": u.SUPPORTED_APP[0],
                    "CFBundleShortVersionString": u.SUPPORTED_APP[1], "CFBundleVersion": u.SUPPORTED_APP[2]}
        (self.bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(self.app))
        self.local = "11111111-1111-4111-8111-111111111111"
        self.backing = {"projectId": "g-p-fixture", "accountId": "fixture-account", "userId": "fixture-user"}
        self.g = {"cache": {"window": 1}, "local-projects": {
            self.local: {"id": self.local, "name": "Fixture", "rootPaths": ["/fixture/project"], "chatGptBacking": self.backing},
            "other": {"id": "other", "name": "Other", "rootPaths": ["/fixture/other"]}}}
        self.write_global(self.g)
        self.meta = {"codex.localProjectId": self.local, "codex.chatGptBacking": json.dumps(self.backing), "keep": "value"}
        with database(self.root) as c:
            c.execute(PROJECT_SQL)
            c.execute("CREATE TABLE thread_sections(id TEXT PRIMARY KEY)")
            c.execute(THREAD_SQL)
            for t in TRIGGERS:
                c.execute(t)
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", ("db-project", "Fixture", json.dumps(self.meta), 0, 1, 1))
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", ("other-db", "Other", '{}', 1, 1, 1))
        self.add_thread("old-thread")
        self.journal = self.root / u.JOURNALS / "fixture.json"
        self.journal_bytes = json.dumps({"localProjectId": self.local, "state": "backed", **self.backing}, indent=2).encode()
        self.journal.write_bytes(self.journal_bytes)
        self.backup = self.base / "backup"
        self.writers = mock.patch.object(u, "process_writers", return_value=[])
        self.writers.start()
        self.addCleanup(self.writers.stop)

    def write_global(self, g):
        (self.root / u.GLOBAL).write_text(json.dumps(g))

    def global_state(self):
        return json.loads((self.root / u.GLOBAL).read_bytes())

    def metadata(self):
        with database(self.root) as c:
            return json.loads(c.execute("SELECT metadata FROM projects WHERE id='db-project'").fetchone()[0])

    def set_metadata(self, meta):
        with database(self.root) as c:
            c.execute("UPDATE projects SET metadata=? WHERE id='db-project'", (json.dumps(meta),))

    def add_thread(self, thread_id):
        with database(self.root) as c:
            c.execute("INSERT INTO threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,project_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (thread_id, "/fixture/rollout", 1, 1, "fixture", "fixture", "/fixture/project", thread_id, "fixture", "fixture", "db-project"))

    def thread_rows(self):
        with database(self.root) as c:
            return c.execute("SELECT * FROM threads ORDER BY id").fetchall()

    def plan(self):
        return u.make_plan(self.root, self.bundle, self.local)

    def test_profile_is_exact_observed_schema(self):
        p = u.profile(self.root, self.bundle)
        self.assertEqual(p["schema_sha256"], u.SUPPORTED_SCHEMA)
        self.assertTrue(u.supported(p))

    def test_success_preserves_threads_other_projects_and_private_files(self):
        rows = self.thread_rows()
        plan = self.plan()
        g = self.global_state()
        g["cache"]["window"] = 2  # App quit may update unrelated window cache.
        self.write_global(g)
        u.apply_plan(plan, self.backup)
        self.assertTrue(u.verify(self.backup)["matches_expected_after"])
        self.assertEqual(self.thread_rows(), rows)
        self.assertEqual(self.global_state()["cache"], {"window": 2})
        self.assertEqual(self.global_state()["local-projects"]["other"], self.g["local-projects"]["other"])
        self.assertEqual(self.metadata(), {"codex.localProjectId": self.local, "keep": "value"})
        self.assertEqual((self.backup / "journal.archived.json").read_bytes(), self.journal_bytes)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o700)
        for f in self.backup.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600, f.name)
        with self.assertRaises(u.Refused):
            u.apply_plan(plan, self.backup)
        self.assertEqual(u.inspect(self.root, self.bundle)["projects"][0]["state"], "unlinked")

    def test_plan_is_private_exclusive_and_does_not_edit_home(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        out = self.base / "plan.json"
        u.exclusive_json(out, self.plan())
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            u.exclusive_json(out, self.plan())

    def test_target_change_since_plan_refuses(self):
        p = self.plan()
        g = self.global_state()
        g["local-projects"][self.local]["name"] = "Changed"
        self.write_global(g)
        with self.assertRaisesRegex(u.Refused, "Target changed"):
            u.apply_plan(p, self.backup)
        self.assertFalse(self.backup.exists())

    def test_schema_change_and_version_change_refuse(self):
        p = self.plan()
        with database(self.root) as c:
            c.execute("PRAGMA user_version=1")
        with self.assertRaisesRegex(u.Refused, "schema changed"):
            u.apply_plan(p, self.backup)
        with database(self.root) as c:
            c.execute("PRAGMA user_version=0")
        self.app["CFBundleVersion"] = "future"
        (self.bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(self.app))
        with self.assertRaisesRegex(u.Refused, "version or database schema changed"):
            u.apply_plan(p, self.backup)
        future = self.plan()
        self.assertFalse(future["write_supported"])
        with self.assertRaisesRegex(u.Refused, "Unreviewed"):
            u.apply_plan(future, self.backup)

    def test_disagreement_and_duplicate_journal_refuse(self):
        m = copy.deepcopy(self.meta)
        m["codex.chatGptBacking"] = json.dumps({**self.backing, "accountId": "different"})
        self.set_metadata(m)
        with self.assertRaisesRegex(u.Refused, "disagree"):
            self.plan()
        self.set_metadata(self.meta)
        (self.journal.parent / "duplicate.json").write_bytes(self.journal_bytes)
        with self.assertRaisesRegex(u.Refused, "exactly one matching"):
            self.plan()

    def test_lifecycle_and_duplicate_database_mapping_refuse(self):
        m = {**self.meta, "codex.retiredChatGptBacking": True}
        self.set_metadata(m)
        with self.assertRaisesRegex(u.Refused, "lifecycle"):
            self.plan()
        self.set_metadata(self.meta)
        with database(self.root) as c:
            c.execute("UPDATE projects SET metadata=? WHERE id='other-db'", (json.dumps(self.meta),))
        with self.assertRaisesRegex(u.Refused, "exactly one database"):
            self.plan()

    def test_running_executor_refuses_before_backup(self):
        p = self.plan()
        with mock.patch.object(u, "process_writers", return_value=[{"pid": 1}]):
            with self.assertRaisesRegex(u.Refused, "executors are running"):
                u.apply_plan(p, self.backup)
        self.assertFalse(self.backup.exists())

    def test_handled_failure_rolls_back_only_target(self):
        before = self.thread_rows()
        def fail(stage):
            if stage == "global_written":
                raise RuntimeError("synthetic failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            u.apply_plan(self.plan(), self.backup, failure_hook=fail)
        self.assertTrue(u.verify(self.backup)["matches_expected_before"])
        self.assertEqual(self.thread_rows(), before)
        self.assertEqual(self.journal.read_bytes(), self.journal_bytes)

    def test_crash_mixed_states_recover_idempotently(self):
        for stage in ("global_written", "journal_archived", "committed"):
            with self.subTest(stage=stage):
                backup = self.base / ("crash-" + stage)
                def crash(current):
                    if current == stage:
                        raise SimulatedCrash()
                with self.assertRaises(SimulatedCrash):
                    u.apply_plan(self.plan(), backup, failure_hook=crash)
                self.add_thread("new-" + stage)
                g = self.global_state()
                g["new-cache"] = stage
                self.write_global(g)
                m = self.metadata()
                m["new-metadata"] = stage
                self.set_metadata(m)
                rows = self.thread_rows()
                u.rollback(backup)
                u.rollback(backup)
                self.assertTrue(u.verify(backup)["matches_expected_before"])
                self.assertEqual(self.thread_rows(), rows)
                self.assertEqual(self.global_state()["new-cache"], stage)
                self.assertEqual(self.metadata()["new-metadata"], stage)

    def test_rollback_after_new_work_preserves_everything_else(self):
        u.apply_plan(self.plan(), self.backup)
        self.add_thread("new-thread")
        rows = self.thread_rows()
        g = self.global_state()
        g["local-projects"][self.local]["new"] = "keep"
        g["local-projects"]["new-project"] = {"id": "new-project", "rootPaths": []}
        self.write_global(g)
        m = self.metadata()
        m["later"] = {"nested": True}
        self.set_metadata(m)
        u.rollback(self.backup)
        self.assertEqual(self.thread_rows(), rows)
        self.assertEqual(self.metadata()["later"], {"nested": True})
        self.assertEqual(self.global_state()["local-projects"][self.local]["new"], "keep")
        self.assertIn("new-project", self.global_state()["local-projects"])

    def test_conflicting_rollback_refuses_without_overwriting(self):
        u.apply_plan(self.plan(), self.backup)
        g = self.global_state()
        g["local-projects"][self.local]["chatGptBacking"] = {**self.backing, "projectId": "new-cloud"}
        self.write_global(g)
        before_g = (self.root / u.GLOBAL).read_bytes()
        before_m = self.metadata()
        with self.assertRaisesRegex(u.Refused, "another writer"):
            u.rollback(self.backup)
        self.assertEqual((self.root / u.GLOBAL).read_bytes(), before_g)
        self.assertEqual(self.metadata(), before_m)
        self.assertFalse(self.journal.exists())

    def test_tampered_plan_cannot_change_other_metadata(self):
        p = self.plan()
        p["after_metadata"]["keep"] = "erase"
        with self.assertRaisesRegex(u.Refused, "beyond unlinking"):
            u.apply_plan(p, self.backup)

    def test_output_paths_cannot_pollute_live_state_or_application(self):
        for location in (self.root / u.JOURNALS / "plan.json", self.root / "sessions" / "backup", self.bundle / "Contents" / "plan.json"):
            with self.subTest(location=location):
                with self.assertRaises(u.Refused):
                    u.output_path(location, self.root, bundle=self.bundle)
                with self.assertRaises(u.Refused):
                    u.output_path(location, self.root, backup=True, bundle=self.bundle)
        self.assertEqual(u.output_path(self.root / "backups" / "workaround", self.root, backup=True), (self.root / "backups" / "workaround").resolve())

    def test_rebound_root_paths_refuse_recovery_without_changes(self):
        u.apply_plan(self.plan(), self.backup)
        g = self.global_state()
        g["local-projects"][self.local]["rootPaths"] = ["/fixture/new-project"]
        self.write_global(g)
        before = (self.root / u.GLOBAL).read_bytes()
        with self.assertRaisesRegex(u.Refused, "roots changed"):
            u.rollback(self.backup)
        self.assertEqual((self.root / u.GLOBAL).read_bytes(), before)
        self.assertNotIn("codex.chatGptBacking", self.metadata())

    def test_verify_rejects_new_matching_journal_or_mapping(self):
        u.apply_plan(self.plan(), self.backup)
        extra = self.journal.parent / "new.json"
        extra.write_bytes(self.journal_bytes)
        with self.assertRaisesRegex(u.Refused, "new matching provisioning"):
            u.verify(self.backup)
        extra.unlink()
        g = self.global_state()
        g["local-projects"]["other"]["chatGptBacking"] = self.backing
        self.write_global(g)
        with self.assertRaisesRegex(u.Refused, "another local mapping"):
            u.verify(self.backup)

    def test_verify_mixed_state_has_failure_exit_status(self):
        def crash(stage):
            raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            u.apply_plan(self.plan(), self.backup, failure_hook=crash)
        with mock.patch.object(u.sys, "stdout", io.StringIO()) as output:
            code = u.main(["verify", "--backup-dir", str(self.backup)])
        self.assertEqual(code, 2)
        self.assertTrue(json.loads(output.getvalue())["mixed_state_needs_recovery"])


class ProcessTests(unittest.TestCase):
    def test_real_executables_or_orphans_block_but_shell_text_does_not(self):
        comm = """101 1 S /custom/Relocated.app/Contents/MacOS/Codex
102 1 S /opt/tools/codex
103 1 S /bin/bash
104 1 S /usr/bin/python3
105 1 Z /opt/tools/codex
106 1 S /opt/node/bin/node
"""
        args = """101 /custom/Relocated.app/Contents/MacOS/Codex
102 /opt/tools/codex app-server
103 bash -c cat codex_project_unlink.py
104 python3 /tmp/codex_project_unlink.py inspect
105 /opt/tools/codex
106 node /opt/code/codex app-server
"""
        with mock.patch.object(u.sys, "platform", "darwin"), mock.patch.object(u.subprocess, "check_output", side_effect=[comm, args]):
            self.assertEqual([p["pid"] for p in u.process_writers()], [101, 102, 106])


if __name__ == "__main__":
    unittest.main()
