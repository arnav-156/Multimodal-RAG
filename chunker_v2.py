#!/usr/bin/env python3
"""Bounded semantic text chunks. Overlap is included in the hard character budget."""
import re


def chunk_greedy_semantic(text: str, ext: str = '', max_chars: int | None = None):
    from config_loader import get_chunking_config
    settings = get_chunking_config()
    limit = max_chars if max_chars is not None else settings['max_chars']
    ratio = settings.get('overlap_ratio', 0.05)
    if not isinstance(limit, int) or limit < 2:
        raise ValueError('max_chars must be an integer of at least 2')
    if not 0 <= ratio < 0.5:
        raise ValueError('overlap_ratio must be between 0 (inclusive) and 0.5')
    if not text or not text.strip():
        return []
    text = text.strip()
    overlap = min(int(limit * ratio), limit - 1)
    chunks, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            # Preserve nearby semantic boundaries without sacrificing a hard bound.
            lower = start + limit // 2
            boundary = text.rfind('\n\n', lower, end)
            if boundary >= lower:
                end = boundary + 2
            else:
                boundary = text.rfind('\n', lower, end)
                if boundary >= lower:
                    end = boundary + 1
                else:
                    boundaries = list(re.finditer(r'[.!?。！？]\s+', text[lower:end]))
                    if boundaries:
                        end = lower + boundaries[-1].end()
        chunks.append(text[start:end])
        if end == len(text):
            break
        # One-sided overlap keeps every original character and never expands a chunk.
        start = max(start + 1, end - overlap)
    return chunks


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--max-chars', type=int, default=None)
    args = parser.parse_args()
    for index, chunk in enumerate(chunk_greedy_semantic(args.path.read_text(encoding='utf-8'), args.path.suffix, args.max_chars)):
        print(index, len(chunk))
