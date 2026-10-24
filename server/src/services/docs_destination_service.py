"""Read-only Tencent discovery and append preview. Never writes spreadsheet cells."""
import os
from pathlib import Path
from contextlib import contextmanager
from docs_sync.mcp_client import Client,tool_data
from docs_sync.target_config import parse_document_url,normalize_target
from docs_sync.sheet_sink import SheetSink

@contextmanager
def tencent_client():
    client=Client(Path(os.environ.get('DOCS_TOKEN_FILE','/run/secrets/tencent-token')).read_text().strip())
    try:
        client.initialize()
        yield client
    finally: client.session.close()

def inspect_document(url,client):
    file_id=parse_document_url(url)
    info=tool_data(client.call('manage.query_file_info',{'file_id':file_id}))
    if info.get('type')!='sheet' or info.get('file_id')!=file_id:
        raise ValueError('请选择腾讯在线表格；普通文档和智能表格暂不支持。')
    data=tool_data(client.call('sheet.get_sheet_info',{'file_id':file_id}))
    sheets=[s for s in data.get('sheets',[]) if s.get('sheet_type')=='worksheet' and not s.get('hidden')]
    if not sheets: raise ValueError('此文档没有可用的普通工作表。')
    return {'file_id':file_id,'url':url.strip().split('?')[0],'title':info.get('title') or '腾讯文档','sheets':sheets}

def inspect_target(target,client):
    target=normalize_target(target)
    if target.get('mode')=='unconfigured': raise ValueError('请先填写您自己的腾讯文档链接、读取工作表并保存设置。')
    info=inspect_document(target['url'],client)
    sheet=next((s for s in info['sheets'] if s['sheet_id']==target['sheet_id']),None)
    if not sheet: raise ValueError('工作表不存在或不可用，请重新选择。')
    target.update(title=info['title'],sheet_name=sheet.get('sheet_name','工作表'))
    preview=SheetSink(client,target['file_id'],target['sheet_id'],target['start_cell']).preview()
    return target,preview
