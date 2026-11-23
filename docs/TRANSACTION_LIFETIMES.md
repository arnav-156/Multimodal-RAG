# Transaction lifetimes

Scanning writes a complete root's metadata/hashes to an owned disk-backed SQLite spool before opening the short main reconciliation transaction. Source bytes are not copied into it. The spool is excluded from scanning; normal, error and cancellation paths close/remove it. An incomplete root never becomes authoritative.

Reusable stages check actual schema/version/tables/indexes and migrate only as needed; explicit startup/init retains initialization ownership. Duplicate admission checks read live owners before recovery writes; the unique active-job index is the concurrent admission guard.

Processing captures a high-water source ID and fetches bounded pages using `pipeline.fetch_limit`. Source connections close before extraction/inference. New IDs wait for the next operation. Source-version guards reject stale commits after concurrent replacement/deletion.

Spooling adds temporary disk I/O, and filesystem changes after staging still require source-hash checks and later scans. A forced crash can leave an owned metadata-only temporary directory for operator/OS cleanup; no broad system-temp purge is performed. Measure representative contention, WAL size and resources before adding another queue or staging layer.
