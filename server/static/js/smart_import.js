let smartImportState = null;
// Retain completed share identities across preview refreshes for this page session.
const smartCompletedShares = new Set();

function previewSmartResources() {
    if (isBatchSavingInProgress) return;
    const source=document.getElementById('batchResourceContent').value.trim();
    const panel=document.getElementById('smartImportPreview');
    const body=document.getElementById('smartImportRows');
    const summary=document.getElementById('smartImportSummary');
    body.replaceChildren(); panel.hidden=false;
    try {
        const parsed=ShareTextParser.parse(source);
        smartImportState={source,rows:[]};
        summary.textContent=`识别 ${parsed.records.length} 条，合并重复 ${parsed.duplicateCount} 条。` + parsed.notices.join('；') + ' 黄色提示项请核对后勾选。';
        parsed.records.forEach(record=> {
            const row=document.createElement('tr');
            const done=smartCompletedShares.has(record.key);
            const select=document.createElement('input'); select.type='checkbox'; select.checked=!done && !record.issues.length; select.disabled=done;
            select.setAttribute('aria-label','确认导入此资源');
            const fields={};
            function cell(node) {const td=document.createElement('td'); td.style.padding='8px'; td.append(node); row.append(td);}
            cell(select);
            for (const [key,label] of [['name','标题'],['share_link','分享链接'],['code','提取码']]) {
                const input=document.createElement('input'); input.type='text'; input.value=record[key]||'';
                input.className='form-control form-control-sm'; input.setAttribute('aria-label',label);
                input.style.minWidth=key==='share_link'?'220px':key==='name'?'140px':'65px';
                fields[key]=input; cell(input);
            }
            const cloud=document.createElement('span'); cloud.textContent=record.cloud_name; cell(cloud);
            const status=document.createElement('span');
            status.textContent=record.issues.join('；') || (record.duplicates?`已合并 ${record.duplicates} 个重复链接`:'待处理');
            if (record.issues.length) status.style.color='#b45309';
            if (done) {status.textContent='已成功处理，本次页面会话不重复提交';status.style.color='#15803d';}
            cell(status); body.append(row);
            const state={record,select,fields,status,done,codeEdited:false};
            fields.code.addEventListener('input',()=>{state.codeEdited=true;});
            smartImportState.rows.push(state);
        });
        toggleBatchTransferOptions(document.getElementById('batchResourceTransferToggle').checked);
    } catch(error) {smartImportState=null; summary.textContent=error.message;}
}

function invalidateSmartPreview() {
    if (isBatchSavingInProgress) return;
    smartImportState=null;
    document.getElementById('smartImportPreview').hidden=true;
    toggleBatchTransferOptions(document.getElementById('batchResourceTransferToggle').checked);
}

async function saveSmartResources() {
    if (isBatchSavingInProgress) return;
    const source=document.getElementById('batchResourceContent').value.trim();
    if (!smartImportState || smartImportState.source!==source) {previewSmartResources(); return;}
    const selected=smartImportState.rows.filter(row=>row.select.checked && !row.done);
    if (!selected.length) {showToast('请核对预览并勾选要处理的资源','warning'); return;}
    const jobs=[];
    try {
        for (const row of selected) {
            const name=row.fields.name.value.trim();
            if (!name) throw new Error('标题不能为空');
            const editedLink=row.fields.share_link.value.trim();
            const info=ShareTextParser.linkInfo(editedLink);
            if (!info) throw new Error('请填写支持的网盘分享链接');
            if (smartCompletedShares.has(info.key)) {
                row.done=true; row.select.checked=false; row.select.disabled=true;
                row.status.textContent='已成功处理，不重复提交';
                continue;
            }
            let code=row.fields.code.value.trim();
            // A newly pasted URL supplies its own code unless the code field was explicitly edited.
            if (!row.codeEdited && code===(row.record.code || '') && editedLink!==row.record.share_link) {
                code=info.code;
                row.fields.code.value=code;
            }
            const link=ShareTextParser.withCode(editedLink,code);
            jobs.push({row,payload:{name,share_link:link,cloud_name:ShareTextParser.linkInfo(link).cloud,
                type:row.record.type || document.getElementById('batchResourceType').value,
                remarks:row.record.remarks || document.getElementById('batchResourceRemarks').value.trim()}});
        }
    } catch(error) {showToast(error.message,'warning'); return;}
    if (!jobs.length) {showToast('所选资源已处理，无需重复提交','warning');return;}
    const transfer=document.getElementById('batchResourceTransferToggle').checked;
    let configured;
    const btn=document.getElementById('batchSaveResourceBtn');
    const controls=Array.from(document.querySelectorAll('#batchAddResourceForm input, #batchAddResourceForm textarea, #batchAddResourceForm select, #batchAddResourceForm button, #smartImportPreview input'));
    const disabledStates=controls.map(el=>el.disabled);
    isBatchSavingInProgress=true; controls.forEach(el=>el.disabled=true); btn.disabled=true;
    let success=0;
    const results=[];
    try {
        if (transfer) await loadNetdiskCredentials();
        configured=getBatchTransferNetdiskConfig();
        for (const [index,job] of jobs.entries()) {
            btn.textContent=`正在处理 ${index+1}/${jobs.length}`;
            job.row.status.textContent='处理中…';
            try {
                const cloud=netdiskCredentialsMap[job.payload.cloud_name];
                if (transfer && (!cloud || !configured[cloud.key])) throw new Error('该网盘账号未就绪，未执行转存；请配置账号后重试');
                job.payload.save_to_netdisk=transfer?configured:{};
                const response=await fetch('/admin/api/resources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(job.payload)});
                const data=await response.json();
                if (!response.ok || !data.success) throw new Error(data.message || '处理失败');
                job.row.status.textContent=transfer?'转存及创建分享成功':'已入库（未转存）';
                job.row.status.style.color='#15803d'; job.row.done=true; job.row.select.checked=false; success++;
                smartCompletedShares.add(ShareTextParser.linkInfo(job.payload.share_link).key);
                results.push({id:data.id,name:job.payload.name,cloud:job.payload.cloud_name,success:true});
            } catch(error) {
                job.row.status.textContent=error.message; job.row.status.style.color='#b91c1c';
                results.push({name:job.payload.name,cloud:job.payload.cloud_name,success:false,error:error.message});
            }
        }
        document.getElementById('smartImportSummary').textContent=`处理完成：成功 ${success}，失败 ${jobs.length-success}。失败项保留勾选，修正后可再次确认重试。`;
        showToast(`成功 ${success}，失败 ${jobs.length-success}`,success===jobs.length?'success':'warning');
        await loadResources();
        if (transfer) {
            try {
                sessionStorage.setItem('smartTransferResults',JSON.stringify(results));
                window.alert(`转存处理完成：成功 ${success}，失败 ${jobs.length-success}。确认后查看本次转存结果。`);
                window.AppUI.closeModal('batchAddResourceModal');
                window.location.assign('/admin/resources/transfer-results');
            } catch (error) {
                showToast('无法打开结果页面，本次结果仍保留在识别窗口中。','warning');
            }
        }
    } finally {
        isBatchSavingInProgress=false;
        controls.forEach((el,i)=>el.disabled=disabledStates[i]);
        smartImportState.rows.forEach(row=>{if(row.done)row.select.disabled=true;});
        btn.disabled=false; toggleBatchTransferOptions(document.getElementById('batchResourceTransferToggle').checked);
    }
}

document.getElementById('batchResourceContent').addEventListener('input',invalidateSmartPreview);
