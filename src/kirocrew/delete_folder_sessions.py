#!/usr/bin/env python3
"""Batch-delete every chat session filed into a given folder (e.g. 'bin').

THREE STAGES — deletion never happens without an explicit confirmation token.

  Stage 1  (no args)                 -> list every folder: name / id / member count
  Stage 2  (--folder bin)            -> DRY-RUN: list the session titles that WOULD be
                                        deleted from that folder. Deletes nothing.
  Stage 3  (--folder bin --confirm DELETE)
                                     -> PERMANENTLY delete those sessions via the
                                        official ConversationLog.delete_session()
                                        (file-lock + sidecar cleanup). Irreversible.

Uses ONLY KiroCrew's own APIs:
  - folder registry  : config_dir()/folders.json  (id <-> name)
  - session listing   : ConversationLog().list_sessions()  (exposes folder_id)
  - deletion          : ConversationLog().delete_session(key)  (official hard delete)

RUN with the bundled interpreter (system python lacks kiro_crew):
  /Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 \
      /Users/miles/.kiro/crew/workspace/delete_folder_sessions.py                 # stage 1
      ... --folder bin                                                            # stage 2
      ... --folder bin --confirm DELETE                                           # stage 3
"""
import argparse
import json
import sys
from datetime import datetime

from kiro_crew.history import ConversationLog
from kiro_crew.dashboard.state import config_dir


def load_folders() -> list[dict]:
    path = config_dir() / "folders.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"!! could not read {path}: {e}", file=sys.stderr)
        return []
    # folders.json is a list of {id, name, ...}
    return data if isinstance(data, list) else data.get("folders", [])


def fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", help="Folder NAME (e.g. bin) to target — case-insensitive")
    ap.add_argument("--confirm", help="Pass the literal token DELETE to delete without prompting")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip the interactive prompt and delete (same as --confirm DELETE)")
    args = ap.parse_args()

    log = ConversationLog()  # no-arg: resolves its own sessions Path
    folders = load_folders()
    sessions = log.list_sessions()

    # ── Stage 1: no folder given → list all folders ──
    if not args.folder:
        print("=== FOLDERS (config_dir()/folders.json) ===")
        if not folders:
            print("  (no folders defined)")
        # count members per folder id
        counts: dict[str, int] = {}
        for s in sessions:
            fid = s.get("folder_id")
            if fid:
                counts[fid] = counts.get(fid, 0) + 1
        for f in folders:
            fid = f.get("id", "")
            print(f"  name={f.get('name','?')!r:40}  id={fid}  members={counts.get(fid, 0)}")
        print("\nNext: rerun with  --folder <name>  to DRY-RUN that folder's members.")
        return 0

    # Resolve folder name -> id (case-insensitive)
    want = args.folder.casefold()
    match = [f for f in folders if str(f.get("name", "")).casefold() == want]
    if not match:
        print(f"!! no folder named {args.folder!r}. Run with no args to list folders.")
        return 2
    if len(match) > 1:
        print(f"!! {len(match)} folders share the name {args.folder!r}; ids: "
              f"{[f.get('id') for f in match]}. Refusing to guess.")
        return 2
    fid = match[0]["id"]

    members = [s for s in sessions if s.get("folder_id") == fid]
    members.sort(key=lambda s: s.get("modified", 0))

    print(f"=== Sessions in folder {args.folder!r} (id={fid}) : {len(members)} ===")
    for s in members:
        print(f"  [{fmt_time(s.get('modified'))}]  {s.get('title', s.get('key'))!r}")
        print(f"        key={s.get('key')}")

    if not members:
        print("  (folder is empty — nothing to delete)")
        return 0

    # ── Decide whether to delete ──
    non_interactive = (args.confirm == "DELETE") or args.yes
    if not non_interactive:
        # Interactive prompt. If stdin is not a TTY (piped), refuse to delete.
        if not sys.stdin.isatty():
            print(f"\nDRY-RUN (no TTY). Nothing deleted. {len(members)} session(s) above.")
            print(f"To delete non-interactively: --folder {args.folder} --yes")
            return 0
        try:
            ans = input(f"\nPermanently delete ALL {len(members)} session(s) above? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted. Nothing deleted.")
            return 0
        if ans not in ("y", "yes"):
            print("Aborted. Nothing deleted.")
            return 0

    # ── Real delete ──
    print(f"\n!! PERMANENTLY DELETING {len(members)} session(s) from folder {args.folder!r} ...")
    ok, fail = 0, 0
    for s in members:
        key = s.get("key")
        try:
            if log.delete_session(key):
                ok += 1
                print(f"  deleted: {s.get('title', key)!r}")
            else:
                fail += 1
                print(f"  NOT removed (lock/timeout or already gone): {key}")
        except Exception as e:
            fail += 1
            print(f"  ERROR deleting {key}: {e}")
    print(f"\nDone. deleted={ok}  failed={fail}")
    print("Restart KiroCrew to clear them from the sidebar/Older Sessions view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
