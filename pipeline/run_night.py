"""Nightly DeepSeek run (shadow mode): chat messages -> draft Sales Orders + a review list.

For each message in the batch window: skip chatter and same-day orders, build a prompt with
candidates from kb.py, ask DeepSeek for strict JSON, then run deterministic checks (customer
must be a candidate and active, branch exists, delivery date is in the future, item code and
UOM exist, piece orders are not entered as kg, description shape seen before, ...).
Anything that fails a check, or where the model asked a question, is held for a human.
Output: output/ds/<night>/orders_ds.json, orders_ds.txt (held items listed first), raw.json, and a zip.
Comments in this file are in Chinese.
"""
import argparse
import collections
import concurrent.futures
import datetime
import glob
import json
import math
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb as KBM  # noqa: E402
from kb import KB, shape  # noqa: E402
import deepseek  # noqa: E402
import attachments  # noqa: E402
import balance  # noqa: E402

# =============================================
# DeepSeek 版的每晚开单（影子模式：只出草稿给人对比，不进公司系统）
#   python3 pipeline/run_night.py --auto                       # n8n 每晚跑：从上次结束的地方到现在
#   python3 pipeline/run_night.py --night 20261014 --start "2026-10-13 22:11:58" --end "2026-10-14 22:07:43" --so-before SO-71831
# 写 output/ds/<night>/：orders_ds.json（过了检查的单）、orders_ds.txt（先列要人确认的）、raw.json（模型原始输出）、
#   以及 output/ds/poultry_ds_<night>.zip
# 检查不过的单一律扣下，不进 orders_ds.json。
# =============================================

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join('output', 'ds')
STATE = os.path.join(OUT_ROOT, 'state.json')
SKIP = re.compile(r'^\W*(got o|noted|ok\b|okay|thank|tq\b|—|-{3,})', re.I)
WORKERS = 6
DETAIL = 4          # 前几名候选给完整资料


def ddmmyyyy_key(d):
    return d[6:] + d[3:5] + d[:2]


def entered_refs():
    """已经录进系统的 ref → SO 号（公司电脑回传的 entered_log）。"""
    out = {}
    for path in glob.glob(os.path.join('data', 'entered', '*', 'entered_log.json')):
        with open(path, encoding='utf-8') as f:
            for ref, e in json.load(f).items():
                if e.get('doc_no'):
                    out[ref] = e['doc_no']
    return out


def build_prompt(kb, m, original, att_text='', n_images=0):
    base = original or m
    cands = kb.candidates(base, att_text)
    codes = [c['code'] for c in cands]
    label = {'document': 'PDF 附件', 'image': '图片'}.get(m['type'], '文字')
    parts = [f"## 消息\n群: {m['chat']}\n发送时间: {m['time']}\n类型: {label}\n" + ('配文' if m['type'] != 'text' else '原文') + f":\n{m['text']}"]
    if att_text:
        parts.append('## PDF 里的文字（照版面抽出来的）\n' + att_text[:12000])
    if n_images:
        parts.append(f'## 附件是 {n_images} 张图片（附在后面），订单内容请从图里读')
    if original:
        parts.append(f"## 这条是回复，被回复的原消息\n发送时间: {original['time']}\n原文:\n{original['text']}")
    parts.append('## 候选客户（按程序打分排序，不一定对）')
    for i, c in enumerate(cands):
        if i < DETAIL:
            parts.append(kb.describe_customer(c['code']) + ('\n  程序依据: ' + '；'.join(c['why']) if c['why'] else ''))
        else:
            cu = kb.customers[c['code']]
            parts.append(f"客户 {c['code']} | {cu['CompanyName'].strip()}（分数低，只列名字）")
    if not cands:
        parts.append('（程序没找到候选）')
    hints = kb.item_hints(m, codes)
    if hints:
        parts.append('## 每行品项以前怎么录\n' + hints)
    parts.append('## 货号表（货号 | 系统品名 | 常用单位）\n' + kb.item_table())
    return '\n\n'.join(parts), codes


def clean_branch(name):
    """模型有时把分店和地址一起抄过来：'[BILLING] NO 10, ...' 或 'BILLING （地址: ...）'，只留分店名。"""
    name = (name or '').strip()
    m = re.match(r'^\[([^\]]*)\]', name)
    if m:
        return m.group(1).strip()
    return re.split(r'\s*[（(]地址', name)[0].strip()


