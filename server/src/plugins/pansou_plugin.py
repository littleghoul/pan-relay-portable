import os
import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.plugins.http_plugin import HttpPlugin, PluginRequestError, clean_text


class PansouPlugin(HttpPlugin):
    name = "pansou"
    display_name = "NAS PanSou 搜索"
    description = "使用 NAS 已部署的 PanSou 搜索百度和夸克资源"
    version = "1.0.0"
    author = "pan-relay"
    priority = 10
    publish_by_default = False
    timeout = 35.0
    max_response_bytes = 2 * 1024 * 1024
    retries = 0

    def __init__(self):
        super().__init__()
        self.base_url = os.environ.get("PANSOU_URL", "").rstrip("/")
        self.is_enabled = bool(self.base_url)

    def search(self, keyword):
        keyword = clean_text(keyword)
        if not keyword or not self.base_url:
            return []
        from src.services.system_config_service import get_search_scheduler_config
        timeout = float(get_search_scheduler_config()['plugin']['timeout'])
        payload = self.read_json("POST", "/api/search", timeout=timeout, json={
            "kw": keyword, "res": "merge", "cloud_types": ["baidu", "quark"],
        })
        return self.parse_results(payload, keyword)

    def read_json(self, method, path, *, timeout=5, **kwargs):
        deadline = time.monotonic() + timeout
        response = self.request(method, self.base_url + path, stream=True,
                                timeout=(min(3, timeout), timeout), **kwargs)
        try:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > self.max_response_bytes:
                raise PluginRequestError('PanSou 响应超过大小限制')
            body = bytearray()
            for block in response.iter_content(chunk_size=8192):
                if time.monotonic() > deadline:
                    raise PluginRequestError('PanSou 响应超时')
                if len(body) + len(block) > self.max_response_bytes:
                    raise PluginRequestError('PanSou 响应超过大小限制')
                body.extend(block)
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise PluginRequestError("PanSou 返回的内容不是 JSON") from error
        finally:
            response.close()

    def parse_results(self, payload, keyword):
        if not isinstance(payload, dict):
            raise PluginRequestError("PanSou 响应格式错误")
        if "code" in payload:
            if payload["code"] != 0:
                raise PluginRequestError("PanSou 搜索失败")
            payload = payload.get("data")
        if not isinstance(payload, dict) or not isinstance(payload.get("merged_by_type"), dict):
            raise PluginRequestError("PanSou 搜索结果格式错误")
        items = []
        for cloud in ("baidu", "quark"):
            records = payload["merged_by_type"].get(cloud, [])
            if not isinstance(records, list):
                continue
            for record in records[:500]:
                if not isinstance(record, dict):
                    continue
                item = self.make_item(
                    record.get("note") or record.get("title") or keyword,
                    record.get("url"), password=record.get("password"),
                    datetime_value=record.get("datetime"),
                )
                if item and item.password:
                    parts = urlsplit(item.share_link)
                    query = parse_qsl(parts.query, keep_blank_values=True)
                    if not any(key.lower() in ("pwd", "password", "code") for key, _ in query):
                        query.append(("pwd", item.password))
                        item.share_link = urlunsplit(parts._replace(query=urlencode(query)))
                items.append(item)
        return self.finalize(items)

    def health_check(self):
        if not self.base_url:
            return False, "未配置 PANSOU_URL"
        try:
            payload = self.read_json("GET", "/api/health")
            return payload.get("status") == "ok", "PanSou 服务健康检查完成"
        except Exception:
            return False, "无法连接 NAS PanSou 服务"
