"""Bound traversal while preserving empty cloud directories."""
from pathlib import PurePosixPath
import json

def safe_name(value):
    if not isinstance(value,str) or not value or value in ('.','..') or '/' in value or '\\' in value or '\0' in value:
        raise ValueError('资源文件名无法安全传输')
    return value

def scan(row, backend, qc, max_files=10000, max_nodes=20000, max_depth=64):
    files=[]; directories=[]; visited=set()
    def enter(source, depth):
        if depth>max_depth or source in visited or len(visited)>=max_nodes:
            raise ValueError('资源目录过深、重复或数量超过扫描限制')
        visited.add(source)
    def add(entry):
        if len(files)>=max_files:
            raise ValueError('文件数量超过单任务 10000 个限制')
        files.append(entry)
    def baidu(path, relative, depth=0):
        enter(path,depth)
        obj=backend.stat(path)
        if not obj:raise ValueError('源文件已不存在')
        if obj['is_dir']:
            directories.append(relative)
            for child in backend.list(path):baidu(child['path'],relative+'/'+safe_name(child['name']),depth+1)
        else:add(dict(source=path,relative=relative,size=int(obj['size'])))
    def quark(fid, relative, depth=0, metadata=None):
        enter(fid,depth)
        if metadata is not None and metadata.get('file_type')!=0:
            add(dict(source=fid,relative=relative,size=int(metadata['size']),quark_name=metadata['file_name']))
            return
        is_dir=metadata is not None
        page=1;found=False
        while True:
            batch=qc.get_dir_file(fid,page=page,size=100)
            if batch and not found:
                directories.append(relative);found=True
            for child in batch:
                quark(str(child['fid']),relative+'/'+safe_name(child['file_name']),depth+1,child)
            if len(batch)<100:break
            page+=1
        if found:return
        if is_dir:
            directories.append(relative);return
        data=qc._request('POST','https://drive-pc.quark.cn/1/clouddrive/file/download',payload={'fids':[fid]},params={'pr':'ucpro','fr':'pc'})
        info=(data or {}).get('data') or []
        if not info:raise ValueError('源目录为空或无法读取原文件')
        original=safe_name(info[0].get('file_name') or relative)
        add(dict(source=fid,relative=original,size=int(info[0]['size']),quark_name=original))
    if row['cloud_name']=='百度网盘':
        path=row['file_id'];baidu(path,safe_name(PurePosixPath(path).name))
    else:
        ids=json.loads(row['file_id']) if row['file_id'].startswith('[') else [row['file_id']]
        for index,fid in enumerate(ids):quark(str(fid),safe_name(row['name']) if len(ids)==1 else '资源-'+str(index+1))
    if not files:raise ValueError('没有可传输的文件')
    return files,directories
