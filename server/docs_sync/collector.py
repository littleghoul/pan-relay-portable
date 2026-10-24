"""Route completed jobs by their immutable destination, without replaying history."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from target_config import normalize_target,target_key
from sheet_sink import link_key
from resource_snapshot import read_resources

def register_target(queue,target):
    target=normalize_target(target); key=target_key(target)
    with queue.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS targets(key TEXT PRIMARY KEY, config TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO targets VALUES(?,?)',(key,json.dumps(target,ensure_ascii=False)))
    return key

def collect_destinations(queue,source,jobs,default):
    register_target(queue,default)
    with closing(sqlite3.connect(Path(jobs).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        current=normalize_target(json.loads(db.execute("SELECT value FROM docs_settings WHERE key='target'").fetchone()[0]))
        ui=[dict(r) for r in db.execute('SELECT source_url,status,name,share_link,target_json FROM docs_batch_jobs ORDER BY id')]
    ui_sources={link_key(r['source_url']) for r in ui}
    ui_sources.update(link_key(r['share_link']) for r in ui if r['share_link'])
    for row in ui:
        if json.loads(row['target_json']).get('mode') in ('local','unconfigured'): continue
        if row['status']=='awaiting' and row['share_link']:
            key=register_target(queue,json.loads(row['target_json']))
            queue.enqueue(key,row['name'],row['share_link'])
    rows=read_resources(source)
    with queue.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS source_seen(key TEXT PRIMARY KEY)')
        baseline=not db.execute("SELECT 1 FROM source_seen WHERE key='initialized'").fetchone()
        if baseline:
            db.executemany('INSERT OR IGNORE INTO source_seen VALUES(?)',[(str(r['id'])+':'+link_key(r['share_link']),) for r in rows])
            db.execute("INSERT INTO source_seen VALUES('initialized')")
        seen={r[0] for r in db.execute('SELECT key FROM source_seen')}
    current_key=register_target(queue,current)
    for row in rows:
        key=str(row['id'])+':'+link_key(row['share_link'])
        if key in seen: continue
        if current.get('mode')!='unconfigured' and link_key(row['source_link']) not in ui_sources and link_key(row['share_link']) not in ui_sources:
            queue.enqueue(current_key,row['name'],row['share_link'])
        with queue.connect() as db: db.execute('INSERT OR IGNORE INTO source_seen VALUES(?)',(key,))
    return current

def active_targets(queue):
    with queue.connect() as db:
        return [(r['key'],json.loads(r['config'])) for r in db.execute("SELECT * FROM targets WHERE key IN (SELECT target FROM deliveries WHERE status IN ('pending','uncertain')) ORDER BY rowid")]
