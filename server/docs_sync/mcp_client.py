"""Minimal Tencent Streamable-HTTP MCP transport, with no sensitive logging."""
import json
import requests

ENDPOINT = 'https://docs.qq.com/openapi/mcp'

class Rejected(Exception): pass
class RateLimited(Rejected): pass
class Uncertain(Exception): pass

def decode_response(text, request_id):
    try:
        messages = [json.loads(text)]
    except ValueError:
        messages = []
        for block in text.replace('\r\n','\n').split('\n\n'):
            data = '\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
            if data:
                try: messages.append(json.loads(data))
                except ValueError: continue
    for message in messages:
        if not isinstance(message,dict) or message.get('id') != request_id:
            continue
        if 'error' in message:
            if str(message['error'].get('code'))=='400007':
                raise RateLimited('rate_limited')
            raise Rejected('rpc_rejected')
        if 'result' in message:
            return message['result']
    raise Uncertain('invalid_response')

def check_tool_result(result):
    if not isinstance(result,dict):
        raise Uncertain('invalid_response')
    if result.get('isError'):
        raise Rejected('remote_rejected')
    return result

def tool_data(result):
    check_tool_result(result)
    data = result.get('structuredContent')
    if data is None:
        for item in result.get('content',[]):
            if item.get('type') == 'text':
                try: data = json.loads(item['text'])
                except (ValueError,KeyError): continue
                break
    if not isinstance(data,dict):
        raise Uncertain('invalid_response')
    if data.get('error'):
        raise Rejected('remote_rejected')
    return data

class Client:
    def __init__(self, token):
        if not token or '\n' in token or '\r' in token:
            raise ValueError('missing_token')
        self.session = requests.Session()
        self.session.headers.update({'Authorization':token, 'Accept':'application/json, text/event-stream'})
        self.counter = 0

    def request(self, method, params=None, notification=False):
        self.counter += 1
        body = {'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notification: body['id'] = self.counter
        try:
            response = self.session.post(ENDPOINT,json=body,timeout=(10,90),allow_redirects=False)
        except requests.RequestException:
            raise Uncertain('response_lost') from None
        if response.status_code==429:
            raise RateLimited('rate_limited')
        if response.status_code in (400,401,403,404,405,422):
            raise Rejected('authorization_rejected' if response.status_code in (401,403) else 'remote_rejected')
        if response.status_code not in (200,202):
            raise Uncertain('invalid_response')
        if sid := response.headers.get('Mcp-Session-Id'):
            self.session.headers['Mcp-Session-Id'] = sid
        if notification: return None
        return decode_response(response.content.decode('utf-8'),self.counter)

    def initialize(self):
        result=self.request('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'pan-relay-docs-sync','version':'1.0'}})
        self.session.headers['MCP-Protocol-Version']=result.get('protocolVersion','2024-11-05')
        self.request('notifications/initialized',notification=True)
        return result

    def tools(self):
        return self.request('tools/list')

    def call(self, name, arguments):
        return check_tool_result(self.request('tools/call',{'name':name,'arguments':arguments}))
