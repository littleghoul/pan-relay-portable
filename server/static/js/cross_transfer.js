(function(){
    const start=document.getElementById('crossTransferStart');
    const status=document.getElementById('crossTransferStatus');
    const jobs=document.getElementById('crossTransferJobs');
    const filter=document.getElementById('crossTaskFilter');
    const labels={queued:'等待中',running:'传输中',failed:'失败',complete:'已完成'};
    let loading=false,submitting=false;
    async function api(path,body,options={}){
        const response=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...(body?{method:'POST',body:JSON.stringify(body)}:{}),...options});
        if(response.status===401)throw new Error('登录已过期，请重新登录');
        const result=await response.json();
        if(!response.ok||!result.success)throw new Error(result.message||'操作失败，请稍后重试');
        return result;
    }
    function share(parent,label,value){
        if(!value)return;
        try{
            const url=new URL(value);
            if(url.protocol!=='https:'||!['pan.baidu.com','pan.quark.cn'].includes(url.hostname))return;
            const a=document.createElement('a');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=label+'分享';
            a.style.marginRight='12px';parent.appendChild(a);
        }catch(_){}
    }
    async function refresh(){
        if(loading)return;loading=true;
        try{
            const data=await api('/admin/api/cross-transfer');
            if(start)start.disabled=!data.enabled||submitting;jobs.replaceChildren();
            let shown=0;
            for(const job of data.jobs){
                if(filter&&filter.value!=='all'&&job.status!==filter.value)continue;
                shown++;
                const line=document.createElement('div');line.style.cssText='padding:8px 0;border-bottom:1px solid #eee;overflow-wrap:anywhere';
                const text=document.createElement('strong');text.textContent=job.name+' · '+labels[job.status];line.appendChild(text);
                const detail=document.createElement('div');detail.textContent=job.source_cloud+' → '+(job.source_cloud==='百度网盘'?'夸克网盘':'百度网盘')+' | '+job.progress;line.appendChild(detail);
                const count=document.createElement('div');count.textContent='已完成 '+(job.result.done||0)+' / '+(job.result.total||'待扫描')+' 个文件'+(job.result.current_file?' | 当前文件：'+job.result.current_file:'');line.appendChild(count);
                const bar=document.createElement('progress');bar.max=job.result.total||1;bar.value=job.result.done||0;bar.style.width='100%';line.appendChild(bar);
                if(job.error){const error=document.createElement('p');error.style.color='#b91c1c';error.textContent=job.error;line.appendChild(error);}
                const id=document.createElement('small');id.textContent='任务编号：'+job.id;line.appendChild(id);line.appendChild(document.createElement('br'));
                share(line,'百度',job.result.baidu_share);share(line,'夸克',job.result.quark_share);
                if(job.status==='failed'){
                    const retry=document.createElement('button');retry.type='button';retry.className='btn btn-secondary btn-sm';retry.textContent='重试';
                    retry.addEventListener('click',async()=>{retry.disabled=true;try{await api('/admin/api/cross-transfer/'+encodeURIComponent(job.id)+'/retry',{});await refresh();}catch(e){status.textContent=e.message;retry.disabled=false;}});line.appendChild(retry);
                    const remove=document.createElement('button');remove.type='button';remove.className='btn btn-danger btn-sm';remove.textContent='删除';remove.style.marginLeft='8px';
                    remove.addEventListener('click',async()=>{
                        if(!window.confirm('删除这个失败任务记录？网盘文件和 NAS 缓存会保留；重新创建任务会使用新的任务编号。'))return;
                        remove.disabled=true;retry.disabled=true;
                        try{await api('/admin/api/cross-transfer/'+encodeURIComponent(job.id),undefined,{method:'DELETE'});await refresh();}
                        catch(e){status.textContent=e.message;remove.disabled=false;retry.disabled=false;}
                    });line.appendChild(remove);
                }
                jobs.appendChild(line);
            }
            if(!shown){const empty=document.createElement('p');empty.textContent='暂无符合条件的任务。请到“我的资源”勾选已转存资源，创建双盘分享任务。';jobs.appendChild(empty);}
        }catch(e){status.textContent=e.message;}finally{loading=false;}
    }
    if(start)start.addEventListener('click',async()=>{
        const ids=typeof selectedResourceMap==='undefined'?[]:Array.from(selectedResourceMap.keys()).map(Number);
        if(!ids.length){status.textContent='请先勾选要生成双盘分享的资源。';return;}
        if(submitting)return;submitting=true;start.disabled=true;
        try{await api('/admin/api/cross-transfer',{ids});status.textContent='已加入后台队列，可关闭页面；完成后导出 Excel。';}
        catch(e){status.textContent=e.message;}
        finally{submitting=false;await refresh();}
    });
    if(filter)filter.addEventListener('change',refresh);
    refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
