/* Fetch attachment bytes without navigating the page into a pending download. */
(function () {
    const button=document.getElementById('personalExcelExportBtn');
    const status=document.getElementById('personalExcelExportStatus');
    const download=document.getElementById('personalExcelDownload');
    let busy=false, fileUrl=null;
    async function exportPersonalExcel() {
        if (busy) return;
        busy=true;button.disabled=true;
        status.textContent='正在生成 Excel…';
        download.hidden=true;
        const controller=new AbortController();
        const timeout=setTimeout(()=>controller.abort(),30000);
        try {
            const response=await fetch('/admin/api/resources/export.xlsx',{
                credentials:'same-origin',cache:'no-store',signal:controller.signal,
                headers:{'Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
            });
            if (response.status===401) throw new Error('登录已过期，请重新登录后导出');
            if (!response.ok) {
                let message='导出失败，请稍后重试';
                try {const data=await response.json();message=data.message||message;} catch (_) {}
                throw new Error(message);
            }
            if (!(response.headers.get('Content-Type')||'').includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
                throw new Error('后台未返回有效 Excel 文件，请重新登录后重试');
            }
            const blob=await response.blob();
            if (!blob.size) throw new Error('导出文件为空，请重试');
            if (fileUrl) URL.revokeObjectURL(fileUrl);
            fileUrl=URL.createObjectURL(blob);
            download.href=fileUrl;download.hidden=false;
            // Keep the URL alive for manual retries; creating another export replaces it.
            download.click();
            status.textContent='Excel 已生成并提交下载；未收到文件可点击“再次下载 Excel”。';
        } catch(error) {
            status.textContent=error.name==='AbortError'?'导出超过30秒，请点击导出重试。':error.message;
            if (fileUrl) download.hidden=false;
        } finally {
            clearTimeout(timeout);busy=false;button.disabled=false;
        }
    }
    button.addEventListener('click',exportPersonalExcel);
    window.addEventListener('pagehide',()=>{if(fileUrl) URL.revokeObjectURL(fileUrl);});
})();
