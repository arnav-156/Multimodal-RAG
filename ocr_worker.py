#!/usr/bin/env python3
"""Standalone OCR v3 worker using the same configured engine/result contract."""
import contextlib
import json
import sys
from ocr_server import init_worker, ocr_single_image, validate_image_paths


def main():
    values = sys.argv[1:] or json.load(sys.stdin)
    paths = validate_image_paths(values)
    with contextlib.redirect_stdout(sys.stderr):
        init_worker()
        results = [ocr_single_image(path) for path in paths]
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__': main()
