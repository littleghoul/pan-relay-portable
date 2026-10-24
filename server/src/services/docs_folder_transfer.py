"""One top-level item per durable document job. Never retry ambiguous writes."""
import hashlib
import json
import time

class TransferRejected(ValueError):
    """A confirmed rejection before any resource transfer."""

class FileCountExceeded(TransferRejected):
    """The provider explicitly rejected this single-item save without a task."""

def is_count_limit(data):
    if not isinstance(data,dict) or data.get('task_id'): return False
    if data.get('errno') in (None,0): return False
    return (data.get('show_msg')=='转存文件数超限' or
            any(i.get('errno') in (120,130) for i in data.get('info') or []))

def rejection_message(data):
    if not isinstance(data,dict) or data.get('task_id'): return None
    errno=data.get('errno')
    if errno in (None,0): return None
    infos=data.get('info') or []
    if errno==12 or any(i.get('errno')==130 for i in infos) or data.get('show_msg')=='转存文件数超限':
        count=data.get('target_file_nums'); limit=data.get('target_file_nums_limit')
        detail=f'共 {count} 个文件，本次账号允许最多 {limit} 个' if count is not None and limit is not None else '超过网盘本次允许的文件数量'
        return f'百度网盘拒绝转存：文件数量超限（{detail}）。请拆分此文件夹后再提交；本次未创建转存任务。'
    return f'百度网盘拒绝转存：{data.get("show_msg") or "网盘返回错误"}（错误码 {errno}）。'

def list_baidu_items(client,surl,parent=None):
    rows=[]; seen=set()
    for page in range(1,102):
        params={
            'web':1,'page':page,'num':100,'order':'name','desc':0,'showempty':0,
            'shorturl':surl,'root':0 if parent else 1,'clienttype':0,'app_id':250528}
        if parent: params.update(dir=parent['path'],shareid=parent['share_id'],uk=parent['uk'])
        data=client._request('GET','https://pan.baidu.com/share/list',params=params,
            headers={'Referer':f'https://pan.baidu.com/s/1{surl}'})
        if data.get('errno')!=0: raise TransferRejected('无法读取百度分享内容，请检查链接、提取码和账号。')
        batch=data.get('list') or []
        for item in batch:
            fid=str(item.get('fs_id') or ''); name=item.get('server_filename')
            if not fid or not name or fid in seen: raise TransferRejected('分享目录返回不完整或重复，已停止，未转存。')
            seen.add(fid); rows.append({'id':fid,'name':name,'path':item.get('path'),
                'isdir':str(item.get('isdir'))=='1','share_id':str(data.get('share_id') or (parent or {}).get('share_id') or ''),
                'uk':str(data.get('uk') or data.get('share_uk') or (parent or {}).get('uk') or '')})
        if len(batch)<100:
            if not rows or any(not r['share_id'] or not r['uk'] for r in rows): raise TransferRejected('分享为空或缺少分享信息。')
            return rows
    raise TransferRejected('分享顶层项目超过 10000 个，请拆分分享后提交。')

def connect(share):
    from src.services.account_pool_manager import AccountPoolManager
    cloud='百度网盘' if share['platform']=='baidu' else '夸克网盘'
    account,client=AccountPoolManager.get_instance().select_account_for_transfer(cloud)
    if client is None: raise TransferRejected(cloud+'没有可用账号，请检查登录状态。')
    if share['platform']=='baidu':
        sid,pwd=client._parse_share_url(share['url'])
        if pwd and not client._verify_pwd(sid,pwd): raise TransferRejected('提取码验证失败，请检查后重新提交。')
        return account,client,sid,None
    sid=client._extract_pwd_id(share['url'])
    data=client._request('POST','https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token',
        payload={'pwd_id':sid,'passcode':share['password']},params={'pr':'ucpro','fr':'pc'})
    token=((data or {}).get('data') or {}).get('stoken')
    if not token: raise TransferRejected('无法读取夸克分享，请检查链接和提取码。')
    return account,client,sid,token

def enumerate_items(share,parent=None):
    _,client,sid,token=connect(share)
    if share['platform']=='baidu': return list_baidu_items(client,sid,parent)
    rows=[]; seen=set()
    for page in range(1,202):
        data=client._request('GET','https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail',
            params={'pwd_id':sid,'stoken':token,'pdir_fid':parent['id'] if parent else 0,'_page':page,'_size':50})
        if not data or data.get('status')!=200: raise TransferRejected('夸克分享目录读取失败，未转存。')
        batch=(data.get('data') or {}).get('list') or []
        for i in batch:
            fid=str(i.get('fid') or '')
            if not fid or not i.get('file_name') or fid in seen: raise TransferRejected('夸克目录信息不完整或重复，未转存。')
            seen.add(fid); rows.append({'id':fid,'name':i['file_name'],'isdir':i.get('dir') is True or i.get('file_type')==0,
                'pdir_fid':parent['id'] if parent else '0'})
        if len(batch)<50:
            if not rows: raise TransferRejected('分享为空。')
            return rows
    raise TransferRejected('分享顶层项目超过 10000 个，请拆分分享后提交。')

def enumerate_children(share,parent):
    # Old jobs may lack folder metadata. Re-read it before descending.
    if 'isdir' not in parent:
        found=next((i for i in enumerate_items(share) if i['id']==parent['id']),None)
        if found: parent={**parent,**found}
    if not parent.get('isdir'): raise TransferRejected('此项目不是可拆分文件夹，无法自动拆分。')
    if share['platform']=='baidu' and not parent.get('path'): raise TransferRejected('原分享未提供文件夹路径，无法自动拆分。')
    ancestors=parent.get('ancestors',[])+[parent['id']]
    if len(ancestors)>64: raise TransferRejected('目录层级超过 64 层，已停止自动拆分，请手动拆分。')
    items=enumerate_items(share,parent)
    if any(i['id'] in ancestors for i in items): raise TransferRejected('网盘返回循环目录，已停止自动拆分。')
    prefix=parent.get('display_name') or parent['name']
    for item in items:
        item.update(ancestors=ancestors,display_name=prefix+' / '+item['name'])
    return items

