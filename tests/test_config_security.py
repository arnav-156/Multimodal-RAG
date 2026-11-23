"""Real config/file/security checks; no model service or credentials required."""
import os
import pytest
import yaml

import config_loader as cl


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    keys=("RAG_CONFIG", "DB_PATH", "LOG_DIR", "JINA_MODEL_PATH", "VLLM_API_URL", "VLLM_MODEL_NAME", "OCR_API_URL", "RAG_PORT", "RAG_HOST", "RAG_API_TOKEN")
    original={key:os.environ.get(key) for key in keys}
    for key in keys:monkeypatch.delenv(key, raising=False)
    cl.reset_config_cache()
    yield
    # Undo process overrides before restoring the fixture boundary. A reload
    # can relinquish dotenv ownership; pytest must not resurrect that old value
    # after reset and silently redirect unrelated tests to a relative two.db.
    monkeypatch.undo()
    cl.reset_config_cache()
    for key,value in original.items():
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value


def config_file(tmp_path, data=None):
    p = tmp_path / "配置.yaml"
    p.write_text(yaml.safe_dump(data or {}, allow_unicode=True), encoding="utf-8")
    return p


def test_custom_string_path_and_unicode(tmp_path):
    cfg = cl.load_config(str(config_file(tmp_path, {"scanner": {"scan_paths": ["测试"]}})))
    assert cfg["scanner"]["scan_paths"] == [str(tmp_path / "测试")]


def test_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "override.db"))
    cl.load_config(config_file(tmp_path, {"database": {"path": "original.db"}}))
    assert cl.get_db_path() == str(tmp_path / "override.db")


def test_safe_defaults(tmp_path):
    cfg = cl.load_config(config_file(tmp_path))
    assert cfg["server"]["host"] == "127.0.0.1"
    assert "*" not in cfg["server"]["cors_origins"]
    assert cfg["scanner"]["scan_paths"] == cfg["scanner"]["home_paths"] == []
    assert cfg["security"]["local_only"] is True
    assert cfg["security"]["allow_cloud"] is False
    assert cfg["vision"]["endpoints"] == []


def test_validated_roundtrip_preserves_endpoint_policy_and_secret(tmp_path):
    p = config_file(tmp_path, {"vision": {"endpoints": [{"api_base": "http://127.0.0.1:1234/v1", "name": "local", "api_key": "SENTINEL_SECRET", "max_parallel": 1, "rate_limit": 2}]}})
    cfg = cl.load_config(p)
    public = cl.public_config(cfg)
    assert "SENTINEL_SECRET" not in repr(public)
    assert public["vision"]["endpoints"][0]["api_key"] == "***REDACTED***"
    public["search"]["default_top_k"] = 7
    after = cl.save_config(public)
    assert after["search"]["default_top_k"] == 7
    assert after["vision"]["endpoints"][0]["api_key"] == "SENTINEL_SECRET"
    assert after["vision"]["endpoints"][0]["name"] == "local"
    assert after["vision"]["endpoints"][0]["rate_limit"] == 2
    assert cl.reload_config()["search"]["default_top_k"] == 7
    assert isinstance(yaml.safe_load(p.read_text(encoding="utf-8"))["server"]["cors_origins"], list)


def test_invalid_save_is_atomic_and_errors_do_not_echo_secrets(tmp_path):
    p = config_file(tmp_path)
    cl.load_config(p)
    original = p.read_bytes()
    with pytest.raises(ValueError) as error:
        cl.save_config({"server": {"port": -1}, "vision": {"endpoints": [{"api_key": "DO_NOT_PRINT"}]}})
    assert "DO_NOT_PRINT" not in str(error.value)
    assert p.read_bytes() == original


@pytest.mark.parametrize("update", [{"server": {"cors_origins": "*"}}, {"search": {"default_top_k": -1}}, {"chunking": {"overlap_ratio": 2}}, {"unexpected": 1}])
def test_reject_invalid_settings(tmp_path, update):
    cl.load_config(config_file(tmp_path))
    with pytest.raises(ValueError):
        cl.save_config(update)


def test_custom_path_selection_and_helpers_are_live(tmp_path):
    first = tmp_path / "one.yaml"
    second = tmp_path / "two.yaml"
    first.write_text("embedding:\n  dimension: 256\n", encoding="utf-8")
    second.write_text("embedding:\n  dimension: 512\n", encoding="utf-8")
    cl.load_config(first)
    assert cl.get_embedding_dim() == 256
    cl.load_config(str(second))
    assert cl.get_embedding_dim() == 512
    cl.save_config({"embedding": {"dimension": 128}})
    assert cl.get_embedding_dim() == 128


