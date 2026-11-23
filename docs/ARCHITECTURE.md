# Architecture

The application is a modular monolith: FastAPI and the CLI share configuration, corpus eligibility, extraction, ingestion and exact retrieval code. Two static HTML routes use vanilla JavaScript. SQLite WAL stores file versions, bounded chunks, normalized int8 vectors, descriptions, job states and index/model contracts.

## Data flow

1. `file_scanner.py` reconciles explicit roots, hashes source content and invalidates derived output when content changes. Unavailable roots do not imply deletion.
2. `metadata_extractor.py` dispatches supported metadata readers with bounded work and native-process deadlines.
3. `extractors.py` returns chunks and diagnostics; optional local OCR/ASR/vision services provide text. Extractors do not own database commits.
4. `run_pipeline.py` verifies source versions and permissions before inference and commit. It writes one file atomically, preserving newer concurrent source state.
5. `jina_v5_embedding.py` implements explicit query/document roles, checks identity/capability and rejects truncation or malformed vectors. `vectors.py` owns normalized int8 serialization.
6. `search_numpy.py` applies current corpus eligibility, validates stored vectors, computes bounded exact cosine batches and returns the best chunk per file with deterministic ties and pagination.

## Ownership and consistency

Each operation uses one validated settings snapshot. Live root revocation is rechecked before source access and persistence. Semantic embedding identity is separate from operational endpoint settings; description generation identity is separate from its embedding state. See [settings](OPERATION_SETTINGS.md), [corpus policy](CORPUS_POLICY.md), [embedding contract](EMBEDDING_CONTRACT.md) and [description identity](DESCRIPTION_IDENTITY.md).

`jobs.py` owns admission, durable outcomes, cancellation and shutdown. One ingestion writer operates per database. Cancellation completes an in-flight atomic file before starting no next file. Scanning stages metadata before a short reconciliation transaction; long extraction/inference does not hold a database writer lock. See [job contract](JOB_CONTRACT.md), [lifecycle](JOB_LIFECYCLE.md) and [transactions](TRANSACTION_LIFETIMES.md).

The browser assigns visible query, pagination, preview and error state to the latest request intent. Settings use dirty-state protection and revision preconditions; late responses cannot overwrite a newer interaction. See [browser ownership](BROWSER_REQUEST_OWNERSHIP.md).

## Boundaries

Models, native converters and optional environments are operator-provisioned and remain external to the application process. Source permissions and identity are checked for indexed inspection and downloads. Local-only egress, token authentication, host/origin validation and inert rendering are defense boundaries, not a native-parser OS sandbox.

The modular monolith, SQLite and exact reference search fit the current single-user pilot. ANN, caches, a queue service, online index generation swaps and microservices require a measured workload need and compatibility/quality gate. No answer generation, training loop, watcher or multi-user upload service is implied. See [limitations](KNOWN_LIMITATIONS.md) and [API](API_CONTRACTS.md).
