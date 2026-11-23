"""Preserve legacy configuration ownership while explaining inactive knobs."""
from fastapi.testclient import TestClient
import yaml

import config_loader as cl
from run_pipeline import init_db
import server


def test_settings_explains_compatibility_fields_and_preserves_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_API_TOKEN', 'compatibility-test-token-' * 2)
    profile = tmp_path / 'legacy.yaml'
    profile.write_text(yaml.safe_dump({
        'scan': {'scan_paths': ['manuals'], 'home_paths': ['old-home'], 'everything_exe': 'retained-es'},
        'chunk': {'max_chars': 900},
        'pipeline': {'extractor_threads': 7, 'fetch_limit': 11},
        'embedding': {'dim': 256, 'vllm_url': 'http://127.0.0.1:8001', 'api_key': 'compatibility-sentinel'},
        'monitor': {'directories': ['retained-watch-folder']},
    }), encoding='utf8')
    cl.load_config(profile)
    init_db()
    with TestClient(server.app, base_url='http://localhost') as client:
        client.headers['Authorization'] = 'Bearer ' + 'compatibility-test-token-' * 2
        page = client.get('/config')
        assert page.status_code == 200
        assert 'Compatibility fields' in page.text
        assert 'do not control scheduling' in page.text
        assert 'pipeline.fetch_limit' in page.text
        cfg = client.get('/api/config').json()['config']
        assert cfg['embedding']['api_key'] == cl.REDACTED
        assert cfg['embedding']['vllm']['api_url'] == 'http://127.0.0.1:8001'
        assert cfg['scanner']['scan_paths'] == [str(tmp_path / 'manuals')]
        assert cfg['chunking']['max_chars'] == 900
        cfg['search']['default_top_k'] = 7
        saved = client.put('/api/config', json={'config': cfg})
        assert saved.status_code == 200, saved.text
        reloaded = client.post('/api/config/reload')
        assert reloaded.status_code == 200, reloaded.text
        result = reloaded.json()['config']
        assert result['pipeline']['extractor_threads'] == 7
        assert result['pipeline']['fetch_limit'] == 11
        assert result['monitor']['directories'] == ['retained-watch-folder']
        assert result['scanner']['everything_exe'] == 'retained-es'
        assert result['scanner']['home_paths'] == [str(tmp_path / 'old-home')]
        assert result['search']['default_top_k'] == 7
        assert 'compatibility-sentinel' not in saved.text + reloaded.text
    assert yaml.safe_load(profile.read_text(encoding='utf8'))['embedding']['api_key'] == 'compatibility-sentinel'
