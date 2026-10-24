"""Append only to the dedicated two-column sheet; verify every write."""
from urllib.parse import urlsplit
try:
    from .mcp_client import tool_data, Uncertain
    from .target_config import parse_cell, cell_name
except ImportError:
    from mcp_client import tool_data, Uncertain
    from target_config import parse_cell, cell_name

def link_key(value):
    url=urlsplit(str(value).strip().split()[0] if str(value).strip() else '')
    if url.hostname in {'pan.baidu.com','pan.quark.cn'} and url.path.startswith('/s/'):
        return url.hostname+url.path.rstrip('/')
    return str(value).strip()

def cell_value(cell):
    if cell.get('formula'): return cell['formula']
    kind=cell.get('value_type')
    if kind in ('STRING','RICH_STRING','TIME_STRING'): return cell.get('string_value','')
    if kind=='NUMBER': return str(cell.get('number_value',0))
    if kind=='BOOL': return str(cell.get('bool_value',False))
    return cell.get('string_value','') or ('#ERROR' if kind=='ERROR' else '')

class SheetSink:
    def __init__(self,client,file_id,sheet_id,start_cell='A2'):
        self.client=client; self.file_id=file_id; self.sheet_id=sheet_id
        self.start_row,self.start_col=parse_cell(start_cell)

    def call(self,name,**args):
        return tool_data(self.client.call(name,dict(file_id=self.file_id,sheet_id=self.sheet_id,**args)))

    def snapshot(self):
        info=tool_data(self.client.call('sheet.get_sheet_info',{'file_id':self.file_id}))
        sheet=next((s for s in info.get('sheets',[]) if s.get('sheet_id')==self.sheet_id),None)
        if not sheet: raise ValueError('target_sheet_missing')
        rows=int(sheet.get('row_count',0)); cols=int(sheet.get('col_count',0))
        if not 1<=rows<=20000 or not 2<=cols<=100:
            raise ValueError('sheet_size_out_of_bounds')
        if self.start_row>=rows or self.start_col+1>=cols:
            raise ValueError('起始位置超出工作表现有行列，请先在腾讯文档中扩展表格。')
        merged=self.call('sheet.get_merged_cells',start_row=self.start_row,end_row=rows-1,start_col=self.start_col,end_col=self.start_col+1)
        if not isinstance(merged.get('merged_cells'),list): raise Uncertain('invalid_merge_response')
        if merged['merged_cells']: raise ValueError('目标两列的写入区域含合并单元格，请改选不含合并单元格的区域。')
        cells={}
        for start in range(self.start_row,rows,200):
            result=self.call('sheet.get_cell_data',start_row=start,end_row=min(rows-1,start+199),
                             start_col=self.start_col,end_col=self.start_col+1,return_csv=False)
            if not isinstance(result.get('cells'),list): raise Uncertain('invalid_response')
            for cell in result['cells']:
                value=cell_value(cell)
                if value!='': cells[int(cell['row']),int(cell['col'])]=value
        return cells,rows

    def preview(self):
        cells,capacity=self.snapshot()
        row=max((r for r,c in cells),default=self.start_row-1)+1
        return {'next_cell':cell_name(row,self.start_col),'link_cell':cell_name(row,self.start_col+1),
                'start_cell':cell_name(self.start_row,self.start_col),'row':row,'capacity':capacity}

    def find(self,link):
        cells,_=self.snapshot()
        return next((r for (r,c),v in cells.items() if c==self.start_col+1 and link_key(v)==link_key(link)),None)

    def ensure_link(self,row,link):
        url=str(link).strip().split()[0]
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in {'pan.baidu.com','pan.quark.cn'} or parsed.username or parsed.password:
            raise ValueError('invalid_share_hyperlink')
        self.call('sheet.set_link',row=row,col=self.start_col+1,url=url,display_text=link)

    def append(self,name,link):
        cells,capacity=self.snapshot()
        existing=next((r for (r,c),v in cells.items() if c==self.start_col+1 and link_key(v)==link_key(link)),None)
        if existing is not None:
            self.ensure_link(existing,cells[existing,self.start_col+1])
            return existing
        row=max((r for r,c in cells),default=self.start_row-1)+1
        if row>=20000: raise ValueError('写入区域已达到 20000 行上限，请更换工作表。')
        if row>=capacity:
            self.call('sheet.insert_dimension',dimension_type='row',index=capacity-1,count=200,direction='after')
        # Check the selected row immediately before mutation; users should not edit
        # the last row concurrently because this API has no compare-and-set.
        current=self.call('sheet.get_cell_data',start_row=row,end_row=row,start_col=self.start_col,end_col=self.start_col+1,return_csv=False)
        if not isinstance(current.get('cells'),list): raise Uncertain('invalid_response')
        if any(cell_value(c)!='' for c in current.get('cells',[])):
            raise ValueError('target_row_changed')
        self.call('sheet.set_range_value',values=[
            {'row':row,'col':self.start_col,'value_type':'STRING','string_value':name},
            {'row':row,'col':self.start_col+1,'value_type':'STRING','string_value':link}])
        self.ensure_link(row,link)
        check=self.call('sheet.get_cell_data',start_row=row,end_row=row,start_col=self.start_col,end_col=self.start_col+1,return_csv=False)
        values={(int(c['row']),int(c['col'])):cell_value(c) for c in check.get('cells',[])}
        if values.get((row,self.start_col))!=name or values.get((row,self.start_col+1))!=link:
            raise Uncertain('invalid_response')
        return row
