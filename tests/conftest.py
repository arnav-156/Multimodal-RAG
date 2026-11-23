"""Every test owns its fallback config; ambient service settings are excluded."""
import pytest
import yaml
import config_loader as cl


@pytest.fixture(autouse=True)
def isolated_default_config(tmp_path,monkeypatch):
    cl.reset_config_cache()
    for name in ['RAG_CONFIG','DB_PATH','LOG_DIR','JINA_MODEL_PATH','OCR_API_URL','RAG_PORT','RAG_HOST','VLLM_API_URL','VLLM_MODEL_NAME','RAG_API_TOKEN']:
        monkeypatch.delenv(name,raising=False)
    profile=tmp_path/'suite-default.yaml'
    profile.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'suite-default.db')},'scanner':{'scan_paths':[],'home_paths':[]}}),encoding='utf8')
    monkeypatch.setattr(cl,'DEFAULT_CONFIG_PATH',profile)
    cl.load_config(profile)
    try:
        yield
    finally:
        cl.reset_config_cache()
