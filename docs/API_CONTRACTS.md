# API contracts

Current source: `server.py`. A freshly exported [OpenAPI schema](openapi.json) accompanies these contracts. Browser documentation routes remain disabled.

All `/api/` routes except session operations and liveness require a valid bearer token or signed HttpOnly/SameSite=Strict cookie. Host/origin validation, CSP and no-store headers apply. Credentials are not query parameters. The service is intended for loopback.

| Method | Path | Contract |
|---|---|---|
| GET/POST/DELETE | /api/session | Check authentication, sign in with token, sign out |
| GET | /api/live | Public liveness only |
| GET/PUT | /api/config | Redacted schema-based settings; optional If-Match revision protects conditional saves; stale revisions return 412 |
| POST | /api/config/reload | Reload the owned configuration; admission conflicts remain explicit |
| GET | /api/config/status and /api/health | Ready-to-ingest versus ready-to-search, selected corpus and service diagnostics |
| GET | /api/stats | Selected, current index counts |
| POST/GET | /api/search | POST query or GET q; top_k, file_types, path, offset, page_size; ranked files, total, eligible_files and invalid-vector count |
| POST | /api/pipeline/{kind} | Scan/extract/process admission returns 202 and a persistent job identity; duplicate admission is rejected |
| GET | /api/pipeline/status | Durable job states/counters/diagnostics |
| POST | /api/pipeline/jobs/{jid}/cancel | Cooperative cancellation; current atomic file may finish |
| GET | /api/documents/{fid} | Indexed text only for an eligible current source |
| GET | /api/documents/{fid}/download | Original bytes after source identity and path checks |
| GET | / and /config | Two native HTML product routes |

The source inventory contains 19 method/path operations, including the two HTML routes. Search results contain bounded snippets, metadata and document URLs; they do not contain generated answers or calibrated confidence. Source eligibility, pagination and error ordering retain their existing tests.

Error families: 401 authentication, 403 boundary/eligibility, 404 missing resource, 409 admission conflict, 412 stale conditional config, 422 invalid input/contract, and 503 unavailable model/database. Consult the schema/source for exact endpoint variants.
