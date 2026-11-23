# Current corpus eligibility

`corpus_policy.py` shares selected-root membership across counts, readiness, search, processing, metadata and descriptions. Membership requires both the operation's admitted roots and the current published roots. Removing a root revokes work; adding one does not broaden an admitted snapshot.

Owned SQLite connections register the lexical boundary predicate before limits/aggregation. Selected files are distinct from searchable output: chunks require active selected membership, done status and matching source versions. Model compatibility and malformed-vector checks remain separate.

Before source hashing, parsing/rendering, inference or attachment access, `source_allowed` checks live membership, resolved boundaries and symlink/junction ancestors. A revoked path is rejected before filesystem inspection. Already running third-party calls cannot be recalled; subsequent guarded work is prevented. This is application policy, not an OS sandbox against hostile parsers or local administrators.

Root removal retains indexed rows and pending work but excludes them from selected counts and inference. Reselecting the root makes still-current output eligible. An empty selected corpus returns no results without query inference; readiness distinguishes ingest capability from a searchable current selection.
