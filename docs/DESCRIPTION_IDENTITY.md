# Description generation and embedding identity

Schema2 adds nullable source, generation and embedding fingerprints to descriptions. Migration preserves existing text/vectors; unknown provenance stays unknown. Back up first. Older application versions reject this newer schema.

`description_contract.py` identifies the enabled fallback model chain, declared revisions, output limits, prompt and renderer policy. Secrets, timeouts and retry/rate settings are excluded. Without a declared revision, the deployment URL contributes to identity. Declared vision revisions are configuration provenance, not runtime attestation of remote weights.

Generation changes refresh affected selected/renderable sources, with live-root and source-version guards. Vision settings do not invalidate ordinary text extraction. New generation identities get a fresh retry budget; unchanged identities do no extra generation/embedding work.

Embedding-only changes require the explicit incompatible-index rebuild. Current generated text can be preserved and embedded again. The drain checks index/model compatibility and compares source/generation/text again at commit. Description chunks record both identities and retain their negative-index representation.

Search, statistics and readiness exclude stale description vectors while retaining rows for review. Remaining stale work makes an outcome partial. A bounded generation pass can require another pass.

Unknown legacy descriptions are not sent automatically to a new endpoint. To regenerate from authorized source files, use `process --regenerate-legacy-descriptions` with the intended private profile; the process API and standalone describer expose the corresponding flag. This does not certify old text and is separate from `--force`.

A standalone drain lazily owns one finite embedding client and closes it on every exit path. Injected clients remain caller-owned. Empty/revoked work creates no client. Extractor policy5 refreshes old source processing; renderer policy2 covers Office filters and bounded DjVu rendering. External converter/font changes require an intentional declared/verified deployment revision.
