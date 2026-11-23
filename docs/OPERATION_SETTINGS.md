# Operation settings

Configuration getters/load/save/reload return private copies. Mutating a returned dictionary does not publish it; save/reload publishes a validated revision.

CLI/reusable scan, metadata, ingestion, search, extraction and description entry points capture one revision; nested calls inherit it. Embedded callers can use `operation_settings(validated_settings)` and explicit database arguments. Admission captures context/credential environment before starting a worker; metadata children receive primitive inputs and a captured timeout.

The single-worker HTTP server serializes settings mutation with admission and rejects it during durable active work. External YAML edits require reload/restart. Existing standalone operations retain their snapshot even if an embedded save publishes new settings. Each non-loopback inference request must satisfy captured and latest global/endpoint authorization; revocation cannot recall an already sent request. This is not distributed YAML locking across multiple servers.

An explicitly selected profile remains selected for later reads/reload/save. `RAG_CONFIG` is the startup selector when none is selected; reset restores environment/default selection. Call `load_config(new_path)` to switch deliberately. Documented per-field environment overrides remain effective. Tests isolate their fallback profile/database and exclude inherited service settings.
