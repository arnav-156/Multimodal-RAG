# Repository sanitization report

21 November 2026, with publication review updated 23 November 2026. **CLEAN WITH CAVEATS.** The original cleanup performed no commit, push, application-repository initialization, history rewrite, branch/tag deletion or attribution transfer. The subsequent publication request explicitly authorizes committing this reviewed source and its working demonstration to `arnav-156/Multimodal-RAG`.

## Scope and inventory

The complete local workspace inventory covered96,564 files,11,014 directories and about8.3GiB of file content. Eighty Windows long-path lookup failures were resolved with extended-length paths; no inventory lookup remains unresolved. The public file set is enumerated and hashed in [PUBLICATION_MANIFEST.json](PUBLICATION_MANIFEST.json). The source-only ZIP uses fixed neutral timestamps and contains only that reviewed set; it excludes local installations and private history.

## Classification and cleanup

| Finding | Decision / result |
|---|---|
| Dated audit, experiment, database and fixture trees | REMOVE from publication:13 internal evidence trees preserved in a reversible sibling archive outside the application root |
| Superseded matrices, checklists and internal reports | REMOVE from publication:40 standalone reports archived; no original report bytes erased |
| Maintainer contracts and deployment guides | KEEP/REPLACE:23 useful guides relocated to `docs/`, reviewed for current behavior and repaired references |
| Original README/model/runtime observations | REPLACE:portable setup, accurate model card/catalog and current limitations replace stale status banners and workstation paths |
| Audit-only report generator | REMOVE from publication:one unreferenced historical generator archived with its inputs; reusable acceptance and evaluation tools retained |
| Unused imports | REMOVE:six statically unused module imports in three files; reference scan and AST comparison confirm no other logic change |
| Dependencies, compatibility exports, optional models | KEEP:dependency pins/profiles and required compatibility aliases retained; no speculative dependency/module deletion |
| Local environments/runtime/model caches | KEEP locally, excluded from Git candidates and source export; no user model, environment or backup was deleted |
| Generated caches and logs | REMOVE from publication:reversibly archived; future caches/logs/verification output are ignored |

The private per-path inventory, classification, original-source snapshot, move/deletion manifest and restoration guidance remain outside the publication root. Paths archived rather than destroyed can be restored from those records. Space savings refer to the publication boundary, not erased disk content.

## Ownership, identity and attribution

The supplied project license/copyright was preserved byte-for-byte. Upstream package/model URLs and license declarations are intentional attribution. The adapted financial table-row helper retains the FinQA copyright and MIT notice in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No ownership was reassigned or invented.

Personal workstation paths were removed from public manifests and documentation while preserving publisher revisions, licenses and artifact hashes. The inspected runtime/static source had no AI-agent author signature requiring removal. Internal session-style histories were archived; useful tests, workflows and implementation code were retained regardless of how they were produced. Regression identifiers remain useful test labels.

## Metadata, watermarks and hidden content

The original publication candidates were inspected as UTF8 text. The 23 November demonstration adds two actual browser JPEG captures, visually reviewed for credentials and private content and checked for EXIF, XMP, comments and alternate streams. The visible filesystem locations refer only to the project and its public sample files. No PDF/Office/model payload is included. JSON/YAML/HTML metadata and source comments were inspected. Three unnecessary download-origin alternate streams on text files were privately backed up and removed; their default file bytes, including the license, were unchanged. Export copies only default data streams and supplies neutral ZIP metadata.

Ignored vendor/runtime/model files and archived experimental documents were inventoried and excluded, not destructively stripped or claimed to have undergone a complete binary/watermark audit. Publisher build timestamps and required names retained in manifests are reproducibility/attribution metadata.

## Security and privacy

The reviewed publication set has no identified private path, agent signature, live credential format, private token file, database, working profile or local evidence log. Test-only sentinels are deliberately retained. Ignore rules cover environment secrets, token files, local profiles, databases, assets, caches and validation output; `.env.example`, source, licenses and useful documentation remain eligible. Root examples still select no personal scan directories. Local-only, authentication and source-permission behavior were not weakened.

This is a bounded pattern/content and publication-boundary check, not proof that every possible secret encoding or external repository history is clean. Keep local working profiles private and review the exact manifest before staging.

## Git history

Neither supplied project root originally contained Git metadata or a remote, so prior commits/authors/branches/tags/removed historical files could not be inspected. On 23 November the user supplied `https://github.com/arnav-156/Multimodal-RAG`; the connected GitHub API confirmed it was public and had no branches. Publication therefore begins new history without replacing existing commits. The supplied license remains unchanged; absent original history prevents independent reconstruction of complete authorship provenance.

## Verification

- Final Python suite:321 passed; zero failures/errors/skips; one upstream deprecation warning.
- JavaScript behavior suite:34 passed; JavaScript syntax check passed.
- Real cached-model ingestion/retrieval, source-change/deletion, restart and authenticated HTTP checks:22 HTTP cases passed, including inspection/download/jobs/backup; owned services stopped afterward.
- CLI help, imports/AST syntax, pinned dependency consistency, examples/metadata parsing, public links, ignore rules and source/asset preservation were checked. Application logic and public API were preserved; only six unused imports were removed.
- No separate linter, type-check configuration or frontend production bundler exists; none was invented and no nonexistent build was claimed.

## Caveats and handoff

The verified publication set is clean within the inspected scope. Local ignored assets and the private sibling archive intentionally remain outside it. Git history and original authorship beyond the supplied license remain unverified because they were absent. This cleanup does not upgrade the [model quality](MODEL_CARD.md), native-parser isolation or production/accessibility guarantees described in [known limitations](docs/KNOWN_LIMITATIONS.md).

Publish from the application root containing `run_pipeline.py`, not its parent workspace or private archive. The requested GitHub publication includes source, the five fictional demo files, a portable demo profile and two real browser captures. Local model assets, demo databases, credentials and logs remain ignored. No additional research-data download retry is scheduled.
