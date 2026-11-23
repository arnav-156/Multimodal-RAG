# Embedding contract

The client stores versioned `embedding_contract` JSON and its fingerprint in index metadata. It identifies provider, artifact revision and pooling, native/output dimensions, query/document roles, L2 normalization, dimension reduction and signed-int8 codec. Effective constructor overrides are represented.

A verified revision/pooling identity allows endpoint relocation with the same semantic contract. Credentials, endpoint, timeout and batch size are operational settings. Unverified identities retain a deployment binding. An OpenAI-compatible service may advertise revision, pooling and native dimension; conflicting dimensions are rejected. Different provider contracts remain distinct.

The input character budget belongs to processing validation, not vector identity. Budget changes require source revalidation while preserving no-truncation checks; they do not alone justify destructive model rebuilding.

Nonempty legacy/incompatible indexes fail conservatively. Back up and deliberately use `process --force` with the intended compatible profile for a rebuild; no automatic equivalence migration or rollback occurs. See [recovery](RECOVERY_AND_OFFLINE.md). The pinned Jina backend additionally requires singleton admission; a compatible identity does not prove batch/concurrency semantic invariance.
