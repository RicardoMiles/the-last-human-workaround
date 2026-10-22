# Keep the Kiro fixes working

A portable version of the maintenance checks I use for my Big Sur setup. Python 3.8 or later, standard library only. File locking needs macOS.

The tool does not download updates, sign binaries, build shims or restart services. It also does not install the CLI app wrapper. Keep your existing launch arrangement.

## Set the paths

Use your own absolute paths:

```sh
export KIRO_FIX_DIR="/path/to/cli-fix"
export CREW_VENV="/path/to/crew-venv"
export KIRO_APP="/Applications/Kiro CLI.app"
export LLAMA_FIX_DIR="/path/to/patched-libs"
```

`KIRO_FIX_DIR` holds the real `kiro-cli-chat` copy and `kiro_stream_shim.dylib`. `LLAMA_FIX_DIR` holds the patched native libraries and both Crew shims. Keep it outside the venv.

## Check first

```sh
python3 kiro_maintenance.py status
python3 kiro_maintenance.py check
```

These commands are read-only. They do not start Kiro, create backups or change locks. On macOS, `check` also uses `otool` to check that the two patched libraries point at their shims.

## Maintenance

```sh
python3 kiro_maintenance.py unlock --acknowledge-file-flags
python3 kiro_maintenance.py backup-cli
```

Then perform the update yourself. For CLI, prepare the non-hardened candidate separately. The tool checks it with your shim before replacing the working copy:

```sh
python3 kiro_maintenance.py refresh-cli --candidate /path/to/prepared/kiro-cli-chat
```

For Crew, restore the library override after an update:

```sh
python3 kiro_maintenance.py reapply-crew
python3 kiro_maintenance.py check
```

Verify normal startup yourself, then choose whether to lock the known working setup:

```sh
python3 kiro_maintenance.py lock --acknowledge-file-flags
```

Locking is optional. Unlock before updating. A locked package can make an updater fail partway through its work. The tool never locks the whole fix root, backup folder or gateway log folder.

## What the guards cover

| Problem | Guard | Recovery |
| --- | --- | --- |
| Candidate is a wrapper or tiny file | Reject before replacement | Choose a real, prepared Mach-O binary |
| Candidate fails under the shim | Check exit status and version output before replacement | Keep the old copy and inspect the failure |
| Working files are locked | Refuse writes | Unlock deliberately before maintenance |
| Crew launcher changed upstream | Require the expected import anchor | Inspect the new launcher before adapting the injection |
| Override already points elsewhere | Refuse conflicting configuration | Decide which native library folder to use |
| A previous copy is needed | Keep backups without automatic pruning | Select and verify a backup before restoring it |

## Verification status

This is a new portable helper. Its read-only `check` passed against my Big Sur setup, including the original launcher override and both patched dependency paths. It was run through stdin and was not installed there. The original launcher, locks and repair scripts remain in use.

The case records the original setup's checks. Two temporary test harnesses cover the portable helper's rejection paths, backups, rollback, idempotent injection and bounded locks. These use isolated fixtures and mocked process results. An additional fixture checks real macOS immutable-file behavior. A full live upgrade and rollback still need a separate test.

[Case and measured limits](../../cases/kiro-bigsur-missing-symbols.md)
