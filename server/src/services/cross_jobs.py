"""Durable queue shared by the web process and a single transfer worker."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager


@contextmanager
def connect():
    path = os.getenv('CROSS_JOBS_DB', '/app/data/cross_jobs.sqlite')
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, resource_id INTEGER NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL, progress TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
        updated REAL NOT NULL)''')
    try:
        schema=conn.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
        if 'UNIQUE' in schema.upper():
            conn.execute('BEGIN IMMEDIATE')
            # Recheck after acquiring the lock: another process may have migrated it.
            schema=conn.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
            if 'UNIQUE' in schema.upper():
                conn.execute('ALTER TABLE jobs RENAME TO jobs_legacy')
                conn.execute('''CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, resource_id INTEGER NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, progress TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL)''')
                conn.execute('INSERT INTO jobs SELECT * FROM jobs_legacy')
                conn.execute('DROP TABLE jobs_legacy')
            conn.commit()
        conn.execute('CREATE INDEX IF NOT EXISTS jobs_resource ON jobs(resource_id)')
        with conn: yield conn
    finally: conn.close()


def enqueue(resources):
    ids = []
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for resource in resources:
            existing = c.execute('SELECT * FROM jobs WHERE resource_id=? ORDER BY rowid DESC LIMIT 1', (resource['id'],)).fetchone()
            if existing:
                old=json.loads(existing['payload'])
                keys=('cloud_name','file_id','account_id','share_link')
                if all(old.get(k)==resource.get(k) for k in keys):
                    ids.append(existing['id']); continue
                if existing['status'] in ('queued','running'):
                    raise ValueError('该资源旧任务尚未结束，不能切换源文件；请等待任务结束后重新提交')
            job_id = str(uuid.uuid4())
            c.execute('INSERT INTO jobs(id,resource_id,payload,status,progress,updated) VALUES(?,?,?,?,?,?)',
                      (job_id, resource['id'], json.dumps(resource, ensure_ascii=False), 'queued', '等待传输', time.time()))
            ids.append(job_id)
    return ids


def list_jobs(limit=500):
    with connect() as c:
        rows = c.execute('SELECT * FROM jobs ORDER BY updated DESC' + (' LIMIT ?' if limit is not None else ''), (limit,) if limit is not None else ()).fetchall()
    return [dict(id=r['id'], resource_id=r['resource_id'], name=json.loads(r['payload'])['name'],
                 source_cloud=json.loads(r['payload'])['cloud_name'], status=r['status'],
                 progress=r['progress'], result=json.loads(r['result']), error=r['error']) for r in rows]


def update(job_id, **fields):
    allowed = {'status', 'progress', 'result', 'error'}
    if not fields or set(fields) - allowed: raise ValueError('Invalid job update')
    if 'result' in fields: fields['result'] = json.dumps(fields['result'], ensure_ascii=False)
    fields['updated'] = time.time()
    with connect() as c:
        c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', [*fields.values(), job_id])


def retry(job_id):
    with connect() as c:
        return c.execute("UPDATE jobs SET status='queued',error='',progress='等待重试',updated=? WHERE id=? AND status='failed'", (time.time(), job_id)).rowcount == 1


def delete_failed(job_id):
    # Conditional delete is atomic with claim/retry; keep cloud files and cache.
    with connect() as c:
        return c.execute("DELETE FROM jobs WHERE id=? AND status='failed'", (job_id,)).rowcount == 1


def resume_accounts(account_ids):
    if os.getenv('CROSS_TRANSFER_ENABLED') != '1':
        return 0
    allowed = {'百度登录失效，请重新扫码', '源账号或目标账号不可用，请重新登录', '目标账号发生变化，任务保留；请恢复原目标账号'}
    count = 0
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute("SELECT * FROM jobs WHERE status='failed'").fetchall():
            payload = json.loads(row['payload']); result = json.loads(row['result'])
            if row['error'] in allowed and (payload.get('account_id') in account_ids or result.get('target_account_id') in account_ids):
                count += c.execute("UPDATE jobs SET status='queued',error='',progress='账号已恢复，等待继续传输',updated=? WHERE id=? AND status='failed'", (time.time(), row['id'])).rowcount
    return count


def claim():
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY updated LIMIT 1").fetchone()
        if not row: return None
        c.execute("UPDATE jobs SET status='running',progress='准备传输',updated=? WHERE id=?", (time.time(), row['id']))
        return dict(id=row['id'], payload=json.loads(row['payload']), result=json.loads(row['result']))


def recover():
    with connect() as c:
        c.execute("UPDATE jobs SET status='queued',progress='服务重启后恢复任务' WHERE status='running'")
