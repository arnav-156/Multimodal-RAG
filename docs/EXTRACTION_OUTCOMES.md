# Extraction outcomes and persistence

`extractors.extract_outcome` returns chunks, operation-local diagnostics, an optional typed error and recovery eligibility. The compatibility adapter returns chunks or raises the recorded error. Extractors do not mutate SQLite or own a database path.

Ingestion owns source-version-checked status/chunk commits and OCR recovery diagnostics. It rechecks source identity and live authorization around extraction, inference and persistence. A source replaced concurrently cannot commit stale output or overwrite newer status/description state.

Empty, corrupt, unsupported or incomplete structured input must not become successful context-only/raw-byte chunks. Missing optional readers/services are explicit failures. OCR/vision recovery remains bounded and source-authorized; partial file extraction is not silently committed after a required page failure.

PDFs preserve native page text and use bounded OCR for raster content when enabled. With OCR disabled, raster content is marked skipped. Page limits, render-pixel limits, archive-member checks and extracted-character limits apply independently of the downstream embedding budget. Tests use explicit substitutes for model contracts; actual modality quality requires separate fixture/data acceptance.
