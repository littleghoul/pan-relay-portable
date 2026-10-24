import json
import os
import time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from mcp_client import Client,Rejected,Uncertain,RateLimited
from outbox import Outbox
from sheet_sink import SheetSink
from collector import collect_destinations, active_targets, register_target
from target_config import DEFAULT_TARGET
from migration import sync_gate,apply_migrations

def next_daily_retry(now=None):
    local=datetime.fromtimestamp(time.time() if now is None else now,timezone(timedelta(hours=8)))
    return (local+timedelta(days=1)).replace(hour=0,minute=10,second=0,microsecond=0).timestamp()

def deliver_one(queue,sink,target):
    item=queue.claim(target)
    if item is None: return False
    try:
        sink.append(item['name'],item['share_link'])
    except RateLimited:
        queue.defer_rate_limit(item['id'])
        raise
    except Rejected as error:
        code='authorization_rejected' if str(error)=='authorization_rejected' else 'remote_rejected'
        queue.finish(item['id'],'failed',code)
    except Exception:
        # Even a read-back error may follow a successful remote write.
        queue.finish(item['id'],'uncertain','response_lost')
    else:
        queue.finish(item['id'],'sent')
    return True

def reconcile(queue,sink,target):
    for item in queue.uncertain(target):
        row=sink.find(item['share_link'])
        if row is not None:
            sink.ensure_link(row,item['share_link'])
            queue.confirm_found(item['id'])

def main():
    import fcntl
    os.umask(0o077)
    config=json.loads(Path(os.environ.get('SYNC_CONFIG','/sync/config.json')).read_text(encoding='utf-8'))
    state=Path(config.get('state_dir','/state')); state.mkdir(parents=True,exist_ok=True)
    lock=(state/'worker.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    queue=Outbox(state/'sync.sqlite'); queue.recover_interrupted()
    register_target(queue,DEFAULT_TARGET)
    cooldown_file=state/'cooldown.json'
    try: cooldown=json.loads(cooldown_file.read_text())
    except (OSError,ValueError): cooldown={'retry_at':0,'delay':1800}
    if cooldown.get('retry_at',0)>time.time() and cooldown.get('policy')!='daily':
        cooldown={'retry_at':next_daily_retry(),'policy':'daily'}
        cooldown_file.write_text(json.dumps(cooldown))
    while True:
        error=''
        with sync_gate(Path(config.get('jobs_db','/source/docs_jobs.sqlite')).parent/'docs_sync_gate.lock'):
            try:
                apply_migrations(queue,config.get('jobs_db','/source/docs_jobs.sqlite'))
                current=collect_destinations(queue,config.get('source_snapshot','/source/docs_resources.json'),config.get('jobs_db','/source/docs_jobs.sqlite'),DEFAULT_TARGET)
                counts=queue.counts()
                if time.time()<cooldown['retry_at']:
                    error='rate_limited'
                elif counts.get('pending') or counts.get('uncertain'):
                    token=Path(config['token_file']).read_text().strip()
                    client=Client(token); client.initialize()
                    try:
                        for target,destination in active_targets(queue):
                            try:
                                sink=SheetSink(client,destination['file_id'],destination['sheet_id'],destination['start_cell'])
                                reconcile(queue,sink,target)
                                for _ in range(10):
                                    if not deliver_one(queue,sink,target): break
                            except RateLimited:
                                raise
                            except Exception:
                                error='target_sync_check_failed'
                    finally:
                        client.session.close()
                    cooldown={'retry_at':0,'policy':'daily'}
                    cooldown_file.write_text(json.dumps(cooldown))
            except RateLimited:
                error='rate_limited'
                cooldown={'retry_at':next_daily_retry(),'policy':'daily'}
                cooldown_file.write_text(json.dumps(cooldown))
            except Rejected:
                error='tencent_connection_rejected'
            except Exception:
                error='sync_check_failed'
        status={'checked_at':time.time(),'counts':queue.counts(),'error':error,'retry_at':cooldown['retry_at']}
        temp=state/'status.tmp'; temp.write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8'); temp.replace(state/'status.json')
        print(json.dumps(status,ensure_ascii=False),flush=True)
        time.sleep(30)

if __name__=='__main__': main()
