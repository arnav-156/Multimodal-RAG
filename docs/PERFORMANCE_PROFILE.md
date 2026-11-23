# Performance verification

Measure the whole configured path: scan/extract/embed/commit, authenticated search, source eligibility, SQL/vector decoding, response serialization and the separately owned model service. Record cold versus warm conditions and concurrency; preserve errors and rejected work.

Existing controlled tools run from the project root:

```powershell
.\.venv\patched\Scripts\python.exe tools/verify_resources.py --output .verification/resources
.\.venv\patched\Scripts\python.exe tools/verify_local_model.py --output .verification/local-model
.\.venv\patched\Scripts\python.exe tools/verify_http.py --run .verification/local-model
.\.venv\patched\Scripts\python.exe tools/benchmark_capacity.py --source-run .verification/local-model --output .verification/capacity
```

Check each tool's `--help` for its exact arguments before use. Real-model tools need the separately provisioned compatible endpoint; they use generated fixture sources. Capacity data repeats actual vectors into artificial rows and must not be counted as relevance evidence. Generated outputs belong in ignored local directories, not a public commit.

Use paired alternating comparisons against identical inputs/weights/contracts; report query/source-group uncertainty, resource sampling boundaries, median/tails, throughput and failure counts. Keep SQL ordering and int8 codec contract regressions intact. Do not infer a code speedup from a different corpus, a warm cache, a provider allocation figure or a source change that also changes ranking quality.

The local implementation retained two measured simple changes: ordering the joined scan by its indexed file key and avoiding redundant finite-array validation of signed-byte vectors. A historical generated-capacity comparison measured API median1738.8→900.3ms; small sequential queries were effectively flat. These are fixture-specific historical observations, not a field SLA or a reason to add ANN/caches automatically.

An8.5-minute local operating check returned740 ranking-identical searches across single-client, four-client, idle and recovery phases. Four clients had lower throughput on that fixture. Its87.8MiB sampled peak included the API, load generator and sampler in one process and excluded model processes. Raw internal diagnostics are archived outside the public source release; a new host/corpus needs fresh measurement. See [limitations](KNOWN_LIMITATIONS.md).
