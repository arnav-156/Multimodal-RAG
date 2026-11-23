#!/usr/bin/env python3
"""Authenticated loopback PaddleOCR v3 service with explicit local model assets.

Models must already be provisioned: no implicit inference-model download is allowed.
"""
import argparse
import base64
import binascii
import io
import hmac
import json
import logging
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from config_loader import get_config

log = logging.getLogger(__name__)
_ocr = None
_engine_info = None
_model_lock = threading.Lock()
_request_slots = threading.BoundedSemaphore(1)


def init_worker():
    """Initialize once before readiness, honoring the configured device and assets."""
    global _ocr, _engine_info
    _ocr = None
    _engine_info = None
    cfg = get_config()['ocr']
    root = Path(cfg['model_dir']) if cfg.get('model_dir') else None
    detection = cfg.get('detection_model_dir') or (str(root / 'det') if root else '')
    recognition = cfg.get('recognition_model_dir') or (str(root / 'rec') if root else '')
    if not detection or not recognition or not Path(detection).is_dir() or not Path(recognition).is_dir():
        raise RuntimeError('OCR requires predownloaded detection_model_dir and recognition_model_dir (or model_dir/det and model_dir/rec)')
    device = cfg.get('device') or ('gpu:0' if cfg.get('use_gpu', False) else 'cpu')
    import yaml
    def local_name(directory):
        try:
            metadata=yaml.safe_load((Path(directory)/'inference.yml').read_text(encoding='utf-8-sig'))
            name=metadata['Global']['model_name']
            if not isinstance(name,str) or not name:raise ValueError('Missing model name')
            return name
        except (OSError,ValueError,TypeError,KeyError,yaml.YAMLError) as exc:
            raise RuntimeError('OCR local model identity requires a valid inference.yml') from exc
    det_name,rec_name=local_name(detection),local_name(recognition)
    # All requested model components have explicit local paths; auxiliary models off.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    os.environ['PADDLE_PDX_DISABLE_DEVICE_FALLBACK'] = 'True'
    from paddleocr import PaddleOCR
    _ocr = PaddleOCR(device=device,cpu_threads=cfg.get('cpu_threads',2),
                    text_detection_model_name=det_name,text_recognition_model_name=rec_name,
                    text_detection_model_dir=detection, text_recognition_model_dir=recognition,
                    use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False)
    _engine_info={'device':device,'configured_language':cfg.get('lang','en'),
        'language_source':'local recognition checkpoint','detection_model':det_name,
        'recognition_model':rec_name,'cpu_threads':cfg.get('cpu_threads',2)}
    return dict(_engine_info)


def ocr_single_image(image_path):
    if _ocr is None: raise RuntimeError('OCR engine is not initialized')
    if not Path(image_path).is_file(): raise ValueError('OCR image is unavailable')
    return _predict_text(image_path)


def _predict_text(image):
    texts = []
    with _model_lock:
        for result in _ocr.predict(input=image):
            if hasattr(result, 'get'):
                values = result.get('rec_texts', [])
            else:
                values = result['rec_texts']
            if not isinstance(values, list) or any(not isinstance(text, str) for text in values):
                raise ValueError('Invalid PaddleOCR v3 result schema')
            texts.extend(text.strip() for text in values if text.strip())
    return '\n'.join(texts) or None


def validate_image_data(values,cfg):
    if not isinstance(values,list) or not 1<=len(values)<=cfg['ocr']['max_batch_images']:
        raise ValueError('image_data must be a bounded nonempty list')
    decoded=[]
    for value in values:
        if not isinstance(value,str):raise ValueError('Image data must be base64 text')
        try:data=base64.b64decode(value,validate=True)
        except (ValueError,binascii.Error) as exc:raise ValueError('Invalid image encoding') from exc
        if not data or len(data)>cfg['limits']['max_image_bytes']:raise ValueError('Image exceeds byte limit')
        decoded.append(data)
    return decoded


def ocr_image_bytes(data):
    from PIL import Image
    import numpy as np
    with Image.open(io.BytesIO(data)) as image:
        if image.width*image.height>get_config()['limits']['max_render_pixels']:
            raise ValueError('OCR image exceeds pixel limit')
        pixels=np.asarray(image.convert('RGB'))[:,:,::-1].copy()  # Paddle image arrays use BGR.
    return _predict_text(pixels)


def validate_image_paths(values):
    cfg = get_config()
    if not isinstance(values, list) or len(values) > cfg['ocr'].get('max_batch_images', 20):
        raise ValueError('images must be a bounded list')
    roots = list(cfg['scanner'].get('scan_paths', [])) + list(cfg['ocr'].get('allowed_roots', []))
    roots.append(cfg['ocr'].get('temp_dir') or str(Path(cfg['database']['path']).parent / 'ocr-temp'))
    roots = [Path(root).resolve() for root in roots]
    validated = []
    for value in values:
        if not isinstance(value, str): raise ValueError('Each image must be a path string')
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise PermissionError('OCR path is outside allowed roots')
        if not path.is_file(): raise ValueError('OCR image is unavailable')
        if path.stat().st_size > cfg['limits']['max_image_bytes']:
            raise ValueError('OCR image exceeds byte limit')
        validated.append(str(path))
    return validated


class OCRHandler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path != '/health':
            self._json({'error': 'Not found'}, 404); return
        ready = _ocr is not None
        self._json({'status': 'ready' if ready else 'unavailable', 'ready': ready,'runtime':_engine_info}, 200 if ready else 503)

    def do_POST(self):
        if self.path != '/ocr':
            self._json({'error': 'Not found'}, 404); return
        from security import get_api_token
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied, 'Bearer ' + get_api_token()):
            self._json({'error': 'Unauthorized'}, 401); return
        if not _request_slots.acquire(blocking=False):
            self._json({'error': 'OCR busy'}, 429); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= get_config()['ocr']['max_request_bytes']: raise ValueError('Invalid or excessive request length')
            if self.headers.get_content_type() != 'application/json': raise ValueError('Expected application/json')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError('Expected an object')
            if 'image_data' in data:
                if 'images' in data:raise ValueError('Ambiguous OCR input')
                images=validate_image_data(data['image_data'],get_config())
                results=[ocr_image_bytes(image) for image in images]
            else:
                paths = validate_image_paths(data.get('images'))
                results=[ocr_single_image(path) for path in paths]
            self._json({'results':results})
        except PermissionError:
            self._json({'error': 'Image path forbidden'}, 403)
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json({'error': 'Invalid OCR request'}, 400)
        except Exception:
            log.exception('OCR request failed')
            self._json({'error': 'OCR inference failed'}, 503)
        finally:
            _request_slots.release()

    def log_message(self, *_):
        return


def main():
    global _request_slots
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config'); parser.add_argument('--port', type=int); parser.add_argument('--workers', type=int)
    args = parser.parse_args()
    if args.config:
        from config_loader import reset_config_cache, load_config
        reset_config_cache(); load_config(args.config)
    cfg = get_config()['ocr']
    workers = args.workers or cfg.get('workers', 1)
    if workers < 1: parser.error('workers must be positive')
    _request_slots = threading.BoundedSemaphore(workers)
    # Binding happens only after model construction succeeds, never false healthy.
    init_worker()
    server = ThreadingHTTPServer(('127.0.0.1', args.port or cfg.get('port', 8002)), OCRHandler)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
