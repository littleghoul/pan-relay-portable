"""Short-lived QR sessions. Cloud cookies stay exclusively on the server."""
import base64
import io
import json
import re
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode, urlparse

import requests
import qrcode

from src.db.accounts import get_all_accounts, get_account_by_id
from src.services.qr_account_service import save_login, extra

NAMES = {"baidu": "百度网盘", "quark": "夸克网盘"}
SESSIONS = {}
LOCK = threading.RLock()


def parse_json(text):
    text = text.strip()
    if not text:
        return {}
    if not text.startswith('{'):
        match = re.fullmatch(r'[\w$]+\s*\((.*)\)\s*;?', text, re.S)
        if not match:
            raise ValueError('网盘登录响应格式异常')
        text = match.group(1)
    return json.loads(text)


class Provider:
    def __init__(self, cloud):
        self.cloud = cloud
        self.http = requests.Session()
        self.http.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Referer': 'https://pan.baidu.com/' if cloud == 'baidu' else 'https://pan.quark.cn/',
        })
        self.gid = str(uuid.uuid4()).upper()

    def get(self, url, params=None, timeout=15):
        response = self.http.get(url, params=params, timeout=(8, timeout))
        response.raise_for_status()
        return response

    def passport(self, path, params, timeout=15):
        args = dict(apiver='v3', tpl='netdisk', tt=int(time.time() * 1000), **params)
        return self.get('https://passport.baidu.com' + path, args, timeout)

    def cas(self, path, params=None):
        args = {'client_id': '532', 'v': '1.2', 'request_id': str(uuid.uuid4())}
        args.update(params or {})
        return self.get('https://uop.quark.cn/cas/ajax/' + path, args).json()

    def start(self):
        if self.cloud == 'baidu':
            data = parse_json(self.passport('/v2/api/getqrcode', {
                'lp': 'pc', 'qrloginfrom': 'pc', 'gid': self.gid,
            }).text)
            self.token = data.get('sign')
            if not self.token or data.get('errno') not in (0, None):
                raise ValueError('百度暂未返回二维码，请稍后重试')
            url = data.get('imgurl', '')
            if not url.startswith('https://'):
                url = 'https://' + url.lstrip('/')
            if urlparse(url).hostname != 'passport.baidu.com':
                raise ValueError('百度二维码地址异常')
            raw = self.get(url).content
            if not raw.startswith(b'\x89PNG'):
                raise ValueError('百度二维码图片异常')
        else:
            data = self.cas('getTokenForQrcodeLogin')
            self.token = data.get('data', {}).get('members', {}).get('token')
            if data.get('status') != 2000000 or not self.token:
                raise ValueError('夸克暂未返回二维码，请稍后重试')
            url = 'https://su.quark.cn/4_eMHBJ?' + urlencode({
                'token': self.token, 'client_id': '532', 'ssb': 'weblogin',
                'uc_param_str': '',
                'uc_biz_str': 'S:custom|OPT:SAREA@0|OPT:IMMERSIVE@1|OPT:BACK_BTN_STYLE@0',
            })
            output = io.BytesIO()
            qrcode.make(url).save(output, format='PNG')
            raw = output.getvalue()
        return 'data:image/png;base64,' + base64.b64encode(raw).decode()

    def poll(self):
        if self.cloud == 'quark':
            data = self.cas('getServiceTicketByQrcodeToken', {'token': self.token})
            ticket = data.get('data', {}).get('members', {}).get('service_ticket')
            if data.get('status') == 2000000 and ticket:
                self.get('https://pan.quark.cn/account/info', {'st': ticket, 'lw': 'scan'})
                self.get('https://pan.quark.cn/1/clouddrive/member', {'pr': 'ucpro', 'fr': 'pc'})
                cookie = self.cookies('quark.cn')
                if '__uid=' not in cookie or '__pus=' not in cookie:
                    raise ValueError('夸克未返回完整登录凭证，请重新扫码')
                return 'success', cookie
            if data.get('status') in (50004002, 50004003, 50004004):
                return 'expired', None
            return 'waiting', None
        data = parse_json(self.passport('/channel/unicast', {
            'channel_id': self.token, 'gid': self.gid, '_sdkFrom': '1',
        }, timeout=12).text)
        channel = data.get('channel_v', {})
        if isinstance(channel, str):
            channel = json.loads(channel)
        ticket = channel.get('v') if isinstance(channel, dict) else None
        if not ticket:
            return ('scanned' if channel.get('status') == 1 else 'waiting'), None
        self.passport('/v3/login/main/qrbdusslogin', {
            'bduss': ticket, 'u': 'https://pan.baidu.com/disk/main',
            'loginVersion': 'v5', 'qrcode': '1', 'time': int(time.time()),
        })
        for _ in range(3):
            self.get('https://passport.baidu.com/v3/login/api/auth/', {
                'return_type': '5', 'tpl': 'netdisk', 'u': 'https://pan.baidu.com/disk/main',
            })
            self.get('https://pan.baidu.com/disk/main')
            if any(c.name == 'STOKEN' and 'pan.baidu.com' in c.domain for c in self.http.cookies):
                break
            time.sleep(1)
        cookie = self.cookies('baidu.com')
        if 'BDUSS=' not in cookie or 'STOKEN=' not in cookie:
            raise ValueError('百度未返回完整网盘登录凭证，请重新扫码')
        return 'success', cookie

    def cookies(self, domain):
        values = {}
        # Prefer the pan-domain cookie over passport-domain cookies of the same name.
        cookies = sorted(self.http.cookies, key=lambda c: c.domain.lstrip('.').startswith('pan.'))
        for item in cookies:
            if item.domain.lstrip('.') == domain or item.domain.endswith('.' + domain):
                values[item.name] = item.value
        return '; '.join(f'{name}={value}' for name, value in values.items())

    def identity(self):
        if self.cloud == 'baidu':
            data = self.get('https://pan.baidu.com/api/gettemplatevariable', {'fields': '["uk"]'}).json()
            value = (data.get('result') or {}).get('uk') if data.get('errno') == 0 else None
        else:
            data = self.get('https://pan.quark.cn/account/info').json()
            value = None
            if data.get('success') is True or data.get('code') == 0:
                value = (data.get('data') or {}).get('uid')
                if not value:
                    # This endpoint omits uid on some versions; use the authenticated
                    # session's account identifier returned by the QR ticket exchange.
                    values = dict(part.strip().split('=', 1) for part in self.cookies('quark.cn').split(';') if '=' in part)
                    value = values.get('__uid')
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip() or str(value) == '0':
            raise ValueError('无法确认网盘账号身份')
        return str(value)

    def validate(self):
        """Require authenticated disk access, rather than trusting cookie names."""
        if self.cloud == 'baidu':
            quota = self.get('https://pan.baidu.com/api/quota', {'checkfree': 1}).json()
            listing = self.get('https://pan.baidu.com/api/list', {
                'dir': '/', 'num': 1, 'page': 1, 'order': 'name', 'web': 1,
            }).json()
            valid = (quota.get('errno') == 0 and int(quota.get('total') or 0) > 0
                     and listing.get('errno') == 0 and isinstance(listing.get('list'), list))
        else:
            member = self.get('https://drive.quark.cn/1/clouddrive/member', {'pr': 'ucpro', 'fr': 'pc'}).json()
            listing = self.get('https://drive-pc.quark.cn/1/clouddrive/file/sort', {
                'pr': 'ucpro', 'fr': 'pc', 'pdir_fid': '0', '_page': 1, '_size': 1,
            }).json()
            valid = (member.get('code') == 0 and member.get('status') == 200
                     and listing.get('code') == 0 and listing.get('status') == 200
                     and isinstance((listing.get('data') or {}).get('list'), list))
        if not valid:
            raise ValueError('网盘访问验证失败')


