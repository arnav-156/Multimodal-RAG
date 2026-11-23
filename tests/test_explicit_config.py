"""Explicit selectors and test defaults must not target an ambient service DB."""
from pathlib import Path
import os,json,sqlite3,hashlib,subprocess,sys
import yaml
import config_loader as cl


def profiles(tmp_path):
    values=[]
    for name in ['explicit','ambient']:
        db=tmp_path/(name+'.db');profile=tmp_path/(name+'.yaml')
        profile.write_text(yaml.safe_dump({'database':{'path':str(db)},'scanner':{'scan_paths':[]}}),encoding='utf8');values.append((profile,db))
    return values


def test_explicit_selection_survives_reads_and_reload_until_reset(tmp_path,monkeypatch):
    (chosen,chosen_db),(ambient,ambient_db)=profiles(tmp_path)
    monkeypatch.setenv('RAG_CONFIG',str(ambient));cl.reset_config_cache()
    assert cl.get_config()['database']['path']==str(ambient_db)
    cl.load_config(chosen)
    assert cl.get_config()['database']['path']==str(chosen_db)
    assert cl.reload_config()['database']['path']==str(chosen_db)
    cl.reset_config_cache()
    assert cl.get_config()['database']['path']==str(ambient_db)


def test_cli_explicit_profile_cannot_initialize_or_write_ambient_db(tmp_path):
    (chosen,chosen_db),(ambient,ambient_db)=profiles(tmp_path)
    with sqlite3.connect(ambient_db) as c:c.execute('CREATE TABLE sentinel(value TEXT)');c.execute("INSERT INTO sentinel VALUES ('ambient must stay unchanged')")
    digest=hashlib.sha256(ambient_db.read_bytes()).hexdigest();env=dict(os.environ,RAG_CONFIG=str(ambient),PYTHONUTF8='1')
    command=[sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'run_pipeline.py'),'--config',str(chosen)]
    initialized=subprocess.run(command+['init-db'],env=env,capture_output=True,text=True,timeout=15)
    assert initialized.returncode==0,initialized.stderr
    assert chosen_db.exists()
    processed=subprocess.run(command+['process'],env=env,capture_output=True,text=True,timeout=15)
    assert processed.returncode==0,processed.stderr
    assert json.loads(processed.stdout)['processed']==0
    assert hashlib.sha256(ambient_db.read_bytes()).hexdigest()==digest
    with sqlite3.connect(chosen_db) as c:assert c.execute("SELECT status FROM jobs WHERE kind='process'").fetchone()[0]=='completed'


def test_suite_default_database_and_roots_are_owned_by_this_test(tmp_path):
    cfg=cl.get_config()
    assert Path(cfg['database']['path']).parent==tmp_path
    assert cfg['scanner']['scan_paths']==[] and cfg['scanner']['home_paths']==[]
