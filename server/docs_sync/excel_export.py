"""Read-only export of unsent deliveries; no cloud requests or state changes."""
import io
import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape,quoteattr

def clean(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))

def export_pending(path,target):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("SELECT name,share_link FROM deliveries WHERE target=? AND status IN ('pending','failed','uncertain') ORDER BY id",(target,)).fetchall()
    return build_workbook(rows)

def build_workbook(rows):
    rows=list(rows)
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    row_xml=[]; links=[]; relationships=[]
    for index,(name,link) in enumerate([('文件名','分享链接')]+rows,1):
        cells=''.join(f'<c r="{col}{index}" t="inlineStr" s="{1 if col=="B" and index>1 else 0}"><is><t xml:space="preserve">{escape(clean(value))}</t></is></c>' for col,value in [('A',name),('B',link)])
        row_xml.append(f'<row r="{index}">{cells}</row>')
        if index>1:
            url=link.strip().split()[0]; parsed=urlsplit(url)
            if parsed.scheme=='https' and parsed.hostname in {'pan.baidu.com','pan.quark.cn'} and not parsed.username and not parsed.password:
                links.append(f'<hyperlink ref="B{index}" r:id="rId{index}"/>')
                relationships.append(f'<Relationship Id="rId{index}" Type="{rel}/hyperlink" Target={quoteattr(url)} TargetMode="External"/>')
    files={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="未同步记录" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{rel}/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><sz val="11"/><name val="Calibri"/><color rgb="FF2563EB"/><u/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf xfId="0"/><xf fontId="1" applyFont="1" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{ns}" xmlns:r="{rel}"><cols><col min="1" max="1" width="65" customWidth="1"/><col min="2" max="2" width="70" customWidth="1"/></cols><sheetData>{"".join(row_xml)}</sheetData>'+ (f'<hyperlinks>{"".join(links)}</hyperlinks>' if links else '')+'</worksheet>',
        'xl/worksheets/_rels/sheet1.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(relationships)+'</Relationships>'
    }
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items(): archive.writestr(name,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+data)
    return output.getvalue(),len(rows)