def check_order(kb, o, cand_codes, night):
    """返回 (要扣下的原因, 只提醒的事)。"""
    o['branch_name'] = clean_branch(o.get('branch_name'))
    hold, warn = [], []
    code = o.get('customer_code') or ''
    cust = kb.customers.get(code)
    if not code:
        hold.append('没选客户')
    elif not cust:
        hold.append(f'客户 {code} 不在主档')
    else:
        if cust.get('Status') != 'A':
            hold.append(f'客户 {code} 不是启用状态')
        if code not in cand_codes:
            hold.append(f'客户 {code} 不在程序给的候选里')
        hist = kb.history.get(code, [])
        if not hist:
            hold.append('这家以前没有单（新客户）')
        override = (o.get('company_name_override') or '').strip()
        if override and not any(h['company'].strip() == override for h in hist):
            hold.append(f'公司名改成 {override!r}，历史里没有同样的先例')
        if 'CASH' in cust['CompanyName'].upper() and not override:
            hold.append('公用 cash debtor 户口但没写公司名')
        names = {b['BranchName'] for b in kb.branches.get(code, [])}
        branch = o.get('branch_name') or ''
        if branch and branch not in names:
            hold.append(f'分店 {branch!r} 不在主档')
        if not branch and not any(h['branch'] == '' for h in hist[-10:]):
            hold.append('没填分店，这家以前都有分店')
    d = o.get('delivery_date') or ''
    if not re.match(r'^\d\d/\d\d/\d{4}$', d):
        hold.append(f'日期格式不对 {d!r}')
    else:
        try:
            dd = datetime.datetime.strptime(d, '%d/%m/%Y')
            n = datetime.datetime.strptime(night, '%Y%m%d')
            if dd <= n:
                hold.append(f'送货日期 {d} 不在明天以后（当日单归凌晨那位，或日期认错）')
            elif (dd - n).days > 14:
                hold.append(f'送货日期 {d} 太远')
        except ValueError:
            hold.append(f'日期不对 {d!r}')
    if not o.get('items'):
        hold.append('没有品项')
    for i in o.get('items') or []:
        ic, desc, uom = i.get('item_code'), i.get('description') or '', i.get('uom') or ''
        try:
            qty = float(i.get('qty'))
            if not math.isfinite(qty):
                raise ValueError
        except (TypeError, ValueError):
            hold.append(f'[{ic}] 数量不是数字 {i.get("qty")!r}')
            continue
        price = i.get('unit_price')
        if price not in (None, ''):
            try:
                if not (math.isfinite(float(price)) and 0 < float(price) < 1000):
                    raise ValueError
            except (TypeError, ValueError):
                hold.append(f'[{ic}] 价钱不对 {price!r}')
        if ic not in kb.items:
            hold.append(f'货号 {ic!r} 不在主档')
            continue
        if uom not in ('KG', 'NOS'):
            hold.append(f'[{ic}] 单位 {uom!r} 不对（只能 KG / NOS）')
        elif kb.uoms.get(ic) and uom not in kb.uoms[ic]:
            hold.append(f'[{ic}] 单位 {uom} 这个货号历史上没用过 {dict(kb.uoms[ic])}')
        has_count = re.search(r'\d+\s*(PCS|NOS)\b', desc.upper())
        if qty == 0 and not has_count:
            hold.append(f'[{ic}] 数量 0 但品名里没写件数')
        src = (i.get('source_line') or '').lower()
        if qty > 0 and uom == 'KG' and re.search(r'\d\s*(pcs|nos|ekor)\b', src):
            hold.append(f'[{ic}] 消息按只数下单，却填了 {qty} KG')
        if qty < 0 or qty > 2000:
            hold.append(f'[{ic}] 数量 {qty} 不合理')
        if shape(desc) not in kb.desc_shapes.get(ic, set()):
            hold.append(f'品名写法历史里没见过: [{ic}] {desc}')
        if i.get('unit_price') not in (None, '', 0) and not re.search(r'\d', src):
            warn.append(f'[{ic}] 填了价钱但原文那行看不到数字')
    return hold, warn


