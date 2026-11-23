# Local service setup

Choose one explicit embedding profile. Model assets, licenses and service processes are provisioned separately; application processing does not start, kill or download an embedding service.

| Capability | Profile | Guide / manifest |
|---|---|---|
| Nomic file retrieval | `config.ollama.example.yaml` | `MODEL_MANIFEST.json`; install the exact configured local Ollama model digest |
| Jina file retrieval | `config.jina-tei.example.yaml` | [Jina](JINA_DEPLOYMENT.md); `JINA_MODEL_MANIFEST.json`; singleton containment required |
| CPU OCR | `config.ocr-cpu.example.yaml` | [OCR](OCR_DEPLOYMENT.md); `OCR_MODEL_MANIFEST.json` |
| English ASR | `config.audio-faster.example.yaml` or `config.audio-cpp.example.yaml` | [Audio](AUDIO_DEPLOYMENT.md); `AUDIO_MODEL_MANIFEST.json` |
| Optional vision descriptions | `config.vision-ollama.example.yaml` | [Vision](VISION_DEPLOYMENT.md); `VISION_MODEL_MANIFEST.json` |
| Native legacy formats | Explicit converter environment | [Legacy formats](LEGACY_DEPLOYMENT.md); `LEGACY_DEPENDENCIES_MANIFEST.json` |

Commands and paths in these guides assume the project root. Copy examples to ignored private working profiles; explicitly select roots and a database. Manifest project paths are relative; `<OLLAMA_MODELS>` denotes the operator's existing Ollama cache. Those records do not bundle checkpoints or grant their license terms through the project's MIT license.

Use local-only policy and loopback endpoints. Use the same private local token for an OCR service and its app. Keep credentials out of committed profiles. Check readiness and actual controlled queries after provisioning; an enabled optional capability is not a quality certificate for every file or platform.
