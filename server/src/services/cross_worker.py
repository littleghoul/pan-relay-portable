"""Single-worker NAS transfer runner. Cloud credentials never enter job records."""
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import time
import urllib.parse
import requests
from src.services import cross_jobs
from src.services.transfer_cache import TransferCache
from src.services.cross_transfer import CrossTransfer, file_hash
from src.services.transfer_io import write_stream
from src.services.transfer_manifest import scan
from src.clients.baidu_client import BaiduPanClient
from src.clients.quark_client import QuarkPanClient


def name(value):
    if not isinstance(value,str) or not value or value in ('.','..') or '/' in value or '\\' in value or '\0' in value:
        raise ValueError('资源文件名无法安全传输')
    return value


def download_baidu(backend, source, local, progress=None, *, resume=True, expected_size=None):
    service=backend._ctx().下载
    url=service.取直链(source)
    if local.is_symlink():raise ValueError('Unsafe transfer cache file')
    offset=local.stat().st_size if resume and local.exists() else 0
    headers=service._下载请求头(offset)
    headers['User-Agent']='LogStatistic'
    headers['Range']=f'bytes={offset}-'
    total=int(backend.stat(source)['size'])
    if expected_size is not None and total!=expected_size:
        raise ValueError('Downloaded file size mismatch')
    if offset>total:raise ValueError('Downloaded file size mismatch')
    if offset==total and resume and local.exists():return
    if total==0:
        write_stream(local,[],0);return
    with requests.get(url,headers=headers,stream=True,timeout=(30,300)) as response:
        response.raise_for_status()
        if response.status_code==206:
            if not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                raise ValueError('Downloaded file size mismatch')
        else:offset=0
        write_stream(local,response.iter_content(1024*1024),total,offset,progress)


class OpenList:
    def __init__(self):
        self.base = os.getenv('OPENLIST_URL','http://openlist-transfer-test:5244')
        self.auth = json.loads(Path('/service/openlist-auth.json').read_text())
        self.token = ''
        self.login()

    def login(self):
        r = requests.post(self.base+'/api/auth/login',json=self.auth,timeout=30).json()
        self.token = r.get('data',{}).get('token','')
        if not self.token: raise RuntimeError('传输服务登录失败')

    def call(self, path, data=None, method='POST', **kwargs):
        headers = kwargs.pop('headers',{})
        body = kwargs.pop('body',None)
        for attempt in range(2):
            if body is not None:body.seek(0)
            r=requests.request(method,self.base+path,json=data if body is None else None,data=body,headers={**headers,'Authorization':self.token},timeout=600,**kwargs)
            r.raise_for_status(); response=r.json()
            if response.get('code') == 401 and attempt == 0: self.login(); continue
            if response.get('code') != 200: raise RuntimeError('网盘操作失败，请检查账号有效性、容量和资源状态')
            return response.get('data')

    def use_quark(self, cookie):
        rows=self.call('/api/admin/storage/list?page=1&per_page=100',method='GET')['content']
        storage=next(s for s in rows if s['mount_path']=='/quark-test')
        storage={k:v for k,v in storage.items() if k!='mount_details'}
        addition=json.loads(storage['addition']);addition['cookie']=cookie
        addition['use_transcoding_address']=False;addition['only_list_video_file']=False
        storage['addition']=json.dumps(addition);self.call('/api/admin/storage/update',storage)

    def stat(self,path):
        self.call('/api/fs/list',dict(path=str(PurePosixPath(path).parent),page=1,per_page=100,refresh=True))
        return self.call('/api/fs/get',dict(path=path))

    def download(self,path,local,expected_size):
        item=self.stat(path);url=item['raw_url']
        if url.startswith('/'):url=self.base+url
        if not url.startswith(self.base+'/'): raise ValueError('下载代理地址异常')
        with requests.get(url,headers={'Authorization':self.token},stream=True,timeout=(30,300)) as r:
            r.raise_for_status()
            write_stream(local,r.iter_content(1024*1024),expected_size)

    def upload(self,path,local):
        with local.open('rb') as f:
            return self.call('/api/fs/put',method='PUT',headers={'File-Path':urllib.parse.quote(path),'Overwrite':'false','Content-Type':'application/octet-stream'},body=f)


