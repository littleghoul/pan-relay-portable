"""Durable submissions and a read-only view of the existing document outbox."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
from docs_sync.target_config import DEFAULT_TARGET, normalize_target, target_key

DOCUMENT_URL = ''
TARGET = 'unconfigured'
URL_PATTERN = re.compile(r'https?://(?:(?!https?://)[A-Za-z0-9_~:/?#\[\]@!$&\x27()*+,;=%.-])+')

class BatchValidationError(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__(f'发现 {len(errors)} 处问题，已在粘贴框内标红，请修改后再预览。')

def input_error(text,start,end,message):
    return {'start':len(text[:start].encode('utf-16-le'))//2,
            'end':len(text[:end].encode('utf-16-le'))//2,
            'line':text.count('\n',0,start)+1,'message':message}

def parse_share(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
        raise ValueError('请粘贴一条网盘分享内容（不超过 10000 字）。')
    urls = [m.group().rstrip('.,;') for m in URL_PATTERN.finditer(text)]
    if len(urls) != 1:
        raise ValueError('每次请提交一条百度或夸克网盘链接。')
    u = urlsplit(urls[0])
    if (u.scheme != 'https' or u.netloc not in ('pan.baidu.com','pan.quark.cn')
            or not re.fullmatch(r'/s/[A-Za-z0-9_-]+',u.path)):
        raise ValueError('仅支持 https://pan.baidu.com/s/ 或 https://pan.quark.cn/s/ 分享链接。')
    pwd = parse_qs(u.query).get('pwd',[''])[0]
    match = re.search(r'(?:提取码|访问码|密码)\s*[:：]?\s*([A-Za-z0-9]{4})(?![A-Za-z0-9])',text)
    if not pwd and match: pwd=match.group(1)
    if pwd and not re.fullmatch(r'[A-Za-z0-9]{4}',pwd):
        raise ValueError('提取码应为 4 位字母或数字。')
    return {'url':f'https://{u.netloc}{u.path}'+('?' + urlencode({'pwd':pwd}) if pwd else ''),
            'key':u.netloc+u.path,'platform':'baidu' if u.netloc=='pan.baidu.com' else 'quark','password':pwd}

def parse_batch(text):
    if not isinstance(text,str) or not text.strip() or len(text)>100000:
        raise ValueError('请粘贴分享内容，最多 100000 字、50 条链接。')
    matches=list(URL_PATTERN.finditer(text))
    if not 1<=len(matches)<=50:
        start=matches[50].start() if len(matches)>50 else 0
        raise BatchValidationError([input_error(text,start,len(text),'每批支持 1 至 50 条完整的 https 网盘链接。')])
    shares={}; positions={}; errors=[]
    for i,m in enumerate(matches):
        segment=text[m.start():matches[i+1].start() if i+1<len(matches) else len(text)]
        markers=list(re.finditer(r'(?:提取码|访问码|密码)(?:\s*[:：]\s*|\s+(?=[A-Za-z0-9]))([A-Za-z0-9]*)',segment))
        invalid=[c for c in markers if len(c.group(1))!=4]
        for c in invalid:
            errors.append(input_error(text,m.start()+c.start(),m.start()+c.end(),'提取码应为 4 位字母或数字。'))
        try: share=parse_share(segment)
        except ValueError as error:
            errors.append(input_error(text,m.start(),m.end(),str(error))); continue
        codes={c.group(1) for c in markers if len(c.group(1))==4}
        if len(codes)>1 or (codes and share['password'] not in codes):
            message=f'第 {i+1} 条链接中的提取码与文字提取码不一致，请核对。'
            errors.append(input_error(text,m.start(),m.end(),message))
            for c in markers: errors.append(input_error(text,m.start()+c.start(),m.start()+c.end(),message))
        old=shares.get(share['key'])
        if old and old['password'] and share['password'] and old['password']!=share['password']:
            previous=positions[share['key']]
            for a,b in [previous,(m.start(),m.end())]: errors.append(input_error(text,a,b,'同一链接出现不同提取码，请核对这两处。'))
        if not old or (not old['password'] and share['password']): shares[share['key']]=share
        positions.setdefault(share['key'],(m.start(),m.end()))
    if errors: raise BatchValidationError(errors)
    return list(shares.values())

class Jobs:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS docs_batch_jobs (
                id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, source_url TEXT NOT NULL,
                platform TEXT NOT NULL, status TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                share_link TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL, target_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(source_key,target))''')
            db.execute('CREATE TABLE IF NOT EXISTS docs_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            columns={r[1] for r in db.execute('PRAGMA table_info(docs_batch_jobs)')}
            if 'parent_id' not in columns: db.execute('ALTER TABLE docs_batch_jobs ADD COLUMN parent_id INTEGER')
            if 'item_json' not in columns: db.execute("ALTER TABLE docs_batch_jobs ADD COLUMN item_json TEXT NOT NULL DEFAULT ''")
            db.execute('INSERT OR IGNORE INTO docs_settings VALUES(?,?)',('target',json.dumps(DEFAULT_TARGET,ensure_ascii=False)))
            if not db.execute("SELECT 1 FROM docs_settings WHERE key='legacy_migrated'").fetchone():
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs_jobs'").fetchone():
                    db.execute('''INSERT OR IGNORE INTO docs_batch_jobs(source_key,source_url,platform,status,name,share_link,message,target,target_json,created_at,updated_at)
                        SELECT source_key,source_url,platform,status,name,share_link,message,?,?,created_at,updated_at FROM docs_jobs''',
                        (target_key(DEFAULT_TARGET),json.dumps(DEFAULT_TARGET,ensure_ascii=False)))
                db.execute("INSERT INTO docs_settings VALUES('legacy_migrated','1')")
    @contextmanager
    def db(self):
        db=sqlite3.connect(str(self.path),timeout=10); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def get_target(self):
        with self.db() as db: return normalize_target(json.loads(db.execute("SELECT value FROM docs_settings WHERE key='target'").fetchone()[0]))
    def save_target(self,target):
        value=normalize_target(target)
        with self.db() as db: db.execute("UPDATE docs_settings SET value=? WHERE key='target'",(json.dumps(value,ensure_ascii=False),))
        return value
    def submit_batch(self,shares,target,local_id=None):
        if local_id is not None:
            if not re.fullmatch(r'[a-f0-9]{32}',local_id): raise ValueError('无效本地批次。')
            key='local:'+local_id; target={'mode':'local','batch_id':local_id}
        else:
            target=normalize_target(target); key=target_key(target)
        created=0; ids=[]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute("SELECT count(*) FROM docs_batch_jobs WHERE status IN ('queued','transferring')").fetchone()[0]
            if pending+len(shares)>200: raise ValueError('待处理任务较多，请等待当前批次完成。')
            for share in shares:
                row=db.execute('SELECT * FROM docs_batch_jobs WHERE source_key=? AND target=?',(share['key'],key)).fetchone()
                if row and local_id is not None: ids.append(row['id']); continue
                if row and row['status']=='expanded':
                    descendants=[r[0] for r in db.execute('WITH RECURSIVE children(id) AS (SELECT id FROM docs_batch_jobs WHERE parent_id=? UNION ALL SELECT j.id FROM docs_batch_jobs j JOIN children c ON j.parent_id=c.id) SELECT id FROM children',(row['id'],))]
                    count=0
                    for child_id in descendants:
                        count+=db.execute("UPDATE docs_batch_jobs SET status='queued',message='',source_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='failed'",(share['url'],child_id)).rowcount
                    ids.append(row['id']); created+=bool(count); continue
                if row and row['status']!='failed': ids.append(row['id']); continue
                if row:
                    db.execute("UPDATE docs_batch_jobs SET status='queued',source_url=?,message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",(share['url'],row['id']))
                    job_id=row['id']
                else:
                    job_id=db.execute("INSERT INTO docs_batch_jobs(source_key,source_url,platform,status,target,target_json) VALUES(?,?,?,'queued',?,?)",
                                      (share['key'],share['url'],share['platform'],key,json.dumps(target,ensure_ascii=False))).lastrowid
                created+=1; ids.append(job_id)
        return {'ids':ids,'created':created,'existing':len(shares)-created}
    def local_batches(self):
        with self.db() as db:
            rows=db.execute("""SELECT target,MIN(created_at) AS created_at,
                SUM(status='awaiting' AND share_link!='') AS ready,
                SUM(status IN ('queued','transferring')) AS pending,
                SUM(status='failed') AS failed,SUM(status='uncertain') AS uncertain
                FROM docs_batch_jobs WHERE target LIKE 'local:%' GROUP BY target ORDER BY MIN(id) DESC""").fetchall()
        return [dict(r,batch_id=r['target'][6:],complete=r['pending']==0) for r in rows]
    def local_rows(self,batch_id):
        if not re.fullmatch(r'[a-f0-9]{32}',batch_id): raise ValueError('无效批次。')
        with self.db() as db:
            rows=db.execute('SELECT status,name,share_link FROM docs_batch_jobs WHERE target=? ORDER BY id',('local:'+batch_id,)).fetchall()
        if not rows: raise ValueError('找不到此批次。')
        if any(r['status'] in ('queued','transferring') for r in rows): raise ValueError('批次仍在处理中，请完成后下载。')
        return [(r['name'],r['share_link']) for r in rows if r['status']=='awaiting' and r['share_link']]
    def submit(self,share):
        result=self.submit_batch([share],self.get_target())
        with self.db() as db: row=dict(db.execute('SELECT * FROM docs_batch_jobs WHERE id=?',(result['ids'][0],)).fetchone())
        return row,bool(result['created'])
    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM docs_batch_jobs WHERE status='transferring' LIMIT 1").fetchone(): return None
            row=db.execute("SELECT * FROM docs_batch_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row: return None
            db.execute("UPDATE docs_batch_jobs SET status='transferring',updated_at=CURRENT_TIMESTAMP WHERE id=?",(row['id'],))
            return dict(row)
    def recover(self):
        with self.db() as db:
            db.execute("UPDATE docs_batch_jobs SET status='uncertain',message='服务重启，转存结果待核对；请先查看我的资源，避免重复转存。' WHERE status='transferring'")
    def list(self):
        with self.db() as db: return [dict(r) for r in db.execute("SELECT * FROM docs_batch_jobs WHERE status!='expanded' ORDER BY id DESC LIMIT 200")]
    def expand(self,parent,items):
        if not items: raise ValueError('分享为空，未转存。')
        if len({str(i['id']) for i in items})!=len(items): raise ValueError('分享目录包含重复项目，未转存。')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            source_key=parse_share(parent['source_url'])['key']
            for item in items:
                db.execute("""INSERT OR IGNORE INTO docs_batch_jobs(source_key,source_url,platform,status,name,target,target_json,parent_id,item_json)
                    VALUES(?,?,?,'queued',?,?,?,?,?)""",(source_key+'#item='+str(item['id']),parent['source_url'],parent['platform'],item.get('display_name') or item['name'],parent['target'],parent['target_json'],parent['id'],json.dumps(item,ensure_ascii=False)))
            db.execute("UPDATE docs_batch_jobs SET status='expanded',message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(f'已拆分为 {len(items)} 个项目',parent['id']))
    def fail(self,job_id,message,uncertain=False):
        with self.db() as db:
            db.execute('UPDATE docs_batch_jobs SET status=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',('uncertain' if uncertain else 'failed',message,job_id))
    def complete(self,job_id,result):
        if (not isinstance(result,dict) or not result.get('is_replaced') or not result.get('account_id')
                or not result.get('file_id') or not result.get('name') or not result.get('share_link')):
            self.fail(job_id,'未能确认转存和分享结果，请检查我的资源及账号状态后再处理。',True); return
        try: parse_share(result['share_link'].split()[0])
        except ValueError:
            self.fail(job_id,'返回的分享链接无效，需要核对结果。',True); return
        with self.db() as db:
            db.execute("UPDATE docs_batch_jobs SET status='awaiting',name=?,share_link=?,message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",(result['name'],result['share_link'],job_id))

def sync_snapshot(root,target=TARGET):
    root=Path(root)
    result={'healthy':False,'deliveries':[],'message':'文档同步服务暂不可用，已转存结果会保留。'}
    try:
        status=json.loads((root/'status.json').read_text())
        result['healthy']=0<=time.time()-status['checked_at']<180 and not status.get('error')
        result['message']='同步服务正常，通常 30 秒内追加到文档。' if result['healthy'] else '同步服务异常或状态过期，尚未同步的结果会保留。'
        if status.get('error')=='rate_limited':
            from datetime import datetime,timezone,timedelta
            retry=datetime.fromtimestamp(status.get('retry_at',0),timezone(timedelta(hours=8))).strftime('%m月%d日 %H:%M')
            result['message']=f'腾讯接口额度或会员限制（400007），同步已挂起。下次尝试：{retry}（北京时间）。恢复后先核对已有链接，只补缺失行，不重复转存；次日不保证解除限制。'
        with closing(sqlite3.connect((root/'sync.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            db.row_factory=sqlite3.Row
            if target is None:
                result['deliveries']=[dict(r) for r in db.execute("SELECT target,name,share_link,status,error_code FROM deliveries WHERE status!='migrated' ORDER BY rowid DESC LIMIT 500")]
            else:
                result['deliveries']=[dict(r) for r in db.execute("SELECT target,name,share_link,status,error_code FROM deliveries WHERE target=? AND status!='migrated' ORDER BY rowid DESC LIMIT 200",(target,))]
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError): pass
    return result