def transfer_item(share,item):
    from src.db.connection import db_cursor
    from src.db.resources import insert_resource
    from src.pan_operator import _resolve_target_dir
    from src.services.system_config_service import get_transfer_target_dir
    source_key=share['key']+'#item='+item['id']
    with db_cursor(as_dict=True) as cur:
        cur.execute('SELECT * FROM resources WHERE source_key=? AND is_replaced=1',(source_key,))
        existing=cur.fetchone()
    if existing and existing.get('share_link'): return dict(existing)
    account,client,sid,token=connect(share)
    target=_resolve_target_dir(client,target_dir_name=get_transfer_target_dir())
    if share['platform']=='baidu':
        # A private, stable item directory avoids mistaking an old same-name file for this transfer.
        directory=target.rstrip('/')+'/docs-'+hashlib.sha256(source_key.encode()).hexdigest()[:20]
        if not client.ensure_dir(directory): raise TransferRejected('无法创建网盘目标目录，请检查空间和账号。')
        name=item['name']
        if '/' in name or name in ('.','..'): raise TransferRejected('分享文件名无效。')
        path=directory+'/'+name
        if client._get_file_id_by_path(path): raise RuntimeError('目标已存在，需要核对，禁止再次转存')
        data=client._request('POST','https://pan.baidu.com/share/transfer',params={
            'shareid':item['share_id'],'from':item['uk'],'ondup':'fail','async':1,
            'bdstoken':client.bdstoken,'channel':'chunlei','clienttype':0,'web':1,'app_id':250528},
            data={'fsidlist':json.dumps([int(item['id'])]),'path':directory},headers={'Referer':f'https://pan.baidu.com/s/1{sid}'})
        message=rejection_message(data)
        if is_count_limit(data): raise FileCountExceeded(message)
        if message: raise TransferRejected(message)
        if data.get('errno')!=0: raise RuntimeError('转存响应不确定')
        entries=(data.get('extra') or {}).get('list') or []
        fid=entries[0].get('to_fs_id') if entries else None
        task_id=data.get('task_id')
        if task_id and not fid:
            for _ in range(60):
                result=client._request('GET','https://pan.baidu.com/share/taskquery',params={'taskid':task_id,'bdstoken':client.bdstoken,'clienttype':0,'web':1})
                if result.get('errno')==0 and result.get('status')=='success': break
                if result.get('status')=='failed': raise RuntimeError('异步转存失败，需要核对部分结果')
                time.sleep(2)
            else: raise RuntimeError('异步转存未确认完成')
        fid=fid or client._get_file_id_by_path(path)
        if not fid: raise RuntimeError('无法确认转存后的文件')
        link=client._create_share(int(fid)); file_id=path
    else:
        # Refresh short-lived item tokens immediately before each save.
        data=None
        for page in range(1,202):
            response=client._request('GET','https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail',params={'pwd_id':sid,'stoken':token,'pdir_fid':item.get('pdir_fid','0'),'_page':page,'_size':50})
            if not response or response.get('status')!=200: raise TransferRejected('无法刷新夸克分享目录，未转存。')
            batch=(response.get('data') or {}).get('list') or []
            data=next((i for i in batch if str(i.get('fid'))==item['id']),None)
            if data or len(batch)<50: break
        if not data or not data.get('share_fid_token'): raise TransferRejected('该文件夹已从原分享移除或无法读取。')
        response=client._request('POST','https://drive.quark.cn/1/clouddrive/share/sharepage/save',
            payload={'fid_list':[item['id']],'fid_token_list':[data['share_fid_token']], 'to_pdir_fid':target,'pwd_id':sid,'stoken':token,'pdir_fid':item.get('pdir_fid','0'),'scene':'link'},params={'pr':'ucpro','fr':'pc'})
        if response and response.get('status')!=200 and not (response.get('data') or {}).get('task_id'):
            if any(s in str(response.get('message') or '') for s in ('文件数量超限','转存文件数超限','保存文件数量超出')):
                raise FileCountExceeded('夸克网盘拒绝转存：'+response['message'])
            raise TransferRejected('夸克网盘拒绝转存：'+str(response.get('message') or response.get('code')))
        task_id=((response or {}).get('data') or {}).get('task_id')
        if not task_id: raise RuntimeError('未确认夸克转存任务')
        saved=client.task(task_id)
        fids=(((saved or {}).get('data') or {}).get('save_as') or {}).get('save_as_top_fids') or []
        if len(fids)!=1: raise RuntimeError('未确认单个文件夹的转存结果')
        file_id=str(fids[0]); task_id=client.share_task_id(file_id,item['name'])
        if not task_id: raise RuntimeError('已转存，分享结果待核对')
        result=client.task(task_id); share_id=((result or {}).get('data') or {}).get('share_id')
        link=client.get_share_link(share_id) if share_id else None
    if not link: raise RuntimeError('已转存，分享结果待核对')
    record={'file_id':file_id,'name':item.get('display_name') or item['name'],'share_link':link,'cloud_name':'百度网盘' if share['platform']=='baidu' else '夸克网盘',
        'account_id':account['id'],'is_replaced':1,'source_link':share['url'],'source_item_key':source_key}
    if not insert_resource(record): raise RuntimeError('已分享，资源入库失败，请核对')
    return record