def accounts(source_id, source_cloud, target_id=None):
    with sqlite3.connect('file:'+os.getenv('SOURCE_DB','/source/pan_relay.db')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        src=db.execute('SELECT * FROM cloud_accounts WHERE id=? AND cloud_name=? AND is_active=1 AND is_valid=1',(source_id,source_cloud)).fetchone()
        other='夸克网盘' if source_cloud=='百度网盘' else '百度网盘'
        if target_id is not None:
            dst=db.execute('SELECT * FROM cloud_accounts WHERE id=? AND cloud_name=? AND is_active=1 AND is_valid=1',(target_id,other)).fetchone()
        else:
            dst=db.execute('SELECT * FROM cloud_accounts WHERE cloud_name=? AND is_active=1 AND is_valid=1 ORDER BY priority,id LIMIT 1',(other,)).fetchone()
    if not src or not dst: raise ValueError('源账号或目标账号不可用，请重新登录')
    return dict(src),dict(dst)


def run(job):
    row=job['payload'];jid=job['id'];result=job['result']
    src,dst=accounts(row['account_id'],row['cloud_name'],result.get('target_account_id'))
    # Freeze target account on the first attempt: never silently switch accounts.
    if result.get('target_account_id') and result['target_account_id']!=dst['id']:
        raise ValueError('目标账号发生变化，任务保留；请恢复原目标账号')
    result['target_account_id']=dst['id']
    result['source_share']=row['share_link']
    result['baidu_share' if row['cloud_name']=='百度网盘' else 'quark_share']=row['share_link']
    baidu=src if src['cloud_name']=='百度网盘' else dst
    quark=src if src['cloud_name']=='夸克网盘' else dst
    sys.path[:0]=['/research/bridge','/research/adapter']
    from 后端_百度 import 后端
    backend=后端('/research/adapter');backend._试从浏览器补全会话=lambda:False
    try:
        if backend.login_cookie(baidu['credential']).get('状态')!='成功': raise ValueError('百度登录失效，请重新扫码')
        # Baidu's download host rejects browser UA for some real resources.
        download_service=backend._ctx().下载
        original_headers=download_service._下载请求头
        def download_headers(offset=0):
            headers=original_headers(offset)
            headers['User-Agent']='LogStatistic'
            return headers
        download_service._下载请求头=download_headers
        ol=OpenList();ol.use_quark(quark['credential'])
        qc=QuarkPanClient(quark['credential'])
        qc.session.headers['User-Agent']='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4 Safari/537.36'
        cache=TransferCache();runner=CrossTransfer(cache)
        target='/PanRelayTransfers/'+jid
        result['target_path']=target
        cross_jobs.update(jid,result=result,progress='扫描资源文件')
        if 'manifest' not in result:
            files,directories=scan(row,backend,qc)
            result.update(manifest=files,directories=directories,total=len(files),done=0)
            if row['cloud_name']=='夸克网盘' and any(any(ch in entry['relative'] for ch in '<>:"|?*') for entry in files):
                raise ValueError('源文件名含百度不支持的字符，请先重命名后再试')
            cross_jobs.update(jid,result=result)
        if row['cloud_name']=='百度网盘':
            ol.call('/api/fs/mkdir',dict(path='/quark-test'+target))
            for directory in result['directories']:ol.call('/api/fs/mkdir',dict(path='/quark-test'+target+'/'+directory))
        else:
            backend.ensure_dir(target)
            for directory in result['directories']:backend.ensure_dir(target+'/'+directory)
        for index,item in enumerate(result['manifest']):
            result['current_file']=item['relative']
            result['stage']='downloading'
            cross_jobs.update(jid,result=result)
            cross_jobs.update(jid,progress=f"传输并校验文件 {index+1}/{result['total']}")
            if not item.get('cache_id'):
                item['cache_id']=cache.create(item['size'],item['source'],target+'/'+item['relative'])
                cross_jobs.update(jid,result=result)
            def download(source,local):
                result['stage']='downloading'
                cross_jobs.update(jid,result=result,progress=f"下载源文件 {index+1}/{result['total']}")
                last=[0]
                def report(phase,done,total):
                    if time.monotonic()-last[0]<1 and done!=total:return
                    last[0]=time.monotonic()
                    result['bytes_done']=done;result['bytes_total']=total
                    cross_jobs.update(jid,result=result,progress=f"下载源文件 {index+1}/{result['total']} · {done/1048576:.1f}/{total/1048576:.1f} MiB")
                if row['cloud_name']=='百度网盘':download_baidu(backend,source,local,report,expected_size=item['size'])
                else:
                    data=qc._request('POST','https://drive-pc.quark.cn/1/clouddrive/file/download',payload={'fids':[source]},params={'pr':'ucpro','fr':'pc'})
                    info=((data or {}).get('data') or [])[0]
                    with qc.session.get(info['download_url'],stream=True,timeout=(30,300)) as response:
                        response.raise_for_status()
                        write_stream(local,response.iter_content(1024*1024),item['size'],progress=report)
            def verify(remote,size,digest):
                result['stage']='verifying'
                cross_jobs.update(jid,result=result,progress=f"校验目标文件 {index+1}/{result['total']}")
                p=cache.directory(item['cache_id'])/'verification'
                if row['cloud_name']=='百度网盘':ol.download('/quark-test'+remote,p,size)
                else:download_baidu(backend,remote,p,resume=False,expected_size=size)
                return p.stat().st_size==size and file_hash(p)==digest
            def upload(local,destination,digest):
                # Reconcile any previous remote commit before retrying an upload.
                try:
                    if verify(destination,item['size'],digest):return destination
                    raise ValueError('目标位置已有不同内容，保留任务以避免覆盖')
                except ValueError:raise
                except Exception:pass
                result['stage']='uploading'
                cross_jobs.update(jid,result=result,progress=f"上传目标文件 {index+1}/{result['total']}")
                if row['cloud_name']=='百度网盘':ol.upload('/quark-test'+destination,local);time.sleep(3);return destination
                renamed=local.with_name(name(PurePosixPath(destination).name))
                if renamed!=local:
                    import shutil
                    shutil.copyfile(local,renamed)
                return backend.upload(str(renamed),str(PurePosixPath(destination).parent),renamed.name,jid,None)['path']
            runner.run(item['cache_id'],download,upload,verify)
            result['done']=index+1;cross_jobs.update(jid,result=result)
        cross_jobs.update(jid,progress='文件校验完成，生成目标分享链接')
        if not result.get('target_share'):
            if row['cloud_name']=='百度网盘':
                parent=qc.get_or_create_dir('PanRelayTransfers')
                folder_id=qc.get_or_create_dir(jid,parent)
                tid=qc.share_task_id(folder_id,row['name'])
                share=qc.task(tid);sid=((share or {}).get('data') or {}).get('share_id')
                link=qc.get_share_link(sid) if sid else ''
            else:
                client=BaiduPanClient(baidu['credential']);fid=client._get_file_id_by_path(target)
                link=client._create_share(fid) if fid else ''
            if not link:raise RuntimeError('文件已传完，但目标分享生成失败；可重试分享阶段')
            result['target_share']=link
        result['source_share']=row['share_link']
        result['baidu_share']=row['share_link'] if row['cloud_name']=='百度网盘' else result['target_share']
        result['quark_share']=row['share_link'] if row['cloud_name']=='夸克网盘' else result['target_share']
        cross_jobs.update(jid,status='complete',progress='双盘分享已生成',result=result,error='')
    finally:backend.close()


def write_debug(error):
    import traceback
    try:
        Path('/app/data/cross-worker-debug.json').write_text(json.dumps({'type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)]}))
    except OSError:
        # Optional diagnostics must not prevent recording the failed task.
        pass


def main():
    logging.disable(logging.CRITICAL)
    lock=open(os.getenv('CROSS_JOBS_DB','/app/data/cross_jobs.sqlite')+'.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cross_jobs.recover()
    while True:
        job=cross_jobs.claim()
        if job:
            try:run(job)
            except Exception as e:
                write_debug(e)
                safe_messages={'资源文件名无法安全传输','下载代理地址异常','源账号或目标账号不可用，请重新登录','目标账号发生变化，任务保留；请恢复原目标账号','百度登录失效，请重新扫码','源文件已不存在','源目录为空或无法读取原文件','没有可传输的文件','文件数量超过单任务 10000 个限制','资源目录过深、重复或数量超过扫描限制','源文件名含百度不支持的字符，请先重命名后再试','目标位置已有不同内容，保留任务以避免覆盖'}
                error_messages={'Insufficient transfer cache space':'NAS 缓存空间不足，请释放空间后重试；已有缓存会保留','Downloaded file size mismatch':'下载数据大小与文件记录不一致，已停止写入；请检查源文件后重试','Target verification failed':'目标文件内容校验失败，缓存已保留；重试时会重新下载校验文件'}
                code=getattr(e,'状态码',None) or getattr(getattr(e,'response',None),'status_code',None)
                current=next((j['result'] for j in cross_jobs.list_jobs() if j['id']==job['id']),{})
                stage={'downloading':'源文件下载','uploading':'目标文件上传','verifying':'目标文件校验'}.get(current.get('stage'),'准备或分享')
                message=error_messages.get(str(e)) or (str(e) if str(e) in safe_messages else stage+'失败'+('（HTTP '+str(code)+'）' if code else '（'+type(e).__name__+'）')+'。请检查对应网盘登录与下载权限后重试；已完成的文件会保留。')
                cross_jobs.update(job['id'],status='failed',error=message,progress='任务保留，等待重试')
        else:time.sleep(3)


if __name__=='__main__':main()
