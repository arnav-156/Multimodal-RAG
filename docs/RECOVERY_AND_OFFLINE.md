# Offline installation and recovery

Before going offline, cache the exact pinned Python distribution, platform-compatible wheels, native tools and model assets required by the chosen profile. The application never downloads model weights automatically. Optional OCR/audio/legacy environments are separate from the base app.

From the project root, prepare and consume a same-platform wheel bundle:

```powershell
.\.venv\patched\Scripts\python.exe -m pip download -r requirements.lock -d wheelhouse
# On the offline machine, using the matching patched interpreter/environment:
.\.venv\patched\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.lock
.\.venv\patched\Scripts\python.exe -m pip check
.\.venv\patched\Scripts\python.exe tools/verify_locked_environment.py --output .verification/offline-packages.json
```

Verify publisher checksums before transferring/installing artifacts. Where a hash lock is provided, use `--require-hashes`. The portable version lock and platform-specific wheel hash locks serve different purposes. A Python wheel bundle does not provision an inference service, checkpoint or native converter.

## Backup and failed rebuild

An incompatible embedding-contract rebuild with `process --force` clears derived vectors/statuses before rebuilding. It is not an atomic old-index/new-index swap and does not automatically roll back. Use an operator-managed maintenance window.

1. Record selected roots, database location and exact embedding/generation configuration. Take a new online SQLite backup; an existing backup destination is refused.
2. Stop serving and ingestion writers and wait for owned jobs to finish. Keep them stopped during rebuild/restore.
3. Run the intended rebuild. Inspect actual exit/job status, source failures, vector counts, integrity/foreign keys and labeled queries before reopening.
4. On failure, preserve the failed database. Copy the immutable backup to a new, nonexistent path; use the original compatible profile with that database path. Never copy only the main file of a live WAL database or overwrite the only backup.
5. Check CLI queries, unchanged resume, HTTP search/preview/download and source identity before reopening normal use.

```powershell
.\.venv\patched\Scripts\python.exe run_pipeline.py --config my-config.yaml backup backups/library-before-rebuild.db
# Stop the service and all writers before rebuilding or restoring.
$backupPath='backups/library-before-rebuild.db'
$restoredPath='recovery/library-restored.db'
if (Test-Path -LiteralPath $restoredPath) { throw 'Choose a new restore path' }
Copy-Item -LiteralPath $backupPath -Destination $restoredPath
# Set database.path to the new path in a copy of the compatible original profile.
.\.venv\patched\Scripts\python.exe run_pipeline.py --config restored-profile.yaml init-db
```

A database backup does not include sources, model assets, configuration or tokens. Protect those separately. Moved/changed sources need rescan/reprocessing; source paths must remain under selected live roots. Restoring does not certify unknown description provenance. See [description identity](DESCRIPTION_IDENTITY.md).
