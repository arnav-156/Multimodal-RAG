# Format capability policy

`format_capabilities.py` is the lightweight source of extension classification and metadata/extraction/render applicability. It imports no parser, model or database clients. Existing extractor constants/classification remain compatible, and search need not import heavy extraction adapters.

Recognition is distinct from reader availability and quality acceptance. Missing applicable optional decoders/readers fail explicitly; no dependency or capability is silently installed. Parent metadata admission and the direct timeout wrapper reject non-applicable work before spawning a parser.

TypeScript defaults to code; transport-stream sync bytes override it to video. PDFs have text/image/hybrid metadata classification. SVG is recognized as image but has no Pillow rendering path. OpenDocument is recognized as Office while extraction remains explicitly unsupported; video semantic extraction is unsupported.

Office input filters are shared by extraction/rendering. Failed/truncated containers cannot fall back to raw bytes or blank successful documents. EPUB follows its linear spine; DjVu requires complete bounded page coverage. Archive containers expose bounded safe member listings; standalone GZ/BZ2/XZ expose complete bounded readable text. Basic RTF rejects unsupported encoded controls explicitly.

Description selection counts renderable candidates toward its limit so earlier nonrenderable work cannot starve valid sources. See [legacy format deployment](LEGACY_DEPLOYMENT.md) for optional native tools and compatibility limits.
