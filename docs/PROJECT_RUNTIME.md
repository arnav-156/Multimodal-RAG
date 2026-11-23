# Runtime setup

The tested project runtime is CPython3.13.18 with SQLite3.53.1. The safety probe accepts the documented fixed SQLite lines and rejects vulnerable WAL builds. The exact Windows/Linux Python archive URLs, sizes and SHA256 values are in `PYTHON_DISTRIBUTIONS.json`; `RUNTIME_MANIFEST.json` contains portable observed identity/provenance.

From the project root, a bootstrap Python can run the standard-library provisioner:

```powershell
python tools/provision_runtime.py --destination .runtime/python-3.13.18 --output .verification/distribution.json
.\.runtime\python-3.13.18\python\python.exe -m venv .venv/patched
.\.venv\patched\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\patched\Scripts\python.exe tools/verify_runtime.py --output .verification/runtime.json
.\.venv\patched\Scripts\python.exe tools/verify_locked_environment.py --output .verification/packages.json
.\.venv\patched\Scripts\python.exe -m pip check
```

The provisioner verifies the archive before extraction and refuses an existing destination. It does not replace global Python, PATH or registry registrations. Skip provisioning an already verified installation. Optional model engines use separate environments described in their deployment guides.

On Linux the same provisioner selects the Linux archive; the extracted interpreter is `.runtime/python-3.13.18/python/bin/python3` and the virtual environment interpreter is `.venv/patched/bin/python`. Run commands from the project root and use the actual selected interpreter. Hosted CI is not attested merely by retaining its workflow.

`requirements.lock` pins the development/parser graph. Platform-specific OCR/audio/legacy locks also contain wheel hashes. Do not silently transfer a Windows wheel bundle to another platform. Node22 is needed for browser tests, not application serving. No npm dependency graph or frontend bundler is used.

Publisher provenance: [Python distribution release](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110). Runtime advisory: [SQLite WAL](https://sqlite.org/wal.html). Third-party attribution is preserved in [notices](../THIRD_PARTY_NOTICES.md).
