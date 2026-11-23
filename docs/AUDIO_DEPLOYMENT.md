# Verified local English audio adapters

the local acceptance run verified both advertised adapters on actual locally synthesized English WAV/MP3 audio, using local Nomic embeddings for retrieval. No ASR or embedding substitute contributed to acceptance. The two spoken passages and compressed copy are a small engineering fixture; they do not establish production or multilingual transcription quality.

| Adapter | Verified runtime/model |
| --- | --- |
| faster-whisper | 1.2.1, CTranslate2 4.9.0, PyAV18.1.0, CPU `int8_float32`, two threads; Systran/faster-whisper-tiny.en revision `0d3d19a32d3338f10357c0889762bd8d64bbdeba` |
| whisper.cpp | Official Windows CPU v1.9.2 binary; ggerganov/whisper.cpp revision `5359861c739e955e79d9a303bcbc70fb988958b1`, `ggml-tiny.en.bin`; GPU disabled, two threads, native non-speech suppression enabled |

`AUDIO_MODEL_MANIFEST.json` records model-file hashes, official binary archive/extracted-file hashes, runtime versions and the existing ffprobe executable's provenance. The stable [whisper.cpp release](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2) archive checksum was verified before execution. The [Systran model card](https://huggingface.co/Systran/faster-whisper-tiny.en) identifies an English-only converted Whisper checkpoint and MIT license. This deployment does not call an OpenAI API or require API credentials.

## Faster-whisper setup

Use the separate environment; the app lock is unchanged:

```powershell
.\.runtime\python-3.13.18\python\python.exe -m venv .venv/audio
.\.venv\audio\Scripts\python.exe -m pip install --only-binary=:all: --require-hashes -r requirements-audio-windows-py313.lock
.\.venv\audio\Scripts\python.exe -m pip check
```

The 24-distribution Windows/Python3.13 graph pins **PyAV18.1.0** deliberately. Actual installation with PyAV19 failed because faster-whisper1.2.1 passes a removed argument; [upstream records that incompatibility](https://github.com/SYSTRAN/faster-whisper/issues/1589). No site-package monkeypatch is used.

Provision the manifest's exact model/tokenizer/config files under the example path. Copy `config.audio-faster.example.yaml` beside the app, choose database/explicit roots, then use the **app** environment for scan/extract-meta/process/serve. `audio.python_executable` points its owned inference child at the isolated environment. Default empty selection preserves the existing `sys.executable` behavior. Local-files-only and offline environment settings remain enforced.

## Whisper.cpp setup

The verified executable/DLL bundle is cached in `.runtime/whisper-cpp-v1.9.2/Release/`; model weights are cached under the manifest's pinned model directory. Copy `config.audio-cpp.example.yaml`, configure explicit roots/database and run the same app CLI. `cpp_executable` and `cpp_model_path` must be supplied together. Explicitly configured missing/failing assets raise an error rather than silently switching engines. Existing unconfigured legacy discovery/fallback behavior is retained for compatibility; it is not the basis of this deployment's proof.

Executable paths must be edited in local YAML and then reloaded/restarted. The browser configuration API rejects changing executable paths; ordinary audio settings remain editable. Inference uses argv lists with no shell invocation. The current text-output adapter reports language `unknown` for whisper.cpp; the tested checkpoint is English-only. Faster-whisper reports `en` in the actual run.

## Admission, ownership and limits

`ffprobe` is required to establish a positive finite source duration before either backend runs. Missing probe, corrupt input, NaN/infinite/nonpositive duration and configured over-duration inputs fail closed. The observed existing tool was ffprobe9.0.2; no global install was performed.

Each transcription runs in one owned subprocess with a finite deadline. Actual1ms deadline tests reaped both backend children; output directories were removed on success/failure/timeout, and a real input-adjacent note was preserved. Whisper.cpp's native `-sns` option prevents its blank-audio token being indexed as speech. Silence and corrupt fixtures fail without chunks. This is not a general guarantee against ASR hallucination on noise or difficult recordings.

The example profiles use a30-second transcription deadline,60-second admission limit and two CPU threads. Only short fixtures were exercised in the local acceptance run; near-limit latency/memory requires fresh workload-specific measurement. Cancellation/shutdown can wait for the current file or its deadline; the app starts no next source after cancellation. No persistent audio service is introduced.

Both adapters produced three valid768-dimensional stored vectors from three speech files, with two deliberate source failures. WERs were0,0.211,0 per adapter under the predeclared0.35 fixture ceiling. The nonzero score came from writing spoken “two hundred and forty” as “240”; the numeric meaning remained correct. WER, silence failures, duration/deadline checks, real retrieval and browser inspection are recorded in the private local validation archive. Five discovered files versus three searchable chunks is expected because two fixtures deliberately fail. Do not add these ASR cases to the earlier text-only relevance baseline.
