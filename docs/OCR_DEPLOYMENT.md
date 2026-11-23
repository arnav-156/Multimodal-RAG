# Verified local CPU OCR deployment

20 November update: native PDF text no longer suppresses OCR of raster content on the same page when OCR is enabled. Native text is retained and full-page OCR is appended with `pdf_mixed` page provenance, under the existing render/page limits. OCR-disabled raster pages carry `raster_ocr_skipped` metadata. Native-only pages without raster images keep their existing route. A native page with an image now requires the configured OCR service when enabled and may incur additional latency or duplicate recognized text. Extractor fingerprint5 refreshes old processing on the next `process` run; back up first. Actual controlled acceptance and remaining complex-layout limits are in [current limitations](KNOWN_LIMITATIONS.md) and [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

the local acceptance run exercised actual PaddleOCR 3.3.2 / PaddlePaddle 3.2.2 / PaddleX 3.3.13 on Windows x86-64, patched Python 3.13.18. The optional service uses a separate environment; the application's 40-package lock is unchanged. `requirements-ocr-windows-py313.lock` pins all 64 OCR distributions and wheel hashes. Linux/GPU and other runtime combinations are not certified by this run.

Publisher assets are cached in `models/ocr/` and recorded in `OCR_MODEL_MANIFEST.json`:

| Component | Exact publisher revision | Weights SHA-256 |
| --- | --- | --- |
| PP-OCRv5_mobile_det | `0d63e78e2b680928f6b1747d76a08db6e645efb7` | `afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b` |
| PP-OCRv5_mobile_rec | `682f20538d8c086cb2128e5cfac775e6c4904e85` | `2460da90875937c94db97eba74ae3d9e5d4c4c57c42f1f41531c09a26bcc771a` |

Ten publisher files were verified using Hub Git/LFS hashes, with local SHA-256 values recorded. The [detector](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) and [recognizer](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec) model cards specify Apache-2.0. Their model identities come from local `inference.yml`; the service explicitly supplies those names to PaddleOCR. The [pinned upstream implementation](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.3.2/paddleocr/_pipelines/ocr.py) ignores `lang` when local model paths are supplied, so language is determined by the recognition checkpoint. Health reports that distinction instead of claiming a language switch.

## Run

After provisioning the manifest's exact local model files, run from the app directory:

```powershell
.\.runtime\python-3.13.18\python\python.exe -m venv .venv/ocr
.\.venv\ocr\Scripts\python.exe -m pip install --only-binary=:all: --require-hashes -r requirements-ocr-windows-py313.lock
.\.venv\ocr\Scripts\python.exe -m pip check
$env:PADDLE_PDX_CACHE_HOME=(Join-Path (Get-Location) 'models/ocr/cache')
$env:HF_HUB_OFFLINE='1'
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True'
$env:OMP_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
.\.venv\ocr\Scripts\python.exe -B ocr_server.py --config config.ocr-cpu.example.yaml
```

The example selects no directories. For ingestion, copy it to a working profile, select explicit roots and database, and use the patched **app** environment for scan/extract-meta/process/serve. The explicit embedding profile is the separately verified Nomic profile; this does not change the project's default Jina configuration. Use the same private local token for the OCR service and app, through their shared database token file or an existing `RAG_API_TOKEN` environment value. No command above prints or generates an external credential.

The service binds loopback only and starts listening after actual engine construction. Missing model identity/assets fail startup. Auxiliary orientation/unwarping models are disabled. Device fallback is disabled rather than silently accepting a different device. The measured profile uses two CPU threads, one request slot and a serial shared engine; increasing slots does not parallelize model inference.

## Transport and operating limits

The current app sends base64 image bytes in `image_data`; the service also retains the `images` path API for existing callers. Upgrade/restart both app and service together. Inline bytes remove cross-process temporary-file ownership: a client timeout no longer tries to delete an image still open in the OCR process on Windows. The tradeoff is additional encoded bytes and memory.

`ocr.max_request_bytes` defaults to **16 MiB**, including encoded content; base64 adds roughly one third to raw size. Image count, per-image bytes and decoded pixel limits also apply. A batch or individual image beyond the encoded budget fails explicitly; reduce batch size or deliberately adjust the request budget within the validated bounds. Large-image/near-limit throughput remains the local acceptance run. The tested clean fixtures are much smaller than those limits. Request slots reject concurrent excess work with 429; malformed/ambiguous/oversized requests return 400, unauthenticated input401 and forbidden legacy paths403.

A client timeout does not kill an externally owned service operation already in progress. In the actual test the caller returned in about 22 ms, no temporary files remained, and the real service recovered its request slot afterward. Cancellation completed the current file and started no next file. The model process peaked around **618 MiB** working set in this fixture. These measurements are local operating evidence, not general memory/latency guarantees.

## Acceptance scope

Actual clean English and Simplified Chinese PNG recognition passed. Scanned PDFs covered both OCR pages; hybrid PDFs preserved text-page and OCR-page provenance. Five successful source files produced seven chunks. Deliberately corrupt image, blank missing PDF page and excessive render dimensions each failed without committing partial chunks. Actual Nomic retrieval and browser inspection displayed both scanned pages. Missing-service and missing-model failures, timeout, cancellation, authentication, saturation and cleanup were exercised.

Handwriting, rotation, noisy scans, other languages, GPU execution and production OCR quality remain unverified. Runtime verification evidence is not a persistent remote-service attestation: the app health response still distinguishes enabled OCR from globally certified multimodal readiness. Owned verification services were stopped after tests; cached models and optional environment remain available. See the private local validation archive and `OCR_MODEL_MANIFEST.json`.
