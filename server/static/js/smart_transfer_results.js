(async function () {
    const summary = document.getElementById('smartResultSummary');
    const body = document.getElementById('smartResultRows');
    let results;
    try { results = JSON.parse(sessionStorage.getItem('smartTransferResults') || '[]'); }
    catch (_) { results = []; }
    if (!Array.isArray(results) || !results.length) {
        summary.textContent = '本标签页暂无转存结果，请返回我的资源查看已保存的分享链接。';
        return;
    }
    const success = results.filter(item => item.success).length;
    summary.textContent = `本次处理 ${results.length} 条：成功 ${success}，失败 ${results.length - success}。Excel 导出包含所有已保存的分享。失败项可返回智能识别重新提交。`;
    for (const item of results) {
        const tr = document.createElement('tr');
        for (const value of [item.name, item.cloud, item.success ? '转存成功' : '失败']) {
            const td = document.createElement('td'); td.textContent = value; tr.append(td);
        }
        const detail = document.createElement('td'); tr.append(detail); body.append(tr);
        detail.textContent = item.success ? '读取分享链接…' : item.error;
        if (!item.success) continue;
        try {
            const response = await fetch(`/admin/api/resources/${Number(item.id)}`);
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.message || '读取失败');
            const url = new URL(data.data.share_link);
            if (!['http:', 'https:'].includes(url.protocol)) throw new Error('链接格式异常');
            const a = document.createElement('a'); a.href = url.href;
            a.textContent = data.data.share_link; a.target = '_blank'; a.rel = 'noopener noreferrer';
            detail.replaceChildren(a);
        } catch (_) { detail.textContent = '资源已保存，分享链接读取失败，请返回我的资源查看。'; }
    }
})();
