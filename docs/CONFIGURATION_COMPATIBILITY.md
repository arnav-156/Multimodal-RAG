# Effective settings and retained compatibility

the local acceptance run reduces the shipped default sample and deletes proven private dead code; it deliberately retains the validated schema and old values. Settings displays a compatibility notice before the JSON editor. Saving still validates, preserves redacted secrets by endpoint identity, and atomically writes YAML. No field is silently discarded or renamed by this cleanup.

| Compatibility-only fields | Meaning in this application |
| --- | --- |
| scanner.home_paths, scanner.everything_exe | Do not select roots or launch Everything. Use scanner.scan_paths. home_paths still resolves as a path list for round trips. |
| metadata.log_file | Accepted/resolved; does not configure the metadata logger. |
| embedding.model_path | Accepted/resolved; public get_jina_model_path alias remains. The client talks to an externally provisioned service; this field does not load local weights. |
| embedding.vllm.port/dtype/gpu_memory_utilization/max_model_len | Old service-launch hints; no service is launched. embedding.vllm.api_url/model_name are **active fallback aliases** when embedding.api_url/model are empty. |
| pipeline.extractor_threads/audio_threads/chunk_queue_size/stream_batch_size/max_retries | No scheduler effect. Processing is serial, pending reads use **pipeline.fetch_limit**, metadata uses metadata workers/limits, vision retries use **vision.max_retries**. |
| vision.max_workers, vision.endpoints[].max_parallel | No parallel generation scheduler. Generation is finite/serial; endpoint rate_limit and timeout remain active. |
| logging.dir/level | No application logging configuration is installed from these fields; public get_log_dir still explicitly creates/returns its path when called. |
| monitor.directories/include_types/exclude_types | No watcher or ingestion filter. Manual scan roots and format capability policy apply. |
| embedding.allow_cloud, vision.endpoints[].allow_cloud | Compatibility flags, not authorization. Actual egress requires security.local_only=false, security.allow_cloud=true and the matching endpoint's cloud_authorized=true, including live revocation checks. Security fields/values remain untouched. |
| embedding.int8_scale | Fixed validated invariant127, not an inactive adjustable scale. vectors.py owns the codec; alternate values reject. |

These values can still appear in the full JSON editor because old profiles must round-trip. The default YAML omits inactive scheduling/launch/watch/logging entries; their omission does not change the effective configuration or hash contract. Public config helper aliases and description_embedder.quantize_embedding remain for possible external callers. Only uncalled private extraction code (_get_whisper, _is_likely_music_only, _OCR_LOCK and their exclusive state/imports) was removed. Active _init_whisper_cpp and local-files-only ASR remain.

| Effective setting group | Actual runtime readers |
| --- | --- |
| server and security | server.py session/middleware/main, security.py Host/Origin/endpoint policy, config_loader.py boundary validation |
| database.path and scanner.scan_paths/skip_dirs/skip_files | database.py, corpus_policy.py and file_scanner.py; source I/O checked against selected/live roots |
| metadata.workers_per_disk/file_timeout/max_pending | metadata_extractor.py bounded scheduling/spawn deadline |
| embedding.provider/model/revision/dimension/native_dimension/query_prefix/document_prefix/api_url/api_key/cloud_authorized/timeout/batch_size/max_input_chars | jina_v5_embedding.py validated protocol/identity/requests and vectors.py contracts; security.py transport checks |
| embedding.description_batch_size | description_embedder.py finite drain |
| ocr settings | ocr_server.py/ocr_worker.py model/service/request admission, extractors.py client and OCR routing; port controls the separately started OCR listener |
| audio settings | extractors.py explicit local runtime/model, CPU threads, deadline and duration |
| chunking and limits | chunker_v2.py, extractors.py, describer.py, djvu_reader.py, office_process.py and jobs.py admission/resource/version checks |
| vision.endpoints identity/enabled/auth/model/revision/max_tokens/timeout/rate_limit and vision.max_retries | describer.py selection, live egress validation, request generation/retry and identity |
| pipeline.fetch_limit | run_pipeline.py finite high-water source pages |
| search.default_top_k/max_top_k | search_numpy.py, server.py validation and static/app.js controls |

Legacy migrations remain: scan→scanner, chunk→chunking, scanner.db_path→database.path (when no authoritative database section), metadata.workers→workers_per_disk, metadata.timeout→file_timeout, embedding.dim→dimension, embedding.vllm_url→vllm.api_url. Authoritative keys win. Existing DB_PATH/LOG_DIR/JINA_MODEL_PATH/OCR_API_URL/RAG_PORT/RAG_HOST/VLLM_API_URL/VLLM_MODEL_NAME overrides remain; RAG_CONFIG is the startup selector, while an explicitly selected profile stays selected. See OPERATION_SETTINGS.md. Neither aliases nor environment overrides grant cloud permission.

Historical run artifacts are retained outside the public source release; rerun the supported acceptance tools for a new deployment.
