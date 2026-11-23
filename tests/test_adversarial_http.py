"""Adversarial HTTP regressions; use real validation/auth, isolated DB, no models."""
import json
import pytest
from fastapi.testclient import TestClient
import server

TOKEN='adversarial-regression-test-token-not-production'
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('RAG_API_TOKEN',TOKEN)
    with TestClient(server.app,base_url='http://127.0.0.1:18170',raise_server_exceptions=False) as client:
        yield client

@pytest.mark.parametrize('token',['अमान्य🔑','café'])
def test_nonascii_login_is_unauthorized_not_a_server_error(client,token):
    response=client.post('/api/session',content=json.dumps({'token':token}),headers={'Content-Type':'application/json'})
    assert response.status_code==401
    assert response.json()=={'detail':'Invalid token'}
    assert client.post('/api/session',json={'token':TOKEN}).status_code==200


def test_nonascii_bearer_header_does_not_break_session_or_authentication(client):
    response=client.get('/api/stats',headers={'Authorization':b'Bearer caf\xe9'})
    assert response.status_code==401
    assert client.get('/api/session',headers={'Authorization':b'Bearer caf\xe9'}).json()=={'authenticated':False}
    assert client.get('/api/stats',headers={'Authorization':'Bearer '+TOKEN}).status_code==200
@pytest.mark.parametrize('value',[None,[], 'invalid',42,True])
def test_database_section_wrong_type_is_rejected_without_settings_changes(client,value):
    from config_loader import get_config
    before=get_config()
    response=client.put('/api/config',json={'config':{'database':value}},headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code==422
    assert response.json()=={'detail':'database must be a configuration object'}
    assert get_config()==before
    assert client.get('/api/config',headers={'Authorization':'Bearer '+TOKEN}).json()['config']['database']==before['database']
def test_deep_invalid_json_returns_structured_validation_without_echo(client):
    response=client.post('/api/search',content='['*1100+'0'+']'*1100,headers={'Content-Type':'application/json','Authorization':'Bearer '+TOKEN})
    assert response.status_code==422
    assert len(response.content)<2000
    errors=response.json()['detail']
    assert errors and all(set(e)=={'type','loc','msg'} for e in errors)
    assert client.get('/api/live').status_code==200


def test_validation_errors_do_not_echo_large_credentials_or_invalid_unicode(client):
    sentinel='UNTRUSTED-CREDENTIAL-SENTINEL-'*1000
    response=client.post('/api/session',json={'token':sentinel})
    assert response.status_code==422 and len(response.content)<2000
    assert 'UNTRUSTED-CREDENTIAL-SENTINEL' not in response.text
    response=client.post('/api/session',content='{"token":"\\ud800"}',headers={'Content-Type':'application/json'})
    assert response.status_code==422
    assert response.json()['detail'][0]['type']=='string_unicode'
def test_stale_editor_cannot_overwrite_a_newer_conditional_save(client):
    import copy
    auth={'Authorization':'Bearer '+TOKEN}
    loaded=client.get('/api/config',headers=auth).json();snapshot=loaded['config']
    version=loaded.get('revision','missing-before-fix')
    first=copy.deepcopy(snapshot);first['search']['default_top_k']=6
    stale=copy.deepcopy(snapshot);stale['search']['max_top_k']=199
    headers={**auth,'If-Match':'"'+version+'"'}
    assert client.put('/api/config',json={'config':first},headers=headers).status_code==200
    response=client.put('/api/config',json={'config':stale},headers=headers)
    assert response.status_code==412
    current=client.get('/api/config',headers=auth).json()
    assert current['config']['search']['default_top_k']==6
    assert current['config']['search']['max_top_k']==snapshot['search']['max_top_k']
    assert current['revision']!=version
    # A freshly reviewed revision can still save; no forced merge or silent retry.
    assert client.put('/api/config',json={'config':{'search':{'max_top_k':199}}},headers={**auth,'If-Match':'"'+current['revision']+'"'}).status_code==200
@pytest.mark.parametrize('route',['/api/stats','/api/config/status'])
def test_unavailable_database_is_503_and_recovers_after_repair(client,route):
    from database import connect
    with connect() as db:db.execute('ALTER TABLE rag_chunks RENAME TO qa_unavailable_chunks')
    try:
        response=client.get(route,headers={'Authorization':'Bearer '+TOKEN})
        assert response.status_code==503
        assert response.json()=={'detail':'Local database unavailable. Check database access and integrity.'}
        assert 'qa_unavailable_chunks' not in response.text
    finally:
        with connect() as db:db.execute('ALTER TABLE qa_unavailable_chunks RENAME TO rag_chunks')
    assert client.get('/api/stats',headers={'Authorization':'Bearer '+TOKEN}).status_code==200
def test_expired_signed_cookie_is_refused_while_a_current_cookie_works(client):
    from itsdangerous import URLSafeTimedSerializer,TimestampSigner
    class ExpiredSigner(TimestampSigner):
        def get_timestamp(self):return 1
    expired=URLSafeTimedSerializer(TOKEN,salt='rag-session',signer=ExpiredSigner).dumps('authorized')
    current=URLSafeTimedSerializer(TOKEN,salt='rag-session').dumps('authorized')
    assert client.get('/api/stats',headers={'Cookie':'rag_session='+expired}).status_code==401
    assert client.get('/api/stats',headers={'Cookie':'rag_session='+current}).status_code==200
