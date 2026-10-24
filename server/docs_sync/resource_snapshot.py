"""Export from the DB owner's writable directory; consumers only read atomic JSON."""
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

def export_resources(source,output):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=rw',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute("SELECT id,name,share_link,source_link FROM resources WHERE is_replaced=1 AND file_id IS NOT NULL AND file_id!='' AND account_id IS NOT NULL AND account_id!='' AND cloud_name IN ('百度网盘','夸克网盘') ORDER BY id")]
    output=Path(output); temporary=output.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump({'created_at':time.time(),'resources':rows},f,ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    temporary.replace(output)

def read_resources(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not 0<=time.time()-data['created_at']<180 or not isinstance(data['resources'],list):
        raise ValueError('resource_snapshot_stale')
    return data['resources']
