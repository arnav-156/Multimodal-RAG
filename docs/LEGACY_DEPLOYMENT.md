# Verified retained format profile

the local acceptance run used generated engineering fixtures, actual native converters, real Nomic embeddings and, for image-only DjVu, real local PaddleOCR/Qwen. Exact package hashes, native file hashes, versions and public RAR fixture provenance are in `LEGACY_DEPENDENCIES_MANIFEST.json`. No user documents were uploaded or personal roots selected. This remains a local pilot compatibility profile, not a production quality or parser-sandbox certification.

| Input | Verified behavior | Dependency / scope |
| --- | --- | --- |
| DOC | Binary Word97 extraction, including Café; real PDF/PNG rendering | MSYS2 native antiword0.37-3, UTF8 mapping; LibreOffice26.8.3/runtime26.8.3.1 for rendering |
| PPT | Binary PowerPoint97 text and two-page rendering | Pinned LibreOffice with declared input filter; catppt fallback remains unverified |
| XLS | BIFF8, two sheets, numeric120 and supplier text; real rendering | Core xlrd2.0.2; LibreOffice for rendering |
| DOCX/XLSX/PPTX | Existing real-model baseline retained; all six Office input filters render expected visible text | Existing core readers; native rendering uses the pinned LibreOffice profile |
| EPUB | UTF8 text, declared linear spine order; navigation cannot replace body text | Core EbookLib0.21 / BeautifulSoup; this does not certify every embedded-image ebook layout |
| FB2 | Well-formed FictionBook XML/body text | Stdlib XML parser; basic generated UTF8 book; no full XSD certification |
| MOBI/AZW3 | Unencrypted generated text; AZW3 native conversion and actual MOBI fallback | Optional mobi0.4.1 overlay; Calibre9.18.0 optional native AZW3 converter |
| RTF | Basic ASCII paragraphs; encoded Unicode/hex controls reject with conversion guidance | Restricted basic adapter. Convert encoded/complex RTF to DOCX or plain UTF8 text |
| DjVu | Exact native page text, text/OCR hybrid, actual image-only vision recovery | DjVuLibre3.5.29 Windows; pinned OCR/vision profiles linked below |
| ZIP/TAR/TAR.GZ | Complete bounded member names/sizes | Stdlib; member payloads are not extracted or indexed |
| 7Z/RAR | Complete bounded listings, including spaces/Unicode in the public RAR fixture | Pinned7-Zip26.06; encrypted/unreadable containers fail |
| GZ/BZ2/XZ | Complete bounded readable text payload | Stdlib; this is distinct from archive member listings |
| ODT/ODS/ODP | Explicit unsupported result on genuine generated files | Recognition does not imply an extraction adapter |

The actual success matrix had17 native sources, then one image-only DjVu recovered through Qwen, with27 valid stored vectors. Three unsupported formats and three deliberately broken/encrypted sources remained explicit; the latter had zero chunks and a partial job. Real hybrid DjVu used page1 native text and page2 PaddleOCR; a blank second page failed without partial chunks. HTTP and browser evidence is in the private local validation archive. Small fixtures do not establish representative production relevance; the local acceptance run owns that evaluation.

## Local runtime

The verified cache is `.runtime/legacy-converters`, outside the base Python environment. Native packages came from official [LibreOffice](https://www.libreoffice.org/download/), [Calibre](https://calibre-ebook.com/download_portable), [DjVuLibre](https://djvu.sourceforge.net/), [7-Zip](https://github.com/ip7z/7zip/releases) and [MSYS2 antiword](https://packages.msys2.org/package/mingw-w64-ucrt-x86_64-antiword) records. The LibreOffice MSI was administratively extracted into the cache; no global application install was required. Calibre's portable installer reached an error dialog; the verified official resource was extracted with a checked LZIP/ZIP path instead. Both original installer and extraction evidence are retained.

For the optional MOBI/AZW3 Python reader, install the exact hash-locked overlay into a separate directory using the patched interpreter:

```powershell
.\.venv\patched\Scripts\python.exe -m pip install --require-hashes --target .runtime/legacy-converters/python -r requirements-legacy-windows-py313.lock
```

the private local validation archive contains the verified wheels for an optional local-only installation. The XLS fixture writer xlwt1.3.0 is separate, test-only, and is not a runtime requirement. The40-pin base graph remains unchanged. Both base and overlaid pip consistency checks passed. No Linux optional-reader/native-converter deployment is certified here.

Use an isolated shell/session for native tools, preserving your existing PATH:

```powershell
$legacyRoot=(Resolve-Path .runtime/legacy-converters).Path
$legacyBins=@('antiword/ucrt64/bin','djvu','calibre-native/Calibre Portable/Calibre','libreoffice-26.8.3/program','7zip') | ForEach-Object { Join-Path $legacyRoot $_ }
$env:PATH=($legacyBins -join [IO.Path]::PathSeparator)+[IO.Path]::PathSeparator+$env:PATH
$env:PYTHONPATH=(Join-Path $legacyRoot 'python')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$env:ANTIWORDHOME=Join-Path $legacyRoot 'antiword/ucrt64/share/antiword'
.\.venv\patched\Scripts\python.exe run_pipeline.py --config your-profile.yaml scan
.\.venv\patched\Scripts\python.exe run_pipeline.py --config your-profile.yaml process
```

Calibre configuration/temp directories can also be isolated with CALIBRE_CONFIG_DIRECTORY and CALIBRE_TEMP_DIR, as in the evidence harness. OCR and vision remain separately enabled local services: see [OCR_DEPLOYMENT.md](OCR_DEPLOYMENT.md) and [VISION_DEPLOYMENT.md](VISION_DEPLOYMENT.md). The app's defaults still select no personal folders and no vision endpoints.

## Bounds and migration

Office commands use an explicit source-format filter and a unique user profile. Auto-detection previously converted a truncated Word container into a blank PDF that vision marked recovered; the declared Word filter now rejects it. The the local acceptance run ownership follow-up terminates the exact owned converter tree on exceptions/deadlines before output draining and parent reaping; unrelated processes/model services are excluded. See JOB_LIFECYCLE.md for the60s command deadline and bounded cleanup grace.

DjVu page count is checked even when text exists; native page dimensions/pixel cap are checked before decoding. Its renderer uses supported PPM output at explicit native dimensions, then PNG conversion. Every page has exact text/OCR provenance. Missing pages reject the whole source; limits do not silently clamp the document.

Archive listings have a40000-character bound and the configured uncompressed byte limit. Standalone compressed text is complete, bounded by the minimum of40000bytes, max_archive_uncompressed_bytes and max_text_file_bytes; oversized/binary payloads fail rather than becoming a partial preview. Listings do not verify/extract member payloads or certify nonstandard ZIP filename encodings. Native parsers are not an OS sandbox.

Extraction policy version5 schedules ordinary reprocessing of older source states. Generation policy version2 includes native DjVu sizing, declared Office filters and the DjVu page cap; earlier known descriptions become stale until regenerated/drained. This is a conservative global generation-policy change and can regenerate otherwise unchanged descriptions. Embedding semantics, vector codec and DB schema are unchanged; no model-contract rebuild is needed solely for this parser update. Converter/font binaries are not automatically attested by the generic generation client: keep this pinned deployment, and treat external converter upgrades as a new declared generation deployment revision followed by verification/reprocessing.