def to_entry(kb, o, ref):
    code = o['customer_code']
    cust = kb.customers[code]
    entry = {'ref': ref, 'code': code, 'company_name': cust['CompanyName'], 'doc_date': o['delivery_date']}
    if (o.get('company_name_override') or '').strip():
        entry['company_name_override'] = o['company_name_override'].strip()
    if o.get('branch_name'):
        entry['branch_name'] = o['branch_name']
        b = next((b for b in kb.branches[code] if b['BranchName'] == o['branch_name']), None)
        if b and b['Address1'].strip():
            entry['d_address1'] = b['Address1'].strip()
    if (o.get('doc_ref1') or '').strip():
        entry['doc_ref1'] = o['doc_ref1'].strip()
    elif o.get('staff'):
        entry['doc_ref1'] = 'STAFF'          # staff meal 另开的单 Ref 1 填 STAFF
    entry['items'] = []
    same = {}           # 同一个品项（货号、品名、单位、价钱都一样）出现几行，合成一行、数量相加，跟人工录法一样
    for i in o['items']:
        q = float(i['qty'])
        price = float(i['unit_price']) if i.get('unit_price') not in (None, '') else None
        key = (i['item_code'], i['description'].strip().upper(), i['uom'], price)
        if q > 0 and key in same:
            same[key]['qty'] += q
            continue
        item = {'item_code': i['item_code'], 'description': i['description'], 'qty': q, 'uom': i['uom']}
        if price is not None:
            item['unit_price'] = price
        entry['items'].append(item)
        if q > 0:
            same[key] = item
    for item in entry['items']:
        item['qty'] = int(item['qty']) if float(item['qty']).is_integer() else round(item['qty'], 3)
    return entry


