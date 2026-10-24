"""Serialize target changes with cloud writes. Requests and application are durable."""
import json
import sqlite3
from contextlib import contextmanager,closing
from pathlib import Path
try:
    from .target_config import normalize_target,target_key
    from .sheet_sink import link_key
except ImportError:
    from target_config import normalize_target,target_key
    from sheet_sink import link_key

@contextmanager
def sync_gate(path,blocking=True):
    import fcntl
    with open(path,'rb') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ValueError('后台正在处理任务，请稍后再保存迁移；当前设置和任务均未改变。') from None
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)

def save_and_migrate(jobs,state,target):
    target=normalize_target(target); new_key=target_key(target)
    with sync_gate(jobs.path.parent/'docs_sync_gate.lock',False):
        with closing(sqlite3.connect((Path(state)/'sync.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as source:
            deliveries=source.execute('SELECT target,share_link,status FROM deliveries').fetchall()
        with jobs.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=normalize_target(json.loads(db.execute("SELECT value FROM docs_settings WHERE key='target'").fetchone()[0])); old_key=target_key(old)
            if old_key==new_key: raise ValueError('目标文档、工作表和起始位置没有变化，无需迁移。')
            sent={link_key(link) for key,link,status in deliveries if key==old_key and status=='sent'}
            rows=[r for r in db.execute('SELECT * FROM docs_batch_jobs WHERE target=?',(old_key,)) if not r['share_link'] or link_key(r['share_link']) not in sent]
            for row in rows:
                if db.execute('SELECT 1 FROM docs_batch_jobs WHERE target=? AND source_key=?',(new_key,row['source_key'])).fetchone():
                    raise ValueError('新目标已有相同任务。为避免重复，未执行迁移，请先核对目标任务。')
            payload=json.dumps(target,ensure_ascii=False)
            for row in rows:
                db.execute('UPDATE docs_batch_jobs SET target=?,target_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(new_key,payload,row['id']))
            db.execute('CREATE TABLE IF NOT EXISTS docs_sync_migrations(id INTEGER PRIMARY KEY,old_target TEXT NOT NULL,target_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('INSERT INTO docs_sync_migrations(old_target,target_json) VALUES(?,?)',(old_key,payload))
            db.execute("UPDATE docs_settings SET value=? WHERE key='target'",(payload,))
            count=sum(1 for key,link,status in deliveries if key==old_key and status in ('pending','failed','uncertain','sending'))
    return {'jobs':len(rows),'deliveries':count}

def apply_migrations(queue,jobs_path):
    with closing(sqlite3.connect(Path(jobs_path).resolve().as_uri()+'?mode=ro',uri=True)) as jobs:
        if not jobs.execute("SELECT 1 FROM sqlite_master WHERE name='docs_sync_migrations'").fetchone(): return
        commands=jobs.execute('SELECT id,old_target,target_json FROM docs_sync_migrations ORDER BY id').fetchall()
    with queue.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS applied_migrations(id INTEGER PRIMARY KEY,audit TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS targets(key TEXT PRIMARY KEY,config TEXT NOT NULL)')
        for command,old,payload in commands:
            if db.execute('SELECT 1 FROM applied_migrations WHERE id=?',(command,)).fetchone(): continue
            target=normalize_target(json.loads(payload)); new=target_key(target)
            db.execute('INSERT OR IGNORE INTO targets VALUES(?,?)',(new,json.dumps(target,ensure_ascii=False)))
            rows=[dict(r) for r in db.execute("SELECT * FROM deliveries WHERE target=? AND status IN ('pending','failed','uncertain','sending')",(old,))]
            for row in rows:
                existing=db.execute('SELECT id,status FROM deliveries WHERE target=? AND share_key=?',(new,row['share_key'])).fetchone()
                if existing:
                    db.execute("UPDATE deliveries SET status='migrated',error_code='' WHERE id=?",(row['id'],))
                    if existing['status']=='failed': db.execute("UPDATE deliveries SET status='pending',error_code='' WHERE id=?",(existing['id'],))
                else:
                    db.execute("UPDATE deliveries SET target=?,status='pending',error_code='',updated_at=CURRENT_TIMESTAMP WHERE id=?",(new,row['id']))
            db.execute('INSERT INTO applied_migrations VALUES(?,?)',(command,json.dumps(rows,ensure_ascii=False)))