def cleanup():
    now = time.monotonic()
    for sid, record in list(SESSIONS.items()):
        if now > record['expires']:
            SESSIONS.pop(sid, None)
            record['cancel'].set()


def start_login(cloud, owner, account_id=None, confirm_legacy=False):
    if cloud not in NAMES:
        raise ValueError('仅支持百度和夸克扫码登录')
    if account_id is not None:
        if type(account_id) is not int:
            raise ValueError('账号编号无效')
        account = get_account_by_id(account_id)
        if not account or account['cloud_name'] != NAMES[cloud]:
            raise ValueError('原账号不存在或网盘类型不符')
    with LOCK:
        cleanup()
        if len(SESSIONS) >= 8:
            raise ValueError('扫码任务过多，请稍后重试')
        for old_id, old in list(SESSIONS.items()):
            if old['owner'] == owner and old['cloud'] == cloud:
                old['cancel'].set()
                SESSIONS.pop(old_id, None)
        sid = secrets.token_urlsafe(24)
        record = dict(owner=owner, cloud=cloud, account_id=account_id, confirm_legacy=confirm_legacy, expires=time.monotonic() + 300,
                      cancel=threading.Event(), status='starting', message='正在获取二维码')
        SESSIONS[sid] = record
    provider = Provider(cloud)
    try:
        image = provider.start()
    except Exception:
        with LOCK:
            SESSIONS.pop(sid, None)
        provider.http.close()
        raise
    record.update(status='waiting', message='请用网盘 App 扫码并确认登录')
    threading.Thread(target=wait_login, args=(provider, record), daemon=True).start()
    return {'session_id': sid, 'image': image, 'expires_in': 300}


