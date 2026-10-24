/* Local share-text parser: no network calls and no executable input. */
(function (root) {
    'use strict';
    const hosts = {
        'pan.baidu.com': '百度网盘', 'bdpan.com': '百度网盘', 'baiduyun.com': '百度网盘',
        'pan.quark.cn': '夸克网盘', 'alipan.com': '阿里云盘', 'aliyundrive.com': '阿里云盘',
        'drive.aliyun.com': '阿里云盘', 'drive.uc.cn': 'UC网盘', 'pan.uc.cn': 'UC网盘',
        'pan.xunlei.com': '迅雷网盘', 'guangyapan.com': '光鸭云盘', 'pan.wkbrowser.com': '悟空网盘'
    };
    function linkInfo(text) {
        try {
            const url = new URL(text.startsWith('http') ? text : 'https://' + text);
            const cloud = hosts[url.hostname.toLowerCase().replace(/^www\./, '')];
            if (!cloud || !/^https?:$/.test(url.protocol) || url.username || url.password || url.port) return null;
            const share = url.pathname.match(/^\/s\/([A-Za-z0-9_-]+)\/?$/);
            if (!share) return null;
            url.protocol = 'https:';
            const code = url.searchParams.get('pwd') || url.searchParams.get('password') || '';
            return {url: url.toString(), cloud, key: cloud + ':' + share[1], code};
        } catch (_) { return null; }
    }
    function cleanTitle(text) {
        const quoted=text.match(/(?:分享了|分享文件)\s*[「【“"]([^」】”"]+)[」】”"]/);
        if (quoted) return quoted[1].trim();
        return text.replace(/^\s*(?:通过.*?网盘分享的文件|我用.*?网盘分享了[「【]?|分享文件|文件名|标题|name)\s*[:：]?\s*/i, '')
            .replace(/^\s*(?:链接|分享链接|share_link)\s*[:：]?\s*/i, '')
            .replace(/[,，\t\s]+$/, '').replace(/^[「【“"]/, '').replace(/[」】”"]\s*$/, '').trim();
    }
    function codes(text) {
        return Array.from(text.matchAll(/(?:提取码|提取密码|访问码|密码|pwd|pass(?:word)?)\s*[:：=]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])/gi), m => m[1]);
    }
    function parse(content) {
        if (content.length > 200000) throw new Error('内容过长，请分批粘贴（最多20万字符）');
        const records = [], notices = [];
        let context = {}, last = [], sourceCount = 0;
        const lines = content.replace(/\r\n?/g, '\n').split('\n');
        for (const raw of lines) {
            const line = raw.trim();
            if (!line) { context = {}; last = []; continue; }
            const matches = Array.from(line.matchAll(/(?:https?:\/\/)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\/s\/[A-Za-z0-9_-]+(?:\?[^\s<>"'，,。；;）)\]】]*)?/g));
            const valid = matches.map(m => ({match:m, info:linkInfo(m[0])})).filter(x => x.info);
            if (matches.length>valid.length) notices.push('有链接不在当前支持范围内，已忽略，请单独核对');
            if (valid.length) {
                sourceCount += valid.length;
                const prefix = cleanTitle(line.slice(0, valid[0].match.index));
                const title = prefix || context.name || '';
                const foundCodes = codes(line);
                last = [];
                for (const {match, info} of valid) {
                    const record = {name:title, share_link:info.url, cloud_name:info.cloud,
                        code:info.code, type:context.type || '', remarks:context.remarks || '',
                        key:info.key, issues:[], duplicates:0};
                    if (!title) record.issues.push('未识别到标题，请填写或确认默认名称');
                    if (record.code && !/^[A-Za-z0-9]{4,8}$/.test(record.code)) record.issues.push('链接内提取码格式异常，请核对');
                    const tail = line.slice(match.index + match[0].length);
                    if (valid.length === 1 && /^[,\t]/.test(tail)) {
                        const parts = tail.slice(1).split(/,|\t/);
                        record.type = parts[0]?.trim() || record.type;
                        record.remarks = parts.slice(1).join(' ').trim() || record.remarks;
                    }
                    if (valid.length === 1) {
                        for (const code of new Set(foundCodes)) assignCode(record, code);
                    }
                    if (context.code) {
                        if (valid.length===1) assignCode(record,context.code);
                        record.issues.push('提取码位于链接之前，归属请核对');
                    }
                    if (valid.length > 1 && foundCodes.length) record.issues.push('同一行有多个链接，提取码归属需确认');
                    records.push(record); last.push(record);
                }
                context = {name:title};
                continue;
            }
            const foundCodes = codes(line);
            if (foundCodes.length) {
                if (last.length === 1 && foundCodes.length === 1) assignCode(last[0],foundCodes[0]);
                else if (last.length) last.forEach(r => r.issues.push('提取码归属不明确，请手动填写'));
                else if (foundCodes.length===1) {context.code=foundCodes[0];}
                else { notices.push('有提取码未匹配到链接，请在预览中核对'); }
                continue;
            }
            const field = line.match(/^(类型|type|备注|remark|remarks|云盘|网盘|cloud_name)\s*[:：]\s*(.*)$/i);
            if (field) {
                const key = /^(类型|type)$/i.test(field[1]) ? 'type' : /^(备注|remark|remarks)$/i.test(field[1]) ? 'remarks' : null;
                if (key) { context[key]=field[2]; if (last.length===1) last[0][key]=field[2]; }
                continue;
            }
            const heading=/分享了\s*[「【“"]|^通过.*?网盘分享的文件|^(?:标题|name)\s*[:：]/i.test(line);
            if (!heading && /复制.*(?:打开|APP|获取)|打开.*(?:APP|网盘)|https?:\/\/|^链接\s*[:：]?$|^[-=]{3,}$/.test(line)) continue;
            const title = cleanTitle(line);
            if (title) { context={name:title}; last=[]; }
        }
        const unique = new Map();
        for (const record of records) {
            if (unique.has(record.key)) {
                const first=unique.get(record.key); first.duplicates++;
                if (record.code) assignCode(first,record.code);
                if (!first.name && record.name) { first.name=record.name; first.issues=first.issues.filter(i=>!i.startsWith('未识别到标题')); }
                for (const issue of record.issues) if (!issue.startsWith('未识别到标题')) first.issues.push(issue);
            } else unique.set(record.key,record);
        }
        const result=Array.from(unique.values());
        if (result.length>500) throw new Error('一次最多识别500条，请分批粘贴');
        result.forEach((r,i)=> { r.name=r.name || '待命名资源 '+(i+1); r.issues=Array.from(new Set(r.issues)); });
        if (!result.length) notices.push('未找到支持的网盘分享链接，目前优先支持百度、夸克等 /s/ 分享链接');
        return {records:result, sourceCount, duplicateCount:records.length-result.length, notices:Array.from(new Set(notices))};
    }
    function assignCode(record,code) {
        if (record.code && record.code!==code) { record.issues.push('出现不同提取码，请核对'); return; }
        record.code=code;
        const url=new URL(record.share_link);
        url.searchParams.delete('password');
        url.searchParams.set('pwd',code);
        record.share_link=url.toString();
    }
    function withCode(link,code) {
        const info=linkInfo(link);
        if (!info) throw new Error('请填写支持的网盘分享链接');
        if (code && !/^[A-Za-z0-9]{4,8}$/.test(code)) throw new Error('提取码应为4至8位字母或数字');
        const url=new URL(info.url);
        url.searchParams.delete('pwd'); url.searchParams.delete('password');
        if (code) url.searchParams.set('pwd',code);
        return url.toString();
    }
    const api={parse,linkInfo,withCode};
    if (typeof module!=='undefined' && module.exports) module.exports=api;
    else root.ShareTextParser=api;
})(typeof globalThis!=='undefined'?globalThis:this);
