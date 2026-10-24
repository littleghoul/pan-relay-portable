import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from flask import Blueprint,jsonify,render_template,request,session,send_file
from src.utils.auth_utils import token_required
from src.services.docs_transfer_service import Jobs,parse_share,parse_batch,sync_snapshot,DOCUMENT_URL,BatchValidationError
from src.services.docs_destination_service import tencent_client,inspect_document,inspect_target
from src.services.docs_folder_transfer import enumerate_items,enumerate_children,transfer_item,TransferRejected,FileCountExceeded
from docs_sync.target_config import DEFAULT_TARGET,target_key
from docs_sync.migration import sync_gate,save_and_migrate

docs_bp=Blueprint('docs_transfer',__name__)
_jobs=None
_init_lock=threading.Lock()
_worker_started=False

def get_jobs():
    global _jobs
    with _init_lock:
        if _jobs is None:
            _jobs=Jobs(os.environ.get('DOCS_JOBS_DB','/app/data/docs_jobs.sqlite')); _jobs.recover()
            (_jobs.path.parent/'docs_sync_gate.lock').touch(exist_ok=True)
    return _jobs

def process_next(jobs):
    row=jobs.claim()
    if not row: return False
    try:
        share=parse_share(row['source_url'])
        if row.get('item_json'):
            jobs.complete(row['id'],transfer_item(share,json.loads(row['item_json'])))
        else:
            from src.db.resources import get_resource_by_share_link
            existing=get_resource_by_share_link(share['url'])
            if existing and existing.get('is_replaced') and existing.get('share_link'):
                jobs.complete(row['id'],existing)
            else: jobs.expand(row,enumerate_items(share))
    except FileCountExceeded as error:
        try:
            jobs.expand(row,enumerate_children(share,json.loads(row['item_json'])))
        except Exception as split_error:
            reason=str(split_error) if isinstance(split_error,TransferRejected) else '读取子目录暂时失败，请重新提交原链接重试。'
            jobs.fail(row['id'],str(error)+' 自动拆分未完成：'+reason)
    except ValueError as error:
        known=not row.get('item_json') or isinstance(error,TransferRejected)
        jobs.fail(row['id'],str(error) if known else '网盘返回异常，转存结果待核对。',not known)
    except Exception: jobs.fail(row['id'],'转存过程中连接中断，结果待核对；请先查看我的资源。',True)
    return True

def start_docs_worker(app):
    global _worker_started
    jobs=get_jobs()
    with _init_lock:
        if _worker_started: return
        _worker_started=True
    def snapshots():
        from src.configs.app_config import SQLITE_DB_PATH
        from docs_sync.resource_snapshot import export_resources
        while True:
            try: export_resources(SQLITE_DB_PATH,'/app/data/docs_resources.json')
            except Exception: app.logger.error('docs resource snapshot refresh failed')
            time.sleep(10)
    threading.Thread(target=snapshots,daemon=True,name='docs-resource-snapshot').start()
    def run():
        with app.app_context():
            while True:
                try:
                    with sync_gate(jobs.path.parent/'docs_sync_gate.lock'):
                        processed=process_next(jobs)
                    if processed: continue
                except Exception: app.logger.error('docs transfer queue check failed')
                time.sleep(2)
    threading.Thread(target=run,daemon=True,name='docs-transfer-queue').start()

def csrf_token():
    if 'docs_csrf' not in session: session['docs_csrf']=secrets.token_urlsafe(32)
    return session['docs_csrf']

def verify_post():
    token=request.headers.get('X-CSRF-Token','')
    if not token or not hmac.compare_digest(token,session.get('docs_csrf','')):
        return jsonify(message='页面验证已过期，请刷新后重试。'),403
    if request.content_length and request.content_length>500000: return jsonify(message='分享内容过长。'),413

def fingerprint(text,target):
    return hashlib.sha256((text+'\n'+target_key(target)).encode()).hexdigest()

@docs_bp.route('/admin/docs-transfer')
@token_required
def page():
    return render_template('docs_transfer.html',active_page='docs_transfer',document_url=DOCUMENT_URL)

@docs_bp.route('/admin/api/docs-transfer/settings',methods=['POST'])
@token_required
def settings_api():
    denied=verify_post()
    if denied: return denied
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict): return jsonify(message='无效设置。'),400
    action=data.get('action')
    if action not in ('inspect','preview','save'): return jsonify(message='无效操作。'),400
    try:
        with tencent_client() as client:
            if action=='inspect': return jsonify(inspect_document(data.get('url'),client))
            target,preview=inspect_target(data.get('target'),client)
        message='预览完成，尚未写入文档。'
        if action=='save':
            if data.get('migrate_pending') is True:
                counts=save_and_migrate(get_jobs(),os.environ.get('DOCS_SYNC_STATE','/docs-sync-state'),target)
                message=f"已保存并迁移 {counts['jobs']} 条任务，{counts['deliveries']} 条已有分享的记录将只同步到新目标。已同步记录保留在原文档；原有限流挂起时间不变。"
            else:
                get_jobs().save_target(target)
                message='设置已保存，仅影响之后提交的任务。'
            session.pop('batch_preview',None)
        return jsonify(target=target,preview=preview,message=message)
    except ValueError as e: return jsonify(message=str(e)),400
    except Exception: return jsonify(message='无法读取腾讯文档，请检查链接、账号访问权限及连接凭据。'),502