def run(night, start, end, so_before=None, limit=None, dry_run=False, out_root=OUT_ROOT):
    kb = KB(start, so_before=so_before, night=night)
    byid = kb.byid
    done = entered_refs()
    msgs = [m for m in kb.messages if start < m['time'] <= end]
    todo, manual, skipped, not_ours = [], [], [], []
    att = {}            # 消息 id → (PDF 文字, 图片)
    for m in msgs:
        if m['type'] in ('deleted', 'system'):
            continue
        if m['type'] in ('document', 'image'):
            text, images, problem = attachments.read(m)
            if not text and not images:
                manual.append((m, f"{'PDF' if m['type'] == 'document' else '图片'}订单读不到：{problem}（配文: {m['text'][:60]!r}）"))
                continue
            att[m['id']] = (text, images)
            todo.append(m)
            continue
        if m['type'] != 'text' or not m['text'].strip():
            continue
        if SKIP.match(m['text']) and len(m['text']) < 40:
            skipped.append(m)
            continue
        po = KBM.LA.parse_order(m)
        if po and not m['reply_to'] and ddmmyyyy_key(po['date']) <= night:
            not_ours.append((m, po['date']))
            continue
        todo.append(m)
    if limit:
        todo = todo[:limit]

    with open(os.path.join(HERE, 'rules_prompt.md'), encoding='utf-8') as f:
        system = f.read()
    jobs = []
    for m in todo:
        original = byid.get(m['reply_to']) if m['reply_to'] else None
        text, images = att.get(m['id'], ('', []))
        user, codes = build_prompt(kb, m, original, text, len(images))
        jobs.append((m, original, user, codes))
    if dry_run:
        sizes = [len(system) + len(u) for _, _, u, _ in jobs]
        print(f'{len(jobs)} 条要识别，提示词平均 {sum(sizes) // max(len(sizes), 1)} 字，最长 {max(sizes or [0])} 字')
        return jobs

    results = {}
    usage = collections.Counter()

    def work(job):
        m, original, user, codes = job
        try:
            out, u = deepseek.chat_json(system, user, images=att.get(m['id'], ('', []))[1])
            return m['id'], out, u, None
        except ValueError as e:
            return m['id'], None, {}, str(e)

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        for mid, out, u, err in ex.map(work, jobs):
            results[mid] = (out, err)
            for k in ('prompt_tokens', 'completion_tokens'):
                usage[k] += u.get(k, 0) if not u.get('cached') else 0
            usage['cached' if u.get('cached') else 'called'] += 1

    good, held, raw = [], [], []
    by_msg = {}            # 消息 id → 这条消息开出来的单（给回复合并用）
    for m, original, user, codes in jobs:
        out, err = results[m['id']]
        id5 = m['id'][-5:]
        base_ref = f"{m['time'][:10].replace('-', '')}-{m['chat'][:3]}-{id5}"
        raw.append({'id': m['id'], 'ref': base_ref, 'time': m['time'], 'chat': m['chat'], 'text': m['text'],
                    'candidates': codes, 'output': out, 'error': err})
        if err or not isinstance(out, dict):
            held.append({'ref': base_ref, 'm': m, 'why': [f'DeepSeek 没给结果: {err}'], 'order': None})
            continue
        kind = out.get('kind')
        questions = [q for q in out.get('questions') or [] if str(q).strip()]
        if kind == 'not_an_order':
            skipped.append(m)
            continue
        if kind in ('add_on', 'amend'):
            orig_ref = None
            if original:
                orig_ref = f"{original['time'][:10].replace('-', '')}-{original['chat'][:3]}-{original['id'][-5:]}"
            if kind == 'add_on' and original and original['id'] in by_msg and not questions:
                target = by_msg[original['id']]
                adds = out.get('orders') or []
                hold, warn = ([] if adds else ['加单回复里没认出品项']), []
                for o in adds:
                    h, w = check_order(kb, dict(o, customer_code=target['order']['customer_code'],
                                                branch_name=target['order'].get('branch_name'),
                                                delivery_date=target['order']['delivery_date']), codes + [target['order']['customer_code']], night)
                    hold += h
                    warn += w
                if not hold:        # 全部过了才并进原单，不并一半
                    for o in adds:
                        target['order']['items'] += o.get('items') or []
                    target['merged'].append(base_ref)
                    target['warn'] += warn
                    continue
            where = f'原单 {orig_ref} 已录成 {done[orig_ref]}' if orig_ref in done else (f'原单 {orig_ref}' if orig_ref else '找不到原单')
            held.append({'ref': base_ref, 'm': m, 'why': [f"{'加单' if kind == 'add_on' else '改单/取消/改日期'}，回复旧单（{where}），要人工处理"] + questions,
                         'order': out.get('orders')})
            continue
        orders = out.get('orders') or []
        if not orders:
            held.append({'ref': base_ref, 'm': m, 'why': ['模型说是订单但没开出单'] + questions, 'order': None})
            continue
        n_plain = 0
        for o in orders:
            if o.get('staff'):
                ref = base_ref + '-STAFF'
            else:
                n_plain += 1
                ref = base_ref if n_plain == 1 else f'{base_ref}-{n_plain}'
            if m['type'] != 'text':
                for i in o.get('items') or []:
                    i['unit_price'] = None          # PDF / 图片里的价钱不填，用系统带出的
            hold, warn = check_order(kb, o, codes, night)
            hold += questions
            rec = {'ref': ref, 'm': m, 'why': hold, 'warn': warn, 'order': o, 'merged': []}
            if hold:
                held.append(rec)
            else:
                good.append(rec)
                by_msg.setdefault(m['id'], rec)

    # 合并之后再查一次，并按消息时间排
    entries, lines = [], []
    for rec in sorted(good, key=lambda r: (r['m']['time'], r['ref'])):
        entry = to_entry(kb, rec['order'], rec['ref'])
        entries.append((rec, entry))

    out_dir = os.path.join(out_root, night)
    os.makedirs(out_dir, exist_ok=True)
    lines.append(f'DeepSeek 草稿（影子模式，只用来对比，不要录入）  批次 {night}  消息 {start} 之后到 {end}')
    lines.append(f'生成时间 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  模型 {deepseek.MODEL}  通过 {len(entries)} 张，扣下 {len(held)} 条，附件 {len(manual)} 条')
    lines.append('')
    lines.append('======== 要人确认的（没放进 orders_ds.json）========')
    for rec in held:
        m = rec['m']
        lines.append(f"[{rec['ref']}] {m['time'][5:16]}  " + ' / '.join(l.strip() for l in m['text'].split('\n') if l.strip())[:200])
        for w in rec['why']:
            lines.append(f'   ? {w}')
        o = rec.get('order')
        if isinstance(o, dict) and o.get('items'):
            lines.append(f"   模型的版本: {o.get('customer_code')} 分店 {o.get('branch_name')} 日期 {o.get('delivery_date')}: "
                         + ' ; '.join(f"[{i.get('item_code')}] {i.get('description')} | {i.get('qty')} {i.get('uom')}" for i in o['items']))
    for m, why in manual:
        lines.append(f"[{m['chat'][:3]} {m['time'][5:16]}] {why}")
    lines.append('')
    lines.append('======== 通过检查的单 ========')
    for n, (rec, e) in enumerate(entries, 1):
        m = rec['m']
        name = e.get('company_name_override') or e['company_name']
        lines.append(f"{n}. [{e['ref']}] {m['time'][5:16]} {e['code']} {name.strip()} | 分店 {e.get('branch_name') or '(不填)'} | 日期 {e['doc_date']}"
                     + (f" | Ref1 {e['doc_ref1']}" if e.get('doc_ref1') else ''))
        lines.append('   消息: ' + ' / '.join(l.strip() for l in m['text'].split('\n') if l.strip())[:300])
        for i in e['items']:
            lines.append(f"   - [{i['item_code']}] {i['description']} | {i['qty']} {i['uom']}" + (f" @ {i['unit_price']}" if 'unit_price' in i else ''))
        if rec['merged']:
            lines.append(f"   已并入回复的加单: {', '.join(rec['merged'])}")
        for w in rec['warn']:
            lines.append(f'   提醒: {w}')
        if rec['order'].get('notes'):
            lines.append(f"   模型说明: {rec['order']['notes']}")
    lines.append('')
    lines.append('======== 没录：送货日是当天或更早的单（归凌晨那位处理）========')
    for m, d in not_ours:
        lines.append(f"[{m['chat'][:3]} {m['time'][5:16]}] 日期 {d}  " + ' / '.join(l.strip() for l in m['text'].split('\n') if l.strip())[:160])
    lines.append('')
    lines.append(f'跳过（不是订单 / got order）: {len(skipped)} 条')
    lines.append(f"DeepSeek 用量: 调用 {usage['called']} 次（缓存 {usage['cached']} 次），输入 {usage['prompt_tokens']} tokens，输出 {usage['completion_tokens']} tokens")

    try:
        bal = balance.text()
    except Exception as e:        # 查不到余额不影响出单
        bal = f'DeepSeek 余额查询失败: {e}'
    lines.insert(2, bal)
    with open(os.path.join(out_dir, 'orders_ds.json'), 'w', encoding='utf-8') as f:
        json.dump([e for _, e in entries], f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, 'orders_ds.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    with open(os.path.join(out_dir, 'raw.json'), 'w', encoding='utf-8') as f:
        json.dump({'night': night, 'start': start, 'end': end, 'so_before': so_before, 'model': deepseek.MODEL,
                   'results': raw, 'held': [{'ref': r['ref'], 'why': r['why']} for r in held],
                   'manual': [f"{m['time'][:10].replace('-', '')}-{m['chat'][:3]}-{m['id'][-5:]}" for m, _ in manual]}, f, ensure_ascii=False, indent=1)
    zpath = os.path.join(out_root, f'poultry_ds_{night}.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(out_dir, 'orders_ds.json'), f'orders_ds_{night}.json')
        z.write(os.path.join(out_dir, 'orders_ds.txt'), f'orders_ds_{night}.txt')
        z.writestr('README.txt', 'DeepSeek shadow draft. FOR COMPARISON ONLY. DO NOT ENTER INTO SQL ACCOUNT.\r\n')
    if out_root == OUT_ROOT:
        shutil.copyfile(zpath, os.path.join(out_root, 'latest.zip'))      # n8n 的下载链接固定拿这个
    print(f'通过 {len(entries)} 张，扣下 {len(held)} 条，附件 {len(manual)} 条 → {zpath}')
    print(bal)
    print(f"DeepSeek: 调用 {usage['called']} 次，缓存 {usage['cached']} 次，输入 {usage['prompt_tokens']} / 输出 {usage['completion_tokens']} tokens")
    return zpath


def auto_window():
    """n8n 每晚用：从上一次结束（或 Claude 最近一晚最后一条消息）到现在。"""
    now = datetime.datetime.now()
    start = None
    if os.path.exists(STATE):
        with open(STATE, encoding='utf-8') as f:
            start = json.load(f).get('last_end')
    if not start:
        files = sorted(glob.glob(os.path.join('output', 'orders_2*.json')))
        with open(LA_MESSAGES, encoding='utf-8') as f:
            ms = {m['id'][-5:] + m['time'][:10].replace('-', ''): m for m in json.load(f)}
        times = []
        for path in files[-2:]:
            with open(path, encoding='utf-8') as f:
                for o in json.load(f):
                    day, _, id5 = o['ref'].split('-')[:3]
                    m = ms.get(id5 + day)
                    if m:
                        times.append(m['time'])
        start = max(times) if times else (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d 22:00:00')
    return now.strftime('%Y%m%d'), start, now.strftime('%Y-%m-%d %H:%M:%S')


LA_MESSAGES = os.path.join('data', 'wa_messages.json')


def main():
    p = argparse.ArgumentParser(description='DeepSeek 版每晚开单（影子模式）')
    p.add_argument('--auto', action='store_true', help='从上次结束到现在（n8n 用）')
    p.add_argument('--night')
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--so-before')
    p.add_argument('--limit', type=int)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--out-root', default=OUT_ROOT)
    a = p.parse_args()
    if a.auto:
        night, start, end = auto_window()
    else:
        night, start, end = a.night, a.start, a.end
        if not (night and start and end):
            p.error('不用 --auto 时要给 --night --start --end')
    path = run(night, start, end, so_before=a.so_before, limit=a.limit, dry_run=a.dry_run, out_root=a.out_root)
    if a.auto and not a.dry_run:
        os.makedirs(OUT_ROOT, exist_ok=True)
        with open(STATE, 'w', encoding='utf-8') as f:
            json.dump({'last_end': end, 'last_zip': path}, f)


if __name__ == '__main__':
    main()