def test_legacy_aliases_migrate_to_authoritative_keys(tmp_path):
    cfg = cl.load_config(config_file(tmp_path, {"scan": {"scan_paths": ["docs"]}, "chunk": {"max_chars": 900}, "metadata": {"workers": 2, "timeout": 3}, "embedding": {"dim": 256, "vllm_url": "http://127.0.0.1:8001"}}))
    assert cfg["scanner"]["scan_paths"] == [str(tmp_path / "docs")]
    assert cfg["chunking"]["max_chars"] == 900
    assert cfg["metadata"]["workers_per_disk"] == 2
    assert cfg["embedding"]["dimension"] == 256
    assert "scan" not in cfg and "chunk" not in cfg


def test_cloud_opt_in_and_loopback_guards(tmp_path):
    import security
    cfg = cl.load_config(config_file(tmp_path))
    security.validate_endpoint("http://127.0.0.1:1234/v1", cfg)
    for url in ["https://example.com/v1", "http://192.168.1.2:1234/v1", "file:///etc/passwd", "http://user:password@localhost:1234"]:
        with pytest.raises(ValueError):
            security.validate_endpoint(url, cfg)
    cfg["security"]["local_only"] = False
    cfg["security"]["allow_cloud"] = True
    with pytest.raises(ValueError):
        security.validate_endpoint("https://example.com/v1", cfg)
    security.validate_endpoint("https://example.com/v1", cfg, cloud_authorized=True)


def test_host_and_origin_boundary(tmp_path):
    import security
    cfg = cl.load_config(config_file(tmp_path))
    security.validate_host_origin("127.0.0.1:8100", "http://127.0.0.1:8100", cfg=cfg)
    security.validate_host_origin("localhost:8100", None, cfg=cfg)
    for host, origin in [("evil.test:8100", None), ("127.0.0.1:8100", "https://evil.test"), ("127.0.0.1:8100", "null"), ("localhost.evil.test", None)]:
        with pytest.raises(ValueError):
            security.validate_host_origin(host, origin, cfg=cfg)


def test_api_token_persistence_and_environment_override(tmp_path, monkeypatch):
    import security
    cl.load_config(config_file(tmp_path))
    a = security.get_api_token()
    assert len(a) >= 32 and security.get_api_token() == a
    assert a not in repr(cl.public_config())
    monkeypatch.setenv("RAG_API_TOKEN", "a" * 48)
    assert security.get_api_token() == "a" * 48


@pytest.mark.parametrize("update", [{"scan": []}, {"scanner": None}, {"embedding": True}, {"metadata": []}, {"embedding": {"vllm_url": "http://127.0.0.1", "vllm": []}}])
def test_malformed_nested_settings_have_safe_validation_error(tmp_path, update):
    p = config_file(tmp_path)
    cl.load_config(p)
    before = p.read_bytes()
    with pytest.raises(ValueError):
        cl.save_config(update)
    assert p.read_bytes() == before


def test_dotenv_reload_and_config_selection(tmp_path, monkeypatch):
    p = config_file(tmp_path)
    env = tmp_path / ".env"
    env.write_text("DB_PATH=one.db\n", encoding="utf-8")
    assert cl.load_config(p)["database"]["path"] == str(tmp_path / "one.db")
    env.write_text("DB_PATH=two.db\n", encoding="utf-8")
    assert cl.reload_config()["database"]["path"] == str(tmp_path / "two.db")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "process.db"))
    assert cl.reload_config()["database"]["path"] == str(tmp_path / "process.db")

def test_endpoint_deletion_preserves_identity_and_never_moves_secret(tmp_path):
    endpoints=[{'name':n,'api_base':f'http://127.0.0.1:{1200+i}/v1','api_key':'SECRET_'+n} for i,n in enumerate('abc')]
    cl.load_config(config_file(tmp_path,{'vision':{'endpoints':endpoints}}))
    public=cl.public_config();public['vision']['endpoints'].pop(0);public['vision']['endpoints'].pop(0)
    assert cl.save_config(public)['vision']['endpoints'][0]['api_key']=='SECRET_c'
    public=cl.public_config();public['vision']['endpoints'][0]['api_base']='http://127.0.0.1:9999/v1'
    with pytest.raises(ValueError,match='no existing value'):cl.save_config(public)

def test_rejected_reload_keeps_last_effective_database(tmp_path):
    p=config_file(tmp_path,{'database':{'path':'one.db'}})
    original=cl.load_config(p)['database']['path']
    p.write_text('database:\n  path: two.db\n',encoding='utf8')
    with pytest.raises(ValueError,match='restart'):cl.reload_config(expected_db_path=original)
    assert cl.get_db_path()==original
