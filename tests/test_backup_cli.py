"""Real SQLite/CLI backup safety; no model or retrieval-quality substitutes."""
from pathlib import Path
import hashlib,sqlite3,subprocess,sys
import yaml
from database import initialize,connect


def test_cli_backup_preserves_relations_and_refuses_overwrite(tmp_path):
    source=tmp_path/'source.db';target=tmp_path/'backup.db';profile=tmp_path/'config.yaml'
    profile.write_text(yaml.safe_dump({'database':{'path':str(source)}}),encoding='utf8');initialize(source)
    with connect(source) as c:
        c.execute("INSERT INTO files(path,name,file_hash) VALUES (?,?,?)",(str(tmp_path/'note.txt'),'note.txt','backup-state-fixture'))
        fid=c.execute('SELECT id FROM files').fetchone()[0]
        c.execute("INSERT INTO rag_status(file_id,status,source_hash) VALUES (?,'done',?)",(fid,'backup-state-fixture'))
    command=[sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'run_pipeline.py'),'--config',str(profile),'backup',str(target)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    with sqlite3.connect(target) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT name,status,source_hash FROM files f JOIN rag_status r ON r.file_id=f.id').fetchone()==('note.txt','done','backup-state-fixture')
    before=hashlib.sha256(target.read_bytes()).hexdigest()
    refused=subprocess.run(command,capture_output=True,text=True,timeout=15)
    assert refused.returncode!=0 and 'already exists' in refused.stderr
    assert hashlib.sha256(target.read_bytes()).hexdigest()==before
