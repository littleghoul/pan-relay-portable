"""Persistent per-task cache; only verified jobs may release their files."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


class TransferCache:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv('PAN_TRANSFER_CACHE_DIR', '/transfer-cache')).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, task_id):
        # Canonical UUIDs keep user titles and remote paths out of filesystem paths.
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError('Invalid transfer task ID')
        p = self.root / task_id
        if p.is_symlink() or p.resolve().parent != self.root:
            raise ValueError('Unsafe transfer cache directory')
        return p

    def create(self, expected_bytes, source, target):
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError('Invalid expected size')
        # Payload, upload staging/renamed file and target verification may coexist.
        if shutil.disk_usage(self.root).free < expected_bytes * 3 + 1024**3:
            raise OSError('Insufficient transfer cache space')
        task_id = str(uuid.uuid4())
        p = self.directory(task_id)
        p.mkdir(mode=0o700)
        self.write(task_id, dict(task_id=task_id, expected_bytes=expected_bytes,
                                source=source, target=target, phase='queued', verified=False))
        return task_id

    def ensure_space(self, task_id):
        state=self.read(task_id)
        existing=0
        for child in self.directory(task_id).iterdir():
            if child.is_symlink() or not child.is_file():
                raise ValueError('Unexpected cache entry')
            if child.name!='state.json':existing+=child.stat().st_size
        remaining=max(0,state['expected_bytes']*3-existing)
        if shutil.disk_usage(self.root).free < remaining+1024**3:
            raise OSError('Insufficient transfer cache space')

    def write(self, task_id, state):
        p = self.directory(task_id)
        if state.get('task_id') != task_id:
            raise ValueError('Transfer task ID mismatch')
        fd, temp = tempfile.mkstemp(prefix='.state-', dir=p)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, p / 'state.json')
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def read(self, task_id):
        p = self.directory(task_id) / 'state.json'
        if p.is_symlink():
            raise ValueError('Unsafe state file')
        state = json.loads(p.read_text(encoding='utf-8'))
        if state.get('task_id') != task_id:
            raise ValueError('Transfer task ID mismatch')
        return state

    def release_verified(self, task_id):
        state = self.read(task_id)
        if state.get('phase') != 'complete' or state.get('verified') is not True:
            raise ValueError('Unverified transfer cache must be retained')
        p = self.directory(task_id)
        # Retain the journal; remove only this task's regular cache files.
        for child in p.iterdir():
            if child.name == 'state.json':
                continue
            if child.is_symlink() or not child.is_file():
                raise ValueError('Unexpected cache entry')
        for child in p.iterdir():
            if child.name != 'state.json':
                child.unlink()
        state['cache_released'] = True
        self.write(task_id, state)
