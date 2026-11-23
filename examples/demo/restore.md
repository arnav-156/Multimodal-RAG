# Database recovery guide

To recover the document library, restore a verified SQLite online backup.
Stop application writers first. Copy the backup to a new database path,
run PRAGMA integrity_check, and reopen the service only after it returns ok.
Keep the original database until the restored library has been checked.

This fictional document is part of the public search demonstration.