STATUS={'pending':'awaiting','sending':'syncing','sent':'sent','failed':'sync_failed','uncertain':'sync_uncertain'}

@docs_bp.route('/admin/api/docs-transfer/local/<batch_id>/export')
@token_required
def local_export_api(batch_id):
    import io
    from docs_sync.excel_export import build_workbook
    try: data,count=build_workbook(get_jobs().local_rows(batch_id))
    except ValueError as error: return jsonify(message=str(error)),409
    result=send_file(io.BytesIO(data),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='网盘分享-'+batch_id[:8]+'.xlsx')
    result.headers['Cache-Control']='no-store'; result.headers['X-Export-Count']=str(count)
    return result

@docs_bp.route('/admin/api/docs-transfer/export')
@token_required
def export_api():
    import io
    from pathlib import Path
    from docs_sync.excel_export import export_pending
    target=get_jobs().get_target()
    try:
        data,count=export_pending(Path(os.environ.get('DOCS_SYNC_STATE','/docs-sync-state'))/'sync.sqlite',target_key(target))
    except Exception:
        return jsonify(message='读取待同步记录失败，请稍后再试。'),503
    result=send_file(io.BytesIO(data),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='未同步记录.xlsx')
    result.headers['Cache-Control']='no-store'
    result.headers['X-Export-Count']=str(count)
    return result

@docs_bp.route('/admin/api/docs-transfer',methods=['GET','POST'])
@token_required
def api():
    if request.method=='GET':
        jobs=get_jobs(); current=jobs.get_target()
        snap=sync_snapshot(os.environ.get('DOCS_SYNC_STATE','/docs-sync-state'),None)
        deliveries={(r.get('target',target_key(DEFAULT_TARGET)),parse_share(r['share_link'].split()[0])['key']):r for r in snap['deliveries']}
        records=[]; seen=set()
        for row in jobs.list():
            row['destination']=json.loads(row.pop('target_json'))
            row.pop('item_json',None)
            if row['destination'].get('mode')=='local':
                if row['status']=='awaiting': row['status']='local_ready'
                records.append(row); continue
            if row['share_link']:
                key=(row['target'],parse_share(row['share_link'].split()[0])['key']); seen.add(key)
                delivery=deliveries.get(key)
                if delivery:
                    row['status']=STATUS.get(delivery['status'],'awaiting')
                    if delivery.get('error_code')=='rate_limited':
                        row['message']='腾讯接口额度或会员限制（400007）。已挂起，次日自动重试；可先导出 Excel 手动补入。'
            records.append(row)
        for key,row in deliveries.items():
            if key not in seen:
                dest=DEFAULT_TARGET if key[0]==target_key(DEFAULT_TARGET) else (current if key[0]==target_key(current) else None)
                records.append({**row,'status':STATUS.get(row['status'],'awaiting'),'message':'','destination':dest})
        return jsonify(csrf=csrf_token(),records=records,local_batches=jobs.local_batches(),sync={'healthy':snap['healthy'],'message':snap['message']},target=current,document_url=current['url'])
    denied=verify_post()
    if denied: return denied
    data=request.get_json(silent=True) or {}
    try:
        text=data.get('text') if isinstance(data,dict) else None
        shares=parse_batch(text); jobs=get_jobs(); target=jobs.get_target()
        if data.get('mode')=='local':
            digest=hashlib.sha256(('local\n'+text).encode()).hexdigest()
            if data.get('action')=='preview':
                session['local_preview']={'digest':digest,'id':secrets.token_hex(16)}
                return jsonify(shares=shares,message=f'识别到 {len(shares)} 条不同链接；仅转存并导出 Excel，不写入腾讯文档。')
            saved=session.get('local_preview',{})
            if not hmac.compare_digest(saved.get('digest',''),digest):
                return jsonify(message='请先重新预览本地 Excel 批次。'),409
            # Repeated HTTP submissions reuse the same batch, including failed rows.
            batch_id=saved['id']
            result=jobs.submit_batch(shares,target,local_id=batch_id)
            return jsonify(**result,batch_id=batch_id,message='已提交本地 Excel 批次。完成后尝试自动下载，也可从本地批次列表下载；不写入腾讯文档。'),202
        if target.get('mode')=='unconfigured': raise ValueError('请先配置腾讯文档，或选择仅转存并导出 Excel。')
        digest=fingerprint(text,target)
        if data.get('action')=='preview':
            with tencent_client() as client: target,preview=inspect_target(target,client)
            session['batch_preview']=digest
            return jsonify(shares=shares,target=target,preview=preview,message=f'识别到 {len(shares)} 条不同链接，请核对后开始。')
        if not hmac.compare_digest(session.get('batch_preview',''),digest):
            return jsonify(message='分享内容或目标设置已变化，请先重新预览批量任务。'),409
        result=jobs.submit_batch(shares,target)
        return jsonify(**result,message=f'新增 {result["created"]} 个任务，复用 {result["existing"]} 个已有任务。将按顺序处理。'),202
    except BatchValidationError as error: return jsonify(message=str(error),errors=error.errors),400
    except ValueError as error: return jsonify(message=str(error)),400
    except Exception: return jsonify(message='无法预览或提交任务，请检查腾讯文档连接后重试。'),502
