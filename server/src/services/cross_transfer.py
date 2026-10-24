"""Provider-independent copy runner with durable local download checkpoints."""
import hashlib


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class CrossTransfer:
    def __init__(self, cache):
        self.cache = cache

    def run(self, task_id, download, upload, verify_target):
        """Callbacks use paths, not credential-bearing URLs.

        upload must reconcile an existing destination before writing on retries.
        verify_target must compare complete target bytes/hash against source_hash.
        """
        state = self.cache.read(task_id)
        if state['phase'] == 'complete':
            if state.get('verified') is not True:
                raise ValueError('Invalid completed transfer')
            self.cache.release_verified(task_id)
            return state
        payload = self.cache.directory(task_id) / 'payload'
        try:
            self.cache.ensure_space(task_id)
            ready = (payload.is_file() and not payload.is_symlink()
                     and payload.stat().st_size == state['expected_bytes']
                     and state.get('source_hash') == file_hash(payload))
            if not ready:
                if payload.is_symlink():raise ValueError('Unsafe transfer cache file')
                # A full-size file without a valid checkpoint must be downloaded anew.
                if payload.exists() and payload.stat().st_size >= state['expected_bytes']:
                    with payload.open('wb'):pass
                state.update(phase='downloading', verified=False)
                self.cache.write(task_id, state)
                download(state['source'], payload)
                if payload.is_symlink() or payload.stat().st_size != state['expected_bytes']:
                    raise ValueError('Downloaded file size mismatch')
                state['source_hash'] = file_hash(payload)
                state['phase'] = 'downloaded'
                self.cache.write(task_id, state)
            state['phase'] = 'uploading'
            self.cache.write(task_id, state)
            # Reconcile on every retry; a crash can happen after remote commit.
            remote = upload(payload, state['target'], state['source_hash'])
            state.update(phase='verifying', remote=remote)
            self.cache.write(task_id, state)
            if verify_target(remote, state['expected_bytes'], state['source_hash']) is not True:
                raise ValueError('Target verification failed')
            state.update(phase='complete', verified=True)
            self.cache.write(task_id, state)
            self.cache.release_verified(task_id)
            return self.cache.read(task_id)
        except Exception as error:
            state.update(phase='failed', verified=False, error_type=type(error).__name__)
            # Never persist exception strings, which can contain signed URLs/cookies.
            self.cache.write(task_id, state)
            raise
