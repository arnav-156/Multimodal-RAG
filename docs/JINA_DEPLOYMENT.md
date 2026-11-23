# Verified Jina CPU deployment

**19 November 2026 correction:** the pinned TEI Candle Qwen3 backend can change vectors for equal-token-length multi-sequence batches. Concurrent singleton requests can also trigger it. The supported containment is application `batch_size: 1` **and** service `--max-client-batch-size 1 --max-concurrent-requests 1`. The command below includes both caps. `--max-batch-requests 1` alone is ineffective: this image overwrites it with4 during startup. Extra client batches are rejected; overlapping requests may receive429 (the application reports provider failure as503). This is a correctness/throughput tradeoff, not a model quality or speed improvement. Actual failing and contained runs: [current limitations](KNOWN_LIMITATIONS.md), [current limitations](KNOWN_LIMITATIONS.md). The upstream defect remains; do not enable concurrency/batching without a new actual semantic proof.

The pinned merged retrieval checkpoint was exercised locally. This is a local document-search pilot; OCR, audio and vision acceptance remains separate.

- Model: `jinaai/jina-embeddings-v5-text-small-retrieval`, revision `6856e76bb72982e58de0620458a4e8b3614da340`.
- Weights: 1,192,133,232 bytes, SHA-256 `0362107c2b13e18284c5152c4d4f667a4dec4665abdfe4f5e43a9bb799ba2276`.
- Service: Hugging Face Text Embeddings Inference CPU 1.9.4, image `ghcr.io/huggingface/text-embeddings-inference@sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d`.
- Execution: Candle Qwen3 CPU, float32, last-token pooling, 1024 native dimensions, client leading-512 Matryoshka reduction and L2 normalization. Query/document prefixes are distinct.
- License: the [publisher model card](https://huggingface.co/jinaai/jina-embeddings-v5-text-small-retrieval) specifies CC BY-NC 4.0. Model licensing is separate from application licensing.

`JINA_MODEL_MANIFEST.json` records all eight publisher file hashes, service metadata, deployment overlay and acceptance evidence. Files are cached under `models/jina-embeddings-v5-text-small-retrieval/6856e76bb72982e58de0620458a4e8b3614da340/`. Publisher files were verified against Hub metadata; the weights have an LFS SHA-256 and small files have Git blob hashes. No user documents were transmitted during provisioning or tests.

The checkpoint advertises a native context of 32768 tokens. This tested deployment deliberately caps input at **512 tokens**, using a locally authored `sentence_bert_config.json` containing `{"max_seq_length":512}`. This sidecar is explicitly distinguished from publisher assets in the manifest. The cap bounds CPU warmup and memory; full native context and vLLM/CUDA are unverified. Oversized requests return 422, with automatic truncation disabled. Application character limits are an additional bound, not a token-count guarantee.

## Start the cached service

Run from the application directory with Docker available. The owned verification container was stopped and removed after acceptance; no service is left running by the local acceptance run. This command uses the cached, verified artifacts and pinned image:

```powershell
$jinaModelPath = (Resolve-Path 'models/jina-embeddings-v5-text-small-retrieval/6856e76bb72982e58de0620458a4e8b3614da340').Path
docker run --rm --name local-rag-jina --memory 4g --cpus 4 -p 127.0.0.1:8001:80 --mount "type=bind,source=$jinaModelPath,target=/model,readonly" -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e OMP_NUM_THREADS=4 ghcr.io/huggingface/text-embeddings-inference@sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d --model-id /model --served-model-name jinaai/jina-embeddings-v5-text-small-retrieval --revision 6856e76bb72982e58de0620458a4e8b3614da340 --dtype float32 --pooling last-token --max-batch-tokens 512 --max-batch-requests 4 --max-client-batch-size 1 --max-concurrent-requests 1 --tokenization-workers 2 --auto-truncate false
```

The measured container used about 2.38 GiB of its 4 GiB limit. CPU timing depends on load. The client validates `/info` model/revision/pooling and rejects automatic truncation; TEI does not need to emulate `/v1/models`. `config.jina-tei.example.yaml` is an explicit embedding profile. Copy and configure your own database and selected roots for normal ingestion; the default configuration is unchanged. Existing incompatible indices require an intentional rebuild.

## Repeat acceptance into new evidence directories

```powershell
.\.venv\patched\Scripts\python.exe -B tools/verify_jina_semantics.py --profile config.jina-tei.example.yaml --output .verification/jina-protocol.json
.\.venv\patched\Scripts\python.exe -B tools/verify_local_model.py --embedding-profile config.jina-tei.example.yaml --output .verification/jina
```

These tools refuse existing output destinations and use controlled fixtures. Re-run them after provisioning or before changing the pinned runtime; small fixture results do not establish production relevance, multimodal quality or concurrency correctness.

The [TEI source revision](https://github.com/huggingface/text-embeddings-inference/tree/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67) matches the inspected image labels.