def wait_login(provider, record):
    try:
        while time.monotonic() < record['expires'] and not record['cancel'].is_set():
            try:
                status, cookie = provider.poll()
                if status == 'success':
                    if record['cancel'].is_set() or time.monotonic() >= record['expires']:
                        return
                    record.update(status='verifying', message='正在验证网盘访问权限')
                    provider.validate()
                    identity = provider.identity()
                    legacy = {}
                    for account in get_all_accounts(NAMES[record['cloud']])[:16]:
                        if extra(account.get('extra_data')).get('provider_user_id'):
                            continue
                        old = Provider(record['cloud'])
                        try:
                            for part in account['credential'].split(';'):
                                if '=' in part:
                                    key, value = part.strip().split('=', 1)
                                    old.http.cookies.set(key, value, domain='.baidu.com' if record['cloud'] == 'baidu' else '.quark.cn')
                            legacy[account['id']] = {'identity': old.identity(), 'credential': account['credential']}
                        except Exception:
                            pass
                        finally:
                            old.http.close()
            except (requests.Timeout, requests.ConnectionError):
                record['cancel'].wait(2)
                continue
            with LOCK:
                if record['cancel'].is_set() or time.monotonic() >= record['expires']:
                    return
                if status == 'success':
                    account_id, refreshed = save_login(NAMES[record['cloud']], cookie, identity, legacy,
                                                      record.get('account_id'), record.get('confirm_legacy') is True)
                    if not account_id:
                        raise ValueError('登录成功，但保存账号失败，请重试')
                    record.update(status='success', message='登录成功，原账号凭证已更新，任务关联已保留' if refreshed else '登录成功，新账号已保存到 NAS')
                    if refreshed:
                        try:
                            from src.services.cross_jobs import resume_accounts
                            ids = {a['id'] for a in get_all_accounts(NAMES[record['cloud']]) if a.get('is_active') and extra(a.get('extra_data')).get('provider_user_id') == identity}
                            resumed = resume_accounts(ids)
                            if resumed:
                                record['message'] += f'；{resumed} 个登录失效任务已恢复到队列'
                        except Exception:
                            record['message'] += '；请到双盘任务手动重试'
                    return
                if status == 'expired':
                    record.update(status='expired', message='二维码已失效，请重新获取')
                    return
                record.update(status=status, message='已扫码，请在手机上确认' if status == 'scanned' else '等待手机扫码确认')
            record['cancel'].wait(2)
        record.update(status='expired', message='二维码已过期或已取消')
    except Exception as error:
        # Never return/log exception text from remote auth endpoints; it can include tokens.
        messages = {'扫码账号与原账号不一致', '原账号不存在', '请确认原账号身份后重新登录', '无法确认网盘账号身份'}
        record.update(status='error', message=str(error) if str(error) in messages else '网盘登录或凭证保存失败，请重新扫码')
    finally:
        provider.http.close()


def get_status(sid, owner, cancel=False):
    with LOCK:
        cleanup()
        record = SESSIONS.get(sid)
        if not record or record['owner'] != owner:
            return None
        if cancel:
            record['cancel'].set()
            record.update(status='cancelled', message='扫码已取消')
        return {key: record[key] for key in ('status', 'message')}
