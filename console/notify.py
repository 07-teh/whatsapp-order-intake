"""Called by n8n after the nightly run: post tonight's zip and summary to the console.
Comments in this file are in Chinese.
"""
import json
import os
import sys
import urllib.request

# =============================================
# 每晚 n8n 跑完 DeepSeek 后调用：把今晚的 zip 和摘要发到总控台的"订单助手"
#   python3 console/notify.py orders
# =============================================

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))


def main():
    bot = sys.argv[1] if len(sys.argv) > 1 else 'orders'
    with open(os.path.join(ROOT, 'output', 'ds', 'state.json'), encoding='utf-8') as f:
        state = json.load(f)
    zpath = os.path.join(ROOT, state['last_zip'])
    night = os.path.basename(zpath).split('_')[-1].split('.')[0]
    txt = os.path.join(ROOT, 'output', 'ds', night, 'orders_ds.txt')
    with open(txt, encoding='utf-8') as f:
        lines = f.read().split('\n')
    head = [l for l in lines[:4] if l.strip()]
    with open(os.path.join(ROOT, 'output', 'ds', night, 'raw.json'), encoding='utf-8') as f:
        raw = json.load(f)
    held = len(raw['held']) + len(raw.get('manual', []))
    text = (f'今晚 {night[4:6]}/{night[6:]} 的 DeepSeek 草稿好了（影子模式，只对比，不要录入）\n'
            + '\n'.join(head[1:]) + f'\n要人确认的 {held} 条在 txt 最前面')
    body = json.dumps({'text': text, 'files': [zpath]}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{os.environ.get("CONSOLE_PORT", "5680")}/api/bots/{bot}/messages', data=body,
                                 headers={'Content-Type': 'application/json', 'X-Console': '1'})
    with urllib.request.urlopen(req, timeout=30) as r:
        print('已发到总控台', r.status)


if __name__ == '__main__':
    main()
