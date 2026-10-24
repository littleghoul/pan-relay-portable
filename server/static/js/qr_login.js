(() => {
    const dialog = document.getElementById('qrLoginDialog');
    const image = document.getElementById('qrLoginImage');
    const status = document.getElementById('qrLoginStatus');
    const refresh = document.getElementById('qrLoginRefresh');
    let cloud, sid, timer, accountId = null, generation = 0, saved = false;
    async function cancel() {
        clearTimeout(timer);
        const previous = sid;
        sid = null;
        if (previous) {
            try { await fetch(`/admin/api/qr-login/session/${previous}`, {method:'DELETE'}); } catch (_) {}
        }
    }
    async function start() {
        const current = ++generation;
        await cancel();
        saved = false;
        image.removeAttribute('src');
        image.style.display = 'none';
        status.textContent = '正在获取二维码…';
        refresh.disabled = true;
        try {
            const response = await fetch(`/admin/api/qr-login/${cloud}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({account_id:accountId,confirm_legacy:accountId !== null})});
            const data = await response.json();
            if (current !== generation || !dialog.open) {
                if (data.session_id) await fetch(`/admin/api/qr-login/session/${data.session_id}`, {method:'DELETE'});
                return;
            }
            if (!response.ok || !data.success) throw new Error(data.message || '获取二维码失败');
            sid = data.session_id;
            image.src = data.image;
            image.style.display = 'block';
            status.textContent = '请扫码，并在手机上确认登录（5 分钟内有效）';
            timer = setTimeout(() => poll(current), 2000);
        } catch (error) {
            if (current === generation) status.textContent = error.message;
        } finally {
            if (current === generation) refresh.disabled = false;
        }
    }
    async function poll(current) {
        if (current !== generation || !sid || !dialog.open) return;
        try {
            const response = await fetch(`/admin/api/qr-login/session/${sid}`, {cache:'no-store'});
            const data = await response.json();
            if (current !== generation || !dialog.open) return;
            if (!response.ok || !data.success) throw new Error(data.message || '登录状态查询失败');
            status.textContent = data.message;
            if (data.status === 'success') {
                saved = true;
                refresh.disabled = true;
                image.removeAttribute('src');
                image.style.display = 'none';
                document.getElementById('qrLoginClose').textContent = '完成并刷新账号列表';
            } else if (['error','expired','cancelled'].includes(data.status)) {
                image.style.display = 'none';
            } else {
                timer = setTimeout(() => poll(current), 2000);
            }
        } catch (error) {
            if (current === generation) status.textContent = error.message;
        }
    }
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-qr-cloud]');
        if (!button || dialog.open) return;
        accountId = button.dataset.qrAccount ? Number(button.dataset.qrAccount) : null;
        if (accountId !== null && !window.confirm('请用原网盘账号扫码。旧账号首次恢复可能需要确认身份；本操作将更新该账号凭证并保留原任务关联。确认使用原账号继续？')) return;
        cloud = button.dataset.qrCloud;
        document.getElementById('qrLoginTitle').textContent = `${cloud === 'baidu' ? '百度' : '夸克'}网盘扫码登录`;
        document.getElementById('qrLoginClose').textContent = '关闭';
        dialog.showModal();
        start();
    });
    refresh.addEventListener('click', start);
    document.getElementById('qrLoginClose').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        generation++;
        cancel();
        image.removeAttribute('src');
        if (saved) location.reload();
    });
})();
