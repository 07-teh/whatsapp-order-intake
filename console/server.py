"""Local control console (http://localhost:5680), styled like a chat app: one "contact" per n8n
project. Bots post messages and files to it; files dropped into a chat are saved and forwarded
to that project's n8n upload webhook. Listens on 127.0.0.1 only. Comments in this file are in Chinese.
"""
import datetime
import http.server
import json
import os
import re
import socketserver
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid

# =============================================
# 总控台：像微信一样，左边每个 n8n 项目一个"联系人"，右边是对话。
#   python3 console/server.py            → http://localhost:5680
# 联系人在 console/bots.json 里加。机器人发消息：POST /api/bots/<id>/messages {"text": "...", "files": ["/绝对路径/x.zip"]}
# 你拖进对话框的文件：存到这个联系人的 upload_dir，再 POST {"path": ...} 给它的 upload_webhook（n8n），n8n 回的文字当回复显示。
# 只听 127.0.0.1。
# =============================================

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, 'data')
PORT = int(os.environ.get('CONSOLE_PORT', '5680'))
MAX_UPLOAD = 300 * 1024 * 1024
LOCK = threading.Lock()
STATUS_CACHE = {}


def bots():
    with open(os.path.join(HERE, 'bots.json'), encoding='utf-8') as f:
        return json.load(f)


def bot(bot_id):
    return next((b for b in bots() if b['id'] == bot_id), None)


def log_path(bot_id):
    return os.path.join(DATA, f'{bot_id}.json')


def messages(bot_id):
    try:
        with open(log_path(bot_id), encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add_message(bot_id, sender, text, files=()):
    msg = {'id': uuid.uuid4().hex[:12], 'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'from': sender, 'text': text, 'files': []}
    for path in files:
        path = os.path.realpath(path)
        if not path.startswith(ROOT + os.sep):
            msg['text'] += f'\n（附件不在项目文件夹里，不给：{os.path.basename(path)}）'
        elif os.path.isfile(path):
            msg['files'].append({'name': os.path.basename(path), 'path': path, 'size': os.path.getsize(path)})
        else:
            msg['text'] += f'\n（附件找不到：{os.path.basename(path)}）'
    with LOCK:
        os.makedirs(DATA, exist_ok=True)
        log = messages(bot_id)
        log.append(msg)
        tmp = log_path(bot_id) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
        os.replace(tmp, log_path(bot_id))
    return msg


def known_file(path):
    """只给对话里出现过的附件，别的路径一律不给。"""
    real = os.path.realpath(path)
    for b in bots():
        for m in messages(b['id']):
            if any(f['path'] == real for f in m['files']):
                return real
    return None


def status(b):
    """联系人头上那一行小字（例如 DeepSeek 余额），60 秒缓存。"""
    cmd = b.get('status_cmd')
    if not cmd:
        return ''
    hit = STATUS_CACHE.get(b['id'])
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        out = f'查询失败: {e}'
    STATUS_CACHE[b['id']] = (time.time(), out)
    return out


def forward_upload(bot_id, path):
    b = bot(bot_id)
    url = b.get('upload_webhook')
    if not url:
        add_message(bot_id, 'bot', '这个联系人还不收文件')
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({'path': path}).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=600) as r:
            reply = r.read().decode('utf-8', 'replace').strip()
    except Exception as e:      # n8n 没开、workflow 没启用都会到这里
        reply = f'处理失败：{e}\n（n8n 有没有开？）'
    add_message(bot_id, 'bot', reply or '收到了')


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 60            # 传到一半断掉的请求，60 秒后放掉线程

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip('/').split('/')
        if url.path in ('/', '/index.html'):
            with open(os.path.join(HERE, 'index.html'), 'rb') as f:
                return self.send(200, f.read(), 'text/html; charset=utf-8')
        if url.path.startswith('/static/'):
            name = os.path.basename(url.path)
            path = os.path.join(HERE, 'static', name)
            if os.path.isfile(path):
                ctype = 'image/png' if name.endswith('.png') else 'image/svg+xml' if name.endswith('.svg') else 'application/octet-stream'
                with open(path, 'rb') as f:
                    return self.send(200, f.read(), ctype)
            return self.send(404, {'error': 'not found'})
        if url.path == '/api/bots':
            out = []
            for b in bots():
                log = messages(b['id'])
                last = log[-1] if log else None
                out.append({k: b.get(k) for k in ('id', 'name', 'avatar', 'subtitle', 'links')} |
                           {'last': last and {'time': last['time'], 'text': last['text'][:60] or (last['files'][0]['name'] if last['files'] else '')},
                            'count': len(log)})
            return self.send(200, out)
        if len(parts) == 4 and parts[:2] == ['api', 'bots'] and parts[3] == 'messages':
            if not bot(parts[2]):
                return self.send(404, {'error': 'no such bot'})
            log = [dict(m, files=[{'name': f['name'], 'size': f['size'], 'url': '/file?p=' + urllib.parse.quote(f['path'])} for f in m['files']])
                   for m in messages(parts[2])]
            return self.send(200, log)
        if len(parts) == 4 and parts[:2] == ['api', 'bots'] and parts[3] == 'status':
            b = bot(parts[2])
            return self.send(200, {'status': status(b) if b else ''})
        if url.path == '/file':
            path = known_file(urllib.parse.parse_qs(url.query).get('p', [''])[0])
            if not path or not os.path.isfile(path):
                return self.send(404, {'error': 'not found'})
            with open(path, 'rb') as f:
                return self.send(200, f.read(), 'application/octet-stream',
                                 {'Content-Disposition': "attachment; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(path))})
        return self.send(404, {'error': 'not found'})

    def do_POST(self):
        # 别的网页可以偷偷往 localhost 发 POST（CSRF）。要求自定义头（浏览器跨站会先预检，我们不答应就发不出来），再核对来源
        origin = self.headers.get('Origin')
        if self.headers.get('X-Console') != '1' or (origin and origin not in (f'http://localhost:{PORT}', f'http://127.0.0.1:{PORT}')):
            return self.send(403, {'error': 'forbidden'})
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip('/').split('/')
        if len(parts) != 4 or parts[:2] != ['api', 'bots'] or not bot(parts[2]):
            return self.send(404, {'error': 'not found'})
        bot_id, action = parts[2], parts[3]
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_UPLOAD:
            return self.send(413, {'error': '文件太大'})
        body = self.rfile.read(length)
        if action == 'messages':           # 机器人（n8n / 脚本）发消息
            try:
                data = json.loads(body or b'{}')
            except json.JSONDecodeError:
                return self.send(400, {'error': 'bad json'})
            return self.send(200, add_message(bot_id, data.get('from', 'bot'), str(data.get('text', '')), data.get('files') or []))
        if action == 'upload':             # 你拖进来的文件
            b = bot(bot_id)
            name = urllib.parse.parse_qs(url.query).get('name', ['upload'])[0]
            name = re.sub(r'[^\w.\-]+', '_', os.path.basename(name))[-80:] or 'upload'
            updir = os.path.join(ROOT, b.get('upload_dir', os.path.join('output', 'inbox')))
            os.makedirs(updir, exist_ok=True)
            path = os.path.join(updir, datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:4] + '_' + name)
            with open(path, 'wb') as f:
                f.write(body)
            add_message(bot_id, 'me', '', [path])
            threading.Thread(target=forward_upload, args=(bot_id, path), daemon=True).start()
            return self.send(200, {'ok': True})
        return self.send(404, {'error': 'not found'})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    print(f'总控台 http://localhost:{PORT}')
    Server(('127.0.0.1', PORT), Handler).serve_forever()
