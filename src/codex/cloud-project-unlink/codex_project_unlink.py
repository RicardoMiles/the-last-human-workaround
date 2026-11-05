#!/usr/bin/env python3
"""Conservative, offline workaround for one verified erroneous project pairing.

No cloud API, project deletion, folder deletion, or thread mutation is implemented.
The on-disk format is private. Unknown application versions/schemas cannot write.
"""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

VERSION = "0.1.0"
FORMAT = "codex-project-unlink/v1"
SUPPORTED_APP = ("com.openai.codex", "26.1029.61003", "24614")
SUPPORTED_SCHEMA = "d59069e0ca168283ff1129c56b8b9b3fd81e4ca23c9be88188d9f58cb504f42c"
RELATED = ("chatGptBackingCreation", "chatGptBackingRecoveryPending", "retiredChatGptBacking")
GLOBAL = ".codex-global-state.json"
DATABASE = "state_5.sqlite"
JOURNALS = "project-chatgpt-provisioning"


class Refused(RuntimeError):
    """A safety precondition did not hold; manual review is needed."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_hash(value):
    return digest(encode(value))


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path):
    value = json.loads(Path(path).read_bytes())
    require(isinstance(value, dict), "Expected a JSON object: " + Path(path).name)
    return value


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Missing or symlinked file: " + path.name)
    return path


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data, mode=0o600):
    path = Path(path)
    require(not path.is_symlink(), "Refusing to replace a symlink")
    fd, temp = tempfile.mkstemp(prefix="." + path.name + ".unlink-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def exclusive_bytes(path, data):
    path = Path(path)
    require(path.parent.is_dir(), "Output parent directory must already exist")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(path.parent)


def exclusive_json(path, value):
    exclusive_bytes(path, encode(value))


def output_path(path, root, backup=False, bundle=None):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path.is_relative_to(root):
        require(backup and path.is_relative_to(root / "backups") and path != root / "backups",
                "Output must be outside Codex home (backup directories may use its backups subtree)")
    if bundle is not None:
        require(not path.is_relative_to(Path(bundle).resolve()), "Output cannot be inside the application bundle")
    return path


def connect(root, write=False):
    database = regular(Path(root) / DATABASE)
    return sqlite3.connect(database.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=5)


def schema(conn):
    return {"user_version": conn.execute("PRAGMA user_version").fetchone()[0], "tables": {
        name: {
            "columns": conn.execute("PRAGMA table_info(" + name + ")").fetchall(),
            "foreign_keys": conn.execute("PRAGMA foreign_key_list(" + name + ")").fetchall(),
            "triggers": conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name", (name,)).fetchall(),
        } for name in ("projects", "threads")}}


def app_version(bundle):
    p = regular(Path(bundle) / "Contents" / "Info.plist")
    with p.open("rb") as stream:
        data = plistlib.load(stream)
    return {"bundle_id": str(data.get("CFBundleIdentifier", "")),
            "version": str(data.get("CFBundleShortVersionString", "")),
            "build": str(data.get("CFBundleVersion", ""))}


def profile(root, bundle):
    root = Path(root).resolve()
    require(root.is_dir(), "Codex home does not exist")
    others = [p.name for p in root.glob("state_*.sqlite") if p.name != DATABASE]
    require(not others, "Another state_N.sqlite is present; this format needs manual review")
    with contextlib.closing(connect(root)) as conn:
        s = schema(conn)
    app = app_version(bundle)
    return {"app": app, "schema_sha256": object_hash(s), "database": DATABASE}


def supported(p):
    app = p["app"]
    return ((app["bundle_id"], app["version"], app["build"]) == SUPPORTED_APP
            and p["schema_sha256"] == SUPPORTED_SCHEMA and p["database"] == DATABASE)


def require_profile(root, bundle, expected):
    actual = profile(root, bundle)
    require(actual == expected, "Application version or database schema changed; make a new plan")
    require(supported(actual), "Unreviewed application version/schema; writes are disabled")


def process_writers():
    """Conservatively block Codex/ChatGPT desktop executors, including orphans.

    argv and comm are queried separately: comm is not polluted by shell text.
    Unrelated Codex CLIs are deliberately blockers; none are terminated.
    """
    require(sys.platform == "darwin", "Writes are supported only on macOS")
    comm_text = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,state=,comm="], text=True)
    arg_text = subprocess.check_output(["/bin/ps", "-axo", "pid=,args="], text=True)
    args = {}
    for line in arg_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            args[int(parts[0])] = parts[1]
    found = []
    for line in comm_text.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid, ppid, state, command = parts
        if state.startswith("Z") or int(pid) == os.getpid():
            continue
        name = Path(command).name.lower()
        argv = args.get(int(pid), "")
        # Name still identifies a relocated/renamed bundle's executable.
        is_core = name in ("codex", "chatgpt", "codex-app-server", "codex-cli")
        is_bundle = bool(re.search(r"\.app/Contents/(MacOS|Resources)/", command)) and any(x in command.lower() for x in ("codex", "chatgpt"))
        try:
            tokens = shlex.split(argv)
        except ValueError:
            tokens = []
        is_app_server = any(Path(t).name.lower() == "codex" for t in tokens[:2]) and "app-server" in tokens
        if is_core or is_bundle or is_app_server:
            found.append({"pid": int(pid), "ppid": int(ppid), "kind": "desktop-or-codex-executor"})
    return found


def quiet(wait_seconds=0):
    require(0 <= wait_seconds <= 1800, "Wait must be between 0 and 1800 seconds")
    deadline = time.monotonic() + wait_seconds
    empty_since = None
    while True:
        writers = process_writers()
        if not writers:
            if wait_seconds == 0:
                return
            if empty_since is None:
                empty_since = time.monotonic()
            if time.monotonic() - empty_since >= 3:
                return
        else:
            empty_since = None
        require(time.monotonic() < deadline, "Desktop/Codex executors are running; quit them normally and retry")
        time.sleep(0.5)


@contextlib.contextmanager
def lock_root(root):
    # Lock the existing directory inode: no lock file is created in Codex home.
    fd = os.open(str(root), os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("Another workaround writer holds the Codex home lock") from exc
        yield
    finally:
        os.close(fd)


def lifecycle(project, meta):
    require(not any(k in project for k in RELATED), "Local project has an active/retired pairing lifecycle")
    require(not any("codex." + k in meta for k in RELATED), "Database project has an active/retired pairing lifecycle")


def metadata_rows(conn):
    rows = []
    for db_id, raw in conn.execute("SELECT id,metadata FROM projects"):
        m = json.loads(raw)
        require(isinstance(m, dict), "Invalid project metadata")
        rows.append((db_id, raw, m))
    return rows


def journals(root):
    directory = Path(root) / JOURNALS
    require(directory.is_dir() and not directory.is_symlink(), "Provisioning directory missing or symlinked")
    result = []
    for p in sorted(directory.rglob("*.json")):
        regular(p)
        # Keep recovery paths within the provisioning tree, including parents.
        require(p.resolve().parent == directory.resolve(), "Nested provisioning journals need manual review")
        data = p.read_bytes()
        result.append((p.name, data, json.loads(data)))
    return result


def snapshot(root, local_id):
    root = Path(root).resolve()
    gp = regular(root / GLOBAL)
    gb = gp.read_bytes()
    global_state = json.loads(gb)
    project = global_state.get("local-projects", {}).get(local_id)
    require(isinstance(project, dict) and project.get("id") == local_id, "Local project not found by exact ID")
    require(isinstance(project.get("rootPaths"), list), "Unexpected local project shape")
    backing = project.get("chatGptBacking")
    require(isinstance(backing, dict), "Local project is already unlinked or has an unsupported link")
    require(all(isinstance(backing.get(k), str) and backing[k] for k in ("projectId", "accountId", "userId")), "Pairing identity is incomplete")
    cloud = backing["projectId"]
    globals_matching = [k for k, p in global_state["local-projects"].items()
                        if isinstance(p, dict) and isinstance(p.get("chatGptBacking"), dict)
                        and p["chatGptBacking"].get("projectId") == cloud]
    require(globals_matching == [local_id], "Cloud project maps to multiple local projects")
    with contextlib.closing(connect(root)) as conn:
        rows = metadata_rows(conn)
    matching = []
    for db_id, raw, m in rows:
        b = m.get("codex.chatGptBacking")
        db_backing = json.loads(b) if isinstance(b, str) else b
        if m.get("codex.localProjectId") == local_id or (isinstance(db_backing, dict) and db_backing.get("projectId") == cloud):
            matching.append((db_id, raw, m, db_backing))
    require(len(matching) == 1, "Expected exactly one database mapping for this local/cloud pair")
    db_id, raw, meta, db_backing = matching[0]
    require(meta.get("codex.localProjectId") == local_id and db_backing == backing, "Global and database pairings disagree")
    lifecycle(project, meta)
    matches = [(n, b, j) for n, b, j in journals(root)
               if isinstance(j, dict) and (j.get("localProjectId") == local_id or j.get("projectId") == cloud)]
    require(len(matches) == 1, "Expected exactly one matching provisioning journal")
    name, journal_bytes, journal = matches[0]
    require(journal.get("localProjectId") == local_id and journal.get("state") == "backed", "Provisioning journal has an unexpected state")
    require(all(journal.get(k) == backing[k] for k in ("projectId", "accountId", "userId")), "Journal identity disagrees with project pairing")
    return {"global_bytes": gb, "global": global_state, "project": project, "metadata_raw": raw,
            "metadata": meta, "db_id": db_id, "journal_name": name, "journal_bytes": journal_bytes}


def make_plan(root, bundle, local_id):
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    p = profile(root, bundle)
    s = snapshot(root, local_id)
    after_project = copy.deepcopy(s["project"])
    after_metadata = copy.deepcopy(s["metadata"])
    del after_project["chatGptBacking"]
    del after_metadata["codex.chatGptBacking"]
    return {"format": FORMAT, "tool_version": VERSION, "created_utc": utc(), "codex_home": str(root),
            "app_bundle": str(bundle), "profile": p, "write_supported": supported(p),
            "local_project_id": local_id, "database_project_id": s["db_id"],
            "before_project": s["project"], "after_project": after_project,
            "before_metadata": s["metadata"], "after_metadata": after_metadata,
            "journal_name": s["journal_name"], "journal_sha256": digest(s["journal_bytes"]),
            "before_sha256": {"project": object_hash(s["project"]), "metadata": object_hash(s["metadata"])},
            "after_sha256": {"project": object_hash(after_project), "metadata": object_hash(after_metadata)},
            "changes": ["remove local project chatGptBacking", "remove target database metadata codex.chatGptBacking", "archive one backed provisioning journal"]}


def check_plan(plan):
    require(plan.get("format") == FORMAT, "Unknown plan/manifest format")
    require(Path(plan["codex_home"]).is_absolute() and Path(plan["app_bundle"]).is_absolute(), "Plan paths must be absolute")
    require(Path(plan["journal_name"]).name == plan["journal_name"] and plan["journal_name"].endswith(".json"), "Unsafe journal name")
    bp, bm = plan["before_project"], plan["before_metadata"]
    ap, am = copy.deepcopy(bp), copy.deepcopy(bm)
    require(bp.get("id") == plan["local_project_id"] and bm.get("codex.localProjectId") == plan["local_project_id"], "Plan target identifiers disagree")
    backing = bp.get("chatGptBacking")
    require(isinstance(backing, dict) and json.loads(bm.get("codex.chatGptBacking", "null")) == backing, "Plan link fields disagree")
    lifecycle(bp, bm)
    del ap["chatGptBacking"]
    del am["codex.chatGptBacking"]
    require(plan["after_project"] == ap and plan["after_metadata"] == am, "Plan contains changes beyond unlinking")
    require(plan["before_sha256"] == {"project": object_hash(bp), "metadata": object_hash(bm)} and
            plan["after_sha256"] == {"project": object_hash(ap), "metadata": object_hash(am)}, "Plan digest mismatch")


def compare_snapshot(plan, s):
    require(s["db_id"] == plan["database_project_id"] and s["project"] == plan["before_project"]
            and s["metadata"] == plan["before_metadata"] and s["journal_name"] == plan["journal_name"]
            and digest(s["journal_bytes"]) == plan["journal_sha256"], "Target changed after planning; make a new plan")


def write_manifest(backup, manifest, phase, **extra):
    manifest.update({"phase": phase, "updated_utc": utc(), **extra})
    atomic(Path(backup) / "manifest.json", encode(manifest))


def backup_database(root, backup):
    fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with contextlib.closing(connect(root)) as src, contextlib.closing(sqlite3.connect(str(backup))) as dst:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    with open(backup, "rb") as stream:
        os.fsync(stream.fileno())
    fsync_dir(Path(backup).parent)


def threads_hash(conn):
    h = hashlib.sha256()
    count = 0
    for row in conn.execute("SELECT * FROM threads ORDER BY id"):
        h.update(encode(row))
        h.update(b"\n")
        count += 1
    return {"count": count, "sha256": h.hexdigest()}


def load_manifest(backup):
    m = read_json(regular(Path(backup) / "manifest.json"))
    check_plan(m["plan"])
    require(m.get("format") == FORMAT and m.get("backup_directory") == str(Path(backup).resolve()), "Manifest backup location differs; manual review required")
    jb = regular(Path(backup) / "journal.before.json").read_bytes()
    require(digest(jb) == m["plan"]["journal_sha256"], "Journal backup checksum mismatch")
    return m


def current_target(plan, conn):
    root = Path(plan["codex_home"])
    gb = regular(root / GLOBAL).read_bytes()
    g = json.loads(gb)
    p = g.get("local-projects", {}).get(plan["local_project_id"])
    require(isinstance(p, dict) and p.get("id") == plan["local_project_id"], "Target local project disappeared")
    require(p.get("rootPaths") == plan["before_project"].get("rootPaths"), "Target local project roots changed; refusing to restore its old pairing")
    row = conn.execute("SELECT metadata FROM projects WHERE id=?", (plan["database_project_id"],)).fetchone()
    require(row is not None, "Target database project disappeared")
    meta = json.loads(row[0])
    require(meta.get("codex.localProjectId") == plan["local_project_id"], "Target database project was rebound")
    root_keys = {k for k in set(meta) | set(plan["before_metadata"]) if re.search(r"root|cwd", k, re.I)}
    require(all(k in meta and k in plan["before_metadata"] and meta[k] == plan["before_metadata"][k] for k in root_keys),
            "Target database root identity changed; refusing to restore its old pairing")
    lifecycle(p, meta)
    return gb, g, p, row[0], meta


def known_field(obj, key, before):
    require(key not in obj or obj[key] == before, "Target link was changed by another writer; refusing to overwrite")


def no_competing_mapping(plan, global_state, conn):
    backing = plan["before_project"]["chatGptBacking"]
    for k, other in global_state.get("local-projects", {}).items():
        if k != plan["local_project_id"] and isinstance(other, dict):
            b = other.get("chatGptBacking")
            require(not isinstance(b, dict) or b.get("projectId") != backing["projectId"], "Cloud project has acquired another local mapping")
    for db_id, _, other in metadata_rows(conn):
        if db_id == plan["database_project_id"]:
            continue
        b = other.get("codex.chatGptBacking")
        b = json.loads(b) if isinstance(b, str) else b
        require(other.get("codex.localProjectId") != plan["local_project_id"] and
                (not isinstance(b, dict) or b.get("projectId") != backing["projectId"]), "Another database mapping conflicts with verification/recovery")
    for n, _, j in journals(plan["codex_home"]):
        if isinstance(j, dict) and (j.get("localProjectId") == plan["local_project_id"] or j.get("projectId") == backing["projectId"]):
            require(n == plan["journal_name"], "A new matching provisioning journal appeared")


def restore(backup, manifest):
    """Idempotently restore ONLY the two link fields and this journal.

    Permits either before/after fields, so interrupted operations can be recovered.
    SQLite backup is never restored over the live database.
    """
    plan = manifest["plan"]
    root = Path(plan["codex_home"])
    require_profile(root, plan["app_bundle"], plan["profile"])
    quiet()
    journal = root / JOURNALS / plan["journal_name"]
    archive = Path(backup) / "journal.archived.json"
    expected = regular(Path(backup) / "journal.before.json").read_bytes()
    require(digest(expected) == plan["journal_sha256"], "Journal backup checksum mismatch")
    # Validate every conflicting resource before changing any of them.
    if journal.exists():
        require(regular(journal).read_bytes() == expected, "Live recovery journal changed; refusing to overwrite")
    if archive.exists():
        require(regular(archive).read_bytes() == expected, "Archived recovery journal changed")
    backing = plan["before_project"]["chatGptBacking"]
    with contextlib.closing(connect(root, write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            gb, g, p, raw, meta = current_target(plan, conn)
            known_field(p, "chatGptBacking", backing)
            known_field(meta, "codex.chatGptBacking", plan["before_metadata"]["codex.chatGptBacking"])
            no_competing_mapping(plan, g, conn)
            before_threads = threads_hash(conn)
            quiet()
            require((root / GLOBAL).read_bytes() == gb, "Global state changed during recovery")
            write_manifest(backup, manifest, "restoring")
            p["chatGptBacking"] = copy.deepcopy(backing)
            meta["codex.chatGptBacking"] = plan["before_metadata"]["codex.chatGptBacking"]
            conn.execute("UPDATE projects SET metadata=? WHERE id=? AND metadata=?", (encode(meta).decode(), plan["database_project_id"], raw))
            atomic(root / GLOBAL, encode(g), stat.S_IMODE((root / GLOBAL).stat().st_mode))
            if not journal.exists():
                exclusive_bytes(journal, expected)
            quiet()
            require(threads_hash(conn) == before_threads, "Thread rows unexpectedly changed during recovery")
            require(conn.execute("PRAGMA quick_check").fetchone() == ("ok",), "SQLite quick_check failed")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    write_manifest(backup, manifest, "rolled_back", restored_only_target_fields=True)
    return {"phase": "rolled_back", "only_target_fields_restored": True, "cloud_requests_sent": 0}


def apply_plan(plan, backup, wait_seconds=0, failure_hook=None):
    check_plan(plan)
    root = Path(plan["codex_home"])
    backup = output_path(backup, root, backup=True, bundle=plan["app_bundle"])
    require(not backup.exists(), "Backup directory already exists; use verify/recover instead of repeating apply")
    require(backup.parent.is_dir(), "Backup parent directory must already exist")
    require(backup.parent.stat().st_dev == (root / JOURNALS).stat().st_dev, "Backup and provisioning directory must be on the same volume")
    quiet(wait_seconds)
    with lock_root(root):
        require_profile(root, plan["app_bundle"], plan["profile"])
        s = snapshot(root, plan["local_project_id"])
        compare_snapshot(plan, s)
        quiet()
        backup.mkdir(mode=0o700)
        atomic(backup / "global-state.before.json", s["global_bytes"])
        atomic(backup / "target-metadata.before.json", s["metadata_raw"].encode())
        atomic(backup / "journal.before.json", s["journal_bytes"])
        backup_database(root, backup / "state_5.before.sqlite")
        manifest = {"format": FORMAT, "tool_version": VERSION, "created_utc": utc(), "backup_directory": str(backup), "plan": plan}
        write_manifest(backup, manifest, "prepared")
        conn = connect(root, write=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            compare_snapshot(plan, snapshot(root, plan["local_project_id"]))
            require_profile(root, plan["app_bundle"], plan["profile"])
            quiet()
            gb, g, p, raw, meta = current_target(plan, conn)
            require(p == plan["before_project"] and meta == plan["before_metadata"], "Target changed before write")
            before_threads = threads_hash(conn)
            del p["chatGptBacking"]
            del meta["codex.chatGptBacking"]
            result = conn.execute("UPDATE projects SET metadata=? WHERE id=? AND metadata=?", (encode(meta).decode(), plan["database_project_id"], raw))
            require(result.rowcount == 1, "Database target changed before update")
            require((root / GLOBAL).read_bytes() == gb, "Global state changed before write")
            atomic(root / GLOBAL, encode(g), stat.S_IMODE((root / GLOBAL).stat().st_mode))
            write_manifest(backup, manifest, "global_written")
            if failure_hook:
                failure_hook("global_written")
            quiet()
            journal = root / JOURNALS / plan["journal_name"]
            require(regular(journal).read_bytes() == s["journal_bytes"], "Journal changed before archival")
            os.replace(journal, backup / "journal.archived.json")
            (backup / "journal.archived.json").chmod(0o600)
            fsync_dir(journal.parent)
            fsync_dir(backup)
            write_manifest(backup, manifest, "journal_archived")
            if failure_hook:
                failure_hook("journal_archived")
            quiet()
            require(json.loads((root / GLOBAL).read_bytes()) == g, "Global state changed during patch")
            require(threads_hash(conn) == before_threads, "Thread rows unexpectedly changed")
            require(conn.execute("PRAGMA quick_check").fetchone() == ("ok",), "SQLite quick_check failed")
            conn.commit()
            write_manifest(backup, manifest, "committed", thread_rows_unchanged_during_patch=before_threads)
            if failure_hook:
                failure_hook("committed")
            verification = verify(backup)
            require(verification["matches_expected_after"], "Post-commit target verification failed")
            write_manifest(backup, manifest, "complete", cloud_requests_sent=0)
            return {"phase": "complete", "backup_directory": str(backup), "cloud_requests_sent": 0}
        except Exception:
            conn.rollback()
            conn.close()
            try:
                restore(backup, manifest)
            except Exception as recovery_error:
                write_manifest(backup, manifest, "needs_review", recovery_error_type=type(recovery_error).__name__)
            raise
        finally:
            conn.close()


def verify(backup):
    backup = Path(backup).resolve()
    manifest = load_manifest(backup)
    plan = manifest["plan"]
    require(profile(plan["codex_home"], plan["app_bundle"]) == plan["profile"], "Version/schema changed since operation")
    with contextlib.closing(connect(plan["codex_home"])) as conn:
        _, g, p, _, meta = current_target(plan, conn)
        no_competing_mapping(plan, g, conn)
    before_p = plan["before_project"]["chatGptBacking"]
    before_m = plan["before_metadata"]["codex.chatGptBacking"]
    known_field(p, "chatGptBacking", before_p)
    known_field(meta, "codex.chatGptBacking", before_m)
    journal = Path(plan["codex_home"]) / JOURNALS / plan["journal_name"]
    archive = backup / "journal.archived.json"
    journal_ok = journal.exists() and digest(regular(journal).read_bytes()) == plan["journal_sha256"]
    archive_ok = archive.exists() and digest(regular(archive).read_bytes()) == plan["journal_sha256"]
    after = "chatGptBacking" not in p and "codex.chatGptBacking" not in meta and not journal.exists() and archive_ok
    before = p.get("chatGptBacking") == before_p and meta.get("codex.chatGptBacking") == before_m and journal_ok
    return {"phase": manifest["phase"], "matches_expected_after": after, "matches_expected_before": before,
            "local_project_preserved": True, "database_project_preserved": True,
            "mixed_state_needs_recovery": not (before or after), "cloud_requests_sent": 0}


def rollback(backup, wait_seconds=0):
    backup = Path(backup).resolve()
    manifest = load_manifest(backup)
    quiet(wait_seconds)
    with lock_root(Path(manifest["plan"]["codex_home"])):
        return restore(backup, manifest)


def inspect(root, bundle):
    p = profile(root, bundle)
    g = read_json(regular(Path(root) / GLOBAL))
    items = []
    for local_id, project in g.get("local-projects", {}).items():
        if not isinstance(project, dict):
            continue
        linked = "chatGptBacking" in project
        entry = {"local_project_id": local_id, "name": project.get("name"), "state": "linked" if linked else "unlinked"}
        if linked:
            try:
                snapshot(root, local_id)
                entry["three_way_pairing_consistent"] = True
            except (Refused, ValueError, KeyError, sqlite3.Error):
                entry["three_way_pairing_consistent"] = False
        items.append(entry)
    return {"tool_version": VERSION, "profile": p, "write_supported": supported(p), "projects": items,
            "note": "Linked does not mean erroneous. Select a confirmed unintended association by exact local ID."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--app-bundle", type=Path, default=Path("/Applications/ChatGPT.app"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="Read-only discovery; linked does not imply a bug")
    p = sub.add_parser("plan", help="Write a private, reviewable plan; never edit live state")
    p.add_argument("--local-project-id", required=True)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("apply", help="Apply one exact plan after normal app/executor exit")
    p.add_argument("--plan", required=True, type=Path)
    p.add_argument("--backup-dir", required=True, type=Path)
    p.add_argument("--wait-seconds", type=int, default=0)
    for name in ("verify", "rollback", "recover"):
        p = sub.add_parser(name)
        p.add_argument("--backup-dir", required=True, type=Path)
        if name != "verify":
            p.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        result = inspect(args.codex_home.resolve(), args.app_bundle.resolve())
    elif args.command == "plan":
        plan = make_plan(args.codex_home, args.app_bundle, args.local_project_id)
        out = output_path(args.out, plan["codex_home"], bundle=plan["app_bundle"])
        exclusive_json(out, plan)
        result = {"plan": str(args.out.resolve()), "write_supported": plan["write_supported"], "changes": plan["changes"], "contains_private_pairing_metadata": True}
    elif args.command == "apply":
        result = apply_plan(read_json(regular(args.plan)), args.backup_dir, args.wait_seconds)
    elif args.command == "verify":
        result = verify(args.backup_dir)
    else:
        result = rollback(args.backup_dir, args.wait_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if args.command == "verify" and result["mixed_state_needs_recovery"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Refused, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        # Raw JSON/account fields are deliberately not included in errors.
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
