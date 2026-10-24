"""Shared, credential-free destination validation."""
import base64
import re
from urllib.parse import urlsplit

DEFAULT_TARGET={'mode':'unconfigured','file_id':'','sheet_id':'','url':'','title':'尚未配置腾讯文档','sheet_name':'请选择工作表','start_cell':'A2'}

def parse_cell(value):
    match=re.fullmatch(r'([A-Z]{1,2})([1-9][0-9]{0,4})',str(value).strip().upper())
    if not match: raise ValueError('起始单元格格式应为 A2、C5 等。')
    col=0
    for ch in match[1]: col=col*26+ord(ch)-64
    row=int(match[2])
    if col>99 or row>20000: raise ValueError('支持 A 至 CU 列、1 至 20000 行，并预留右侧一列给链接。')
    return row-1,col-1

def cell_name(row,col):
    letters=''; col+=1
    while col: col,n=divmod(col-1,26); letters=chr(65+n)+letters
    return letters+str(row+1)

def parse_document_url(value):
    if not isinstance(value,str) or len(value)>1000: raise ValueError('请填写腾讯在线表格链接。')
    url=urlsplit(value.strip())
    if url.scheme!='https' or url.netloc!='docs.qq.com' or not re.fullmatch(r'/sheet/D[A-Za-z0-9_-]+',url.path):
        raise ValueError('请填写 https://docs.qq.com/sheet/ 开头的在线表格链接。')
    encoded=url.path.split('/')[-1][1:]
    try: file_id=base64.b64decode(encoded+'='*((-len(encoded))%4),altchars=b'-_',validate=True).decode('ascii')
    except Exception: raise ValueError('无法识别文档链接，请复制浏览器中的完整表格地址。') from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,100}',file_id): raise ValueError('无效的腾讯文档地址。')
    return file_id

def normalize_target(target):
    if not isinstance(target,dict): raise ValueError('请选择目标文档。')
    if target.get('mode')=='unconfigured': return dict(DEFAULT_TARGET)
    file_id=parse_document_url(target.get('url'))
    if target.get('file_id')!=file_id: raise ValueError('文档信息不匹配，请重新读取工作表。')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(target.get('sheet_id',''))): raise ValueError('请选择工作表。')
    row,col=parse_cell(target.get('start_cell','A2'))
    return {'file_id':file_id,'sheet_id':target['sheet_id'],'url':target['url'].strip().split('?')[0],
            'title':str(target.get('title','腾讯文档'))[:200],'sheet_name':str(target.get('sheet_name','工作表'))[:100],
            'start_cell':cell_name(row,col)}

def target_key(target):
    t=normalize_target(target)
    if t.get('mode')=='unconfigured': return 'unconfigured'
    return t['file_id']+':'+t['sheet_id']+(':'+t['start_cell'] if t['start_cell']!='A2' else '')
