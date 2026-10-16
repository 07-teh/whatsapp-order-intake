"""Minimal DeepSeek client (OpenAI-compatible /chat/completions) with JSON mode and a disk cache.

Identical prompts are answered from data/ds_cache/, so replaying past nights costs nothing.
Retries transient errors; stops early on auth/balance errors. Key is read from .env.
Comments in this file are in Chinese.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

# =============================================
# 调 DeepSeek（OpenAI 兼容接口）。key 放在项目根目录的 .env（不进 git）：
#   DEEPSEEK_API_KEY=sk-...
#   DEEPSEEK_MODEL=deepseek-flash       （可不写）
#   DEEPSEEK_EFFORT=low                 （可不写）
# 同样的提示词只调一次，结果缓存在 data/ds_cache/，回放重跑不花钱。
# =============================================

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
CACHE_DIR = os.path.join(ROOT, 'data', 'ds_cache')


def load_env():
    path = os.path.join(ROOT, '.env')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
EFFORT = os.environ.get('DEEPSEEK_EFFORT', 'low')      # 思考深度 low / high / max；low 便宜约三成，回放结果一样
BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')


def chat_json(system, user, use_cache=True, images=()):
    """返回 (解析好的 JSON, 用量信息)。解析失败抛 ValueError。"""
    img_hash = [hashlib.sha256(data).hexdigest() for _, data in images]
    key = hashlib.sha256(json.dumps([MODEL, EFFORT, system, user] + img_hash, ensure_ascii=False).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key[:2], key + '.json')
    if use_cache and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            cached = json.load(f)
        return json.loads(cached['content']), dict(cached.get('usage', {}), cached=True)

    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise SystemExit('没有 DEEPSEEK_API_KEY：请在项目根目录的 .env 里写一行 DEEPSEEK_API_KEY=你的key')
    body = json.dumps({
        'model': MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user if not images else
                     [{'type': 'text', 'text': user}] + [{'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(data).decode()}}
                                                         for mime, data in images]}],
        'response_format': {'type': 'json_object'},
        'temperature': 0,
        'max_tokens': 16000,
        'reasoning_effort': EFFORT,
    }).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(BASE_URL + '/chat/completions', data=body, headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.load(r)
            content = resp['choices'][0]['message']['content']
            parsed = json.loads(content)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'content': content, 'usage': resp.get('usage', {}), 'model': resp.get('model')}, f, ensure_ascii=False)
            return parsed, resp.get('usage', {})
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}: {e.read()[:300]!r}'
            if e.code in (400, 401, 402, 403):
                break           # key 错、余额不足这类重试没用
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise ValueError(f'DeepSeek 调用失败: {last}')
