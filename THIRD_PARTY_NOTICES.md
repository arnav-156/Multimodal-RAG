# Third-party notices

The project [MIT license](LICENSE) and existing contributor attribution are retained unchanged. Dependencies, optional services and checkpoints retain their own licenses; declaring or installing them does not relicense them as project code. No third-party binary, checkpoint or dataset is included in the public source package.

## Evaluation row serialization

The financial table-row serialization follows the corrected template from [FinQA](https://github.com/czyssrs/FinQA/tree/0f16e2867befa6840783e58be38c9efb9229d742). The upstream attribution/license is preserved below for that adapted helper. FinQA data is not bundled.

MIT License

Copyright (c) 2021 Zhiyu Chen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Optional assets and dependencies

- Python runtime distribution provenance: `PYTHON_DISTRIBUTIONS.json` and `RUNTIME_MANIFEST.json`; the distribution contains its upstream license notices when installed locally.
- Nomic: upstream model reference and declared Apache2.0 record in `MODEL_MANIFEST.json`.
- Jina retrieval checkpoint: pinned publisher reference and declared CC-BY-NC4.0 record in `JINA_MODEL_MANIFEST.json`; see the deployment guide for required singleton containment.
- Paddle OCR assets, Whisper adapters and optional vision checkpoint: source revisions, declared licenses and hashes in their corresponding manifests. No model weights are redistributed here.
- Optional LibreOffice, Calibre, DjVu, antiword, FFmpeg and archive tools: keep their publisher notices with any separately provisioned binary distribution. Sources/hashes are recorded in `LEGACY_DEPENDENCIES_MANIFEST.json` and `AUDIO_MODEL_MANIFEST.json`.
- Python packages and GitHub Actions are referenced by their upstream package/action names and pinned versions. Their third-party names, URLs and required notices are intentional attribution, not abandoned project ownership.
