"""Generation semantics, independent of embedding and transport settings."""
import hashlib
import json
from config_loader import get_config
from format_capabilities import OFFICE_INPUT_FILTERS

VISION_PROMPT = 'Describe this document page faithfully for retrieval. Preserve visible text, numbers, chart meanings and uncertainty. Page {page_num}.'
RENDER_POLICY = {'version':2,'pdf_dpi':150,'image_mode':'RGB','page_labels':True,'djvu_size':'native_pixels','office_input_filters':OFFICE_INPUT_FILTERS}


def generation_fingerprint(config=None):
    cfg=config if config is not None else get_config()
    endpoints=[]
    for endpoint in cfg['vision']['endpoints']:
        if not endpoint['enabled']:continue
        identity={key:endpoint.get(key,'') for key in ('model','revision','max_tokens')}
        # Without a pinned revision, relocation cannot establish equivalence.
        if not endpoint.get('revision'):identity['deployment']=endpoint['api_base'].rstrip('/')
        endpoints.append(identity)
    contract={'version':1,'endpoints':endpoints,'prompt':VISION_PROMPT,'render':RENDER_POLICY,
        'limits':{key:cfg['limits'][key] for key in ('max_image_bytes','max_describe_pages','max_render_pixels','max_djvu_pages')}}
    return hashlib.sha256(json.dumps(contract,sort_keys=True).encode('utf8')).hexdigest()


CURRENT_DESCRIPTION = 'd.generation_fingerprint=description_generation() AND d.source_hash IS f.file_hash'
