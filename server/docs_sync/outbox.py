"""Durable, single-writer delivery queue; no credentials or remote side effects."""
import hashlib
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit


class Outbox:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, share_key TEXT NOT NULL,
                name TEXT NOT NULL, share_link TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target,share_key))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, target, name, share_link):
        target, name, share_link = target.strip(), name.strip(), share_link.strip()
        url = urlsplit(share_link.split()[0] if share_link else '')
        if (not target or not name or len(name) > 4096 or len(share_link) > 4096
                or url.scheme != 'https' or url.hostname not in {'pan.baidu.com', 'pan.quark.cn'}
                or not url.path.startswith('/s/') or url.username or url.password):
            raise ValueError('invalid_share')
        # The same share with different extraction-code formatting remains one delivery.
        key = hashlib.sha256((url.hostname + url.path.rstrip('/')).encode()).hexdigest()
        with self.connect() as db:
            result = db.execute('INSERT OR IGNORE INTO deliveries(target,share_key,name,share_link) VALUES(?,?,?,?)',
                                (target, key, name, share_link))
            return result.rowcount

    def collect(self, source, target):
        count = 0
        with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute('''SELECT name,share_link FROM resources
                WHERE is_replaced=1 AND file_id IS NOT NULL AND file_id != ''
                AND account_id IS NOT NULL AND account_id != ''
                AND cloud_name IN ('百度网盘','夸克网盘') ORDER BY id''')
            for name, link in rows:
                count += self.enqueue(target, name, link)
        return count

    def claim(self, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM deliveries WHERE target=? AND status='pending' ORDER BY id LIMIT 1",
                             (target,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE deliveries SET status='sending',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
            return dict(row)

    def finish(self, row_id, status, error_code=''):
        if status not in {'sent', 'failed', 'uncertain'} or error_code not in {
            '', 'response_lost', 'authorization_rejected', 'remote_rejected', 'interrupted', 'invalid_response'
        }:
            raise ValueError('invalid_status')
        with self.connect() as db:
            changed = db.execute("UPDATE deliveries SET status=?,error_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='sending'",
                                 (status, error_code, row_id)).rowcount
            if not changed:
                raise ValueError('invalid_transition')

    def recover_interrupted(self):
        # Call only after obtaining the process-wide exclusive worker lock.
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status='uncertain',error_code='interrupted' WHERE status='sending'")

    def retry_failed(self, row_id):
        with self.connect() as db:
            changed = db.execute("UPDATE deliveries SET status='pending',error_code='' WHERE id=? AND status='failed'", (row_id,)).rowcount
            if not changed:
                raise ValueError('only_rejected_writes_can_retry')

    def defer_rate_limit(self, row_id):
        # append checks the existing link before writing, including partial writes.
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status='pending',error_code='rate_limited',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='sending'",(row_id,))

    def counts(self):
        with self.connect() as db:
            return dict(db.execute('SELECT status,count(*) FROM deliveries GROUP BY status'))

    def uncertain(self, target):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM deliveries WHERE target=? AND status='uncertain' ORDER BY id LIMIT 20",(target,))]

    def confirm_found(self, row_id):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status='sent',error_code='',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='uncertain'",(row_id,))
