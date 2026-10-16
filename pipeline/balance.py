"""Show the DeepSeek account balance (plain text, or HTML for the n8n dashboard).
Comments in this file are in Chinese.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deepseek  # noqa: E402

# =============================================
# 查 DeepSeek 账户余额
#   python3 pipeline/balance.py           # 一行文字
#   python3 pipeline/balance.py --html    # n8n 网页用
# =============================================


def fetch():
    req = urllib.request.Request(deepseek.BASE_URL + '/user/balance', headers={
        'Authorization': 'Bearer ' + os.environ.get('DEEPSEEK_API_KEY', ''), 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def text(info=None):
    info = info or fetch()
    parts = [f"{b['currency']} {b['total_balance']}（充值 {b['topped_up_balance']}，赠送 {b['granted_balance']}）"
             for b in info.get('balance_infos', [])]
    return 'DeepSeek 余额 ' + ('；'.join(parts) or '查不到') + ('' if info.get('is_available') else '  ⚠ 余额不足，调用会失败')


def html():
    info = fetch()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = ''.join(f"<tr><td>{b['currency']}</td><td class=big>{b['total_balance']}</td><td>{b['topped_up_balance']}</td><td>{b['granted_balance']}</td></tr>"
                   for b in info.get('balance_infos', []))
    warn = '' if info.get('is_available') else '<p class=warn>余额不足，今晚的 DeepSeek 会跑失败</p>'
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>DeepSeek 余额</title><meta http-equiv=refresh content=60>
<style>body{{font-family:-apple-system,sans-serif;margin:24px;background:#fff;color:#111}}table{{border-collapse:collapse}}
td,th{{padding:6px 14px;border-bottom:1px solid #ddd;text-align:left}}.big{{font-size:28px;font-weight:600}}.warn{{color:#b00}}
@media (prefers-color-scheme:dark){{body{{background:#1b1b1b;color:#eee}}td,th{{border-color:#444}}}}</style>
<h2>DeepSeek 余额</h2>{warn}<table><tr><th>币种</th><th>总余额</th><th>充值</th><th>赠送</th></tr>{rows}</table>
<p>查询时间 {now}（每 60 秒自动刷新）</p>"""


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--html', action='store_true')
    a = p.parse_args()
    print(html() if a.html else text())
