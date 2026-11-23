# Model card

## Intended system

Local document/file retrieval using separately served pretrained encoders. Text, optional OCR/ASR and visual descriptions become bounded text chunks. The index stores normalized int8 vectors; search returns the maximum chunk cosine per eligible file. There is no training or generated-answer product endpoint.

| Profile | Pinned identity / representation | License record |
|---|---|---|
| Nomic | Ollama `nomic-embed-text:latest`, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`,768 dimensions, explicit search roles, F16 model artifact | Apache2.0 as recorded for the upstream model; `MODEL_MANIFEST.json` |
| Jina | `jinaai/jina-embeddings-v5-text-small-retrieval`, revision `6856e76bb72982e58de0620458a4e8b3614da340`,1024 native/512 output, last-token pool | CC-BY-NC4.0 as recorded in `JINA_MODEL_MANIFEST.json`; singleton containment required |
| OCR/ASR/vision | Optional separately provisioned pinned components | See modality manifests and [service setup](docs/SERVICE_SETUP.md) |

The application's MIT license does not relicense model weights, native tools or evaluation data. Assets are not distributed with this source release. Publisher revisions, hashes and original license references are retained in the portable manifests and [third-party notices](THIRD_PARTY_NOTICES.md).

## Evaluation evidence and limits

Local engineering acceptance exercised real ingestion, persistence, retrieval, inspection, source downloads and optional modality adapters on controlled fixtures. Passing regressions validates those contracts; it is not a model-quality score or proof of representative sampling.

An adaptation of the author-published FinQA public test used all380 supplied unique page excerpts and one lowest-SHA256-question-ID question per company,100 companies. Only pre-text, uniformly serialized table rows and post-text entered opaque indexed files; questions, answers, gold and supplied retrieval results were excluded. The authors' source row IDs supplied evidence units. Four source/gold serialization discrepancies were retained rather than silently excluded.

| Method | Source-page Recall@5 | MRR@5 | nDCG@5 |
|---|---:|---:|---:|
| Dense Nomic | .800 | .6873 | .7154 |
| BM25, k1=1.2/b=.75 | .930 | .8000 | .8330 |
| Fixed equal RRF60 | .910 | .7903 | .8205 |

The dense−BM25 Recall difference was−.13, paired company-bootstrap95%[−.21,−.06], exact sign p=.00235. The fusion comparison was retrospective/exploratory on exposed labels: fusion−BM25−.02[−.07,+.03], Holm sign p=.6875. No default promotion followed. Source-page annotations can omit valid alternatives; company grouping does not prove statistical independence. Public old datasets may have appeared in model pretraining. These are retrieval-adaptation observations, not official FinQA numerical-answer scores, calibrated probabilities or cross-domain quality claims.

Historical raw diagnostics, machine paths, fixture tokens and local databases are preserved privately and are not part of this public source release. Those measurements are not represented as independently reproducible from this source export alone. `tools/finqa_benchmark.py` retains label-independent conversion/sampling and the lexical baseline; use independent reviewed target data for new comparisons. No independent cross-domain quality result is asserted.

`tools/evaluate_evidence.py` separates exact quote provenance, complete supplied evidence units and external answer-bound semantic review. It never infers semantic correctness solely from an authentic fragment. Its significance-design preflight rejects mathematically impossible sign/Holm or ceiling targets but does not establish prospective power.

See [known limitations](docs/KNOWN_LIMITATIONS.md) before making production, robustness, calibration, novelty or multilingual accuracy claims.
