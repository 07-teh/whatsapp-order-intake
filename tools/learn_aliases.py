"""Learn how each customer is *written* in the chat -> customer code + branch in SQL Account.

Pairs every historical order message with the Sales Order that was actually entered, using only
the delivery date and the quantity signature (pkt = 2 kg, ctn = 12 kg, pcs counted separately),
never the name. The names learned from those pairs are therefore evidence, not guesses.
Input:  data/wa_messages.json, data/so_history/*.json
Output: mapping/customer_aliases.csv, data/pairs.json (message <-> Sales Order pairs, reused for evaluation)
Comments in this file are in Chinese.
"""
import collections
import csv
import json
import os
import re

# =============================================
# 把群里的订单消息和系统里实际录的 Sales Order 配成对，从中学出"群里的叫法 -> 客户码 + 分店"
# 配对只看送货日期和数量，不看名字，所以学出来的叫法不是猜的。
#   python3 tools/learn_aliases.py
# 读: data/wa_messages.json（tools/wa_extract.py 生成）、data/so_history/*.json（公司电脑导出，可放多个）
# 写: mapping/customer_aliases.csv（对照表）、data/pairs.json（消息和单的配对，以后算准确率用）
# =============================================

MESSAGES_JSON = os.path.join('data', 'wa_messages.json')
SO_DIR = os.path.join('data', 'so_history')   # 公司电脑导出的单，可以放多个 json（不同日期范围）
ALIASES_CSV = os.path.join('mapping', 'customer_aliases.csv')
PAIRS_JSON = os.path.join('data', 'pairs.json')
KG_PER_CTN = 12
KG_PER_PKT = 2
DATE = re.compile(r'(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)')
# 单独占一行的标记，不是名字：例如 "🚨Delivery 🚛" 下一行才是送货的店名
MARKER = re.compile(r'^(amended?|delivery|deliver to|delivery to|new order|urgent)$', re.I)
NOT_AN_ORDER = re.compile(r'^\W*(add|remark|cancel|change|got o)', re.I)
QTY = re.compile(r'(\d+(?:\.\d+)?)\s*(kgs?|nos|pcs|pkts?|ctns?|ekor|packs?)\b', re.I)
# 第二行如果是地址、营业时间、品项，就不算名字的一部分
NOT_A_NAME = re.compile(r'jalan|jln|lorong|\bno\.?\s*\d|\d{5}|\bopen|\btime\b|before|after|\d\s*[ap]m\b|deliver(?!y?\s*to)|fresh|frozen|keras', re.I)


def load_sales_orders():
    """读 data/so_history/ 下所有导出文件，同一张单只留一份，取消的单不要。"""
    orders = {}
    for name in sorted(os.listdir(SO_DIR)):
        if name.endswith('.json'):
            with open(os.path.join(SO_DIR, name), encoding='utf-8-sig') as f:
                for order in json.load(f):
                    orders[order['DocKey']] = order
    return [o for o in orders.values() if o['Cancelled'] == 'False']


def clean(line):
    return re.sub(r'[*_~🚛🚨]', '', line).strip()


def norm(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def quantities(text):
    """把一段订单文字里的数量取出来。规格（括号里的、1.8-2kg 这种范围）先去掉。"""
    body = re.sub(r'\([^)]*\)', ' ', text)
    body = re.sub(r'\d+(\.\d+)?\s*(kg|g)?\s*-\s*\d+(\.\d+)?\s*(kg|g)\b(\s*up)?', ' ', body, flags=re.I)
    body = re.sub(r'\d+(\.\d+)?\s*(kg|g)\s*up', ' ', body, flags=re.I)
    sig = collections.Counter()
    for number, unit in QTY.findall(body):
        number, unit = float(number), unit.lower()
        if unit.startswith('kg'):
            sig[('kg', number)] += 1
        elif unit.startswith('ctn'):
            sig[('kg', number * KG_PER_CTN)] += 1
        elif unit.startswith(('pkt', 'pack')):
            sig[('kg', number * KG_PER_PKT)] += 1
        else:
            sig[('cnt', number)] += 1
    return sig


def parse_order(message):
    """认出一条订单消息的送货日期和开头的名字行。认不出就返回 None。"""
    lines = [clean(line) for line in message['text'].split('\n')]
    # 带数字的行不算标记：Amin 的 "Delivery : 18/4" 是日期行，要留着
    lines = [line for line in lines
             if line and (re.search(r'\d', line) or not MARKER.match(re.sub(r'[^A-Za-z ]', '', line).strip()))]
    if len(lines) < 2 or NOT_AN_ORDER.match(lines[0]):
        return None
    first = DATE.match(lines[0])
    if first and len(lines[0]) <= 24:
        # Mei 的写法：第一行日期，下面是客户
        found, names = first, lines[1:3]
    else:
        # Amin 的写法：第一行客户，后面有一行 Delivery: 日期
        index = next((i for i, line in enumerate(lines[1:5], 1)
                      if re.search(r'deliver', line, re.I) and DATE.search(line)), None)
        if index is None:
            index = next((i for i, line in enumerate(lines[1:4], 1) if DATE.match(line) and len(line) <= 30), None)
        if index is None:
            return None
        found, names = DATE.search(lines[index]), lines[:min(index, 2)]
    day, month = int(found.group(1)), int(found.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return {'id': message['id'], 'chat': message['chat'], 'date': f'{day:02d}/{month:02d}/{message["time"][:4]}',
            'names': names, 'sig': quantities('\n'.join(lines))}


def so_quantities(order):
    sig = collections.Counter()
    for item in order['items']:
        count = re.search(r'(\d+)\s*(NOS|PCS)\b', item['Description'])
        qty = float(item['Qty'] or 0)
        if count:
            sig[('cnt', float(count.group(1)))] += 1
        elif qty > 0:
            sig[('cnt' if item['UOM'] == 'NOS' else 'kg', qty)] += 1
    return sig


def overlap(a, b):
    total = max(sum(a.values()), sum(b.values()))
    return sum((a & b).values()) / total if total else 0


def outlet_line(order):
    """名字的第二行（分店、Delivery to 哪里）。第二行不是名字就返回空。"""
    if len(order['names']) < 2:
        return ''
    line = order['names'][1]
    return '' if NOT_A_NAME.search(line) or QTY.search(line) or DATE.match(line) else line


RECENT = 5   # 只看最近几次怎么录的：客户会换客户码（例如从 CASH DEBTOR 换成自己的户口），旧的录法不能算数


def decide(history):
    """history 是 [(消息时间, 目标)]。返回 (最近最常用的目标, 最近几次里它占几成, 一共见过几次)。"""
    recent = [target for _, target in sorted(history)[-RECENT:]]
    target, count = collections.Counter(recent).most_common(1)[0]
    return target, count / len(recent), len(history)


def alias_keys(order):
    line1 = norm(order['names'][0])
    return line1, f'{line1}|{norm(outlet_line(order))}'


def main():
    with open(MESSAGES_JSON, encoding='utf-8') as f:
        messages = json.load(f)
    sales_orders = load_sales_orders()

    orders = {}
    for message in messages:
        if message['type'] == 'text' and not message['reply_to']:
            order = parse_order(message)
            if order:
                orders[order['id']] = order
    # Add 是回复原单发的，把它的数量并进原单
    for message in messages:
        if message['reply_to'] in orders and re.match(r'^\W*add', message['text'], re.I):
            orders[message['reply_to']]['sig'] += quantities(message['text'])

    by_date = collections.defaultdict(list)
    for so in sales_orders:
        so['sig'] = so_quantities(so)
        by_date[so['DocDate']].append(so)
    in_range = [o for o in orders.values() if o['date'] in by_date and o['sig']]

    # 第一轮：同一天里数量完全一致，而且消息和单互相都只有这一个候选
    candidates = [(o, so) for o in in_range for so in by_date[o['date']] if overlap(o['sig'], so['sig']) >= 0.99]
    per_order = collections.Counter(o['id'] for o, so in candidates)
    per_so = collections.Counter(so['DocKey'] for o, so in candidates)
    pairs = {o['id']: (so, 'exact') for o, so in candidates if per_order[o['id']] == 1 and per_so[so['DocKey']] == 1}

    # 第二轮：用第一轮学到的叫法，去认剩下那些"同一天有好几张数量相近的单"的消息
    learned = collections.defaultdict(collections.Counter)
    for order_id, (so, _) in pairs.items():
        for key in alias_keys(orders[order_id]):
            learned[key][so['Code']] += 1
    taken = {so['DocKey'] for so, _ in pairs.values()}
    for order in in_range:
        if order['id'] in pairs:
            continue
        codes = set()
        for key in alias_keys(order):
            codes |= set(learned.get(key, ()))
        best = sorted(((overlap(order['sig'], so['sig']), so) for so in by_date[order['date']]
                       if so['Code'] in codes and so['DocKey'] not in taken), key=lambda x: -x[0])
        if best and best[0][0] >= 0.5 and (len(best) == 1 or best[0][0] > best[1][0]):
            pairs[order['id']] = (best[0][1], 'by_name')
            taken.add(best[0][1]['DocKey'])

    # 汇总成对照表：先看只凭第一行够不够；不够（同一行对到多个客户或分店）再带上第二行
    line1_votes = collections.defaultdict(list)
    both_votes = collections.defaultdict(list)
    sent = {m['id']: m['time'] for m in messages}
    shown = {}
    for order_id, (so, _) in pairs.items():
        order = orders[order_id]
        line1, both = alias_keys(order)
        target = (so['Code'], so['CompanyName'].strip(), so['BranchName'])
        line1_votes[line1].append((sent[order_id], target))
        both_votes[both].append((sent[order_id], target))
        shown[line1] = order['names'][0]
        shown[both] = outlet_line(order)
    # 同一个第一行在历史里带过不止一种分店写法的，一律要看第二行。
    # 否则这段时间没下单的分店，会被当成下过单的那家。
    outlets = collections.defaultdict(set)
    for order in orders.values():
        outlets[alias_keys(order)[0]].add(norm(outlet_line(order)))
    rows = []
    for line1, votes in line1_votes.items():
        if decide(votes)[1] >= 0.9 and len(outlets[line1]) <= 1:
            groups = [(shown[line1], '', votes)]
        else:
            groups = [(shown[line1], shown[both], v) for both, v in both_votes.items() if both.split('|')[0] == line1]
        for name1, name2, v in groups:
            (code, company, branch), share, n = decide(v)
            rows.append({'alias_line1': name1, 'alias_line2': name2, 'customer_code': code, 'system_company_name': company,
                         'branch_name': branch, 'times_seen': n, 'last_seen': max(v)[0][:10], 'agreement': f'{share:.0%}',
                         'check': '' if share >= 0.9 and n >= 2 else ('只见过一次' if n == 1 else '最近几次录法不一致')})
    rows.sort(key=lambda r: (-r['times_seen'], r['alias_line1']))
    with open(ALIASES_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with open(PAIRS_JSON, 'w', encoding='utf-8') as f:
        json.dump([{'message_id': k, 'doc_no': so['DocNo'], 'how': how} for k, (so, how) in pairs.items()], f, indent=1)

    exact = sum(1 for _, how in pairs.values() if how == 'exact')
    print(f'导出范围内的订单消息 {len(in_range)} 条，系统里的单 {len(sales_orders)} 张')
    print(f'配上 {len(pairs)} 对：数量完全一致 {exact}，靠已学到的叫法补认 {len(pairs) - exact}')
    print(f'没配上的消息 {len(in_range) - len(pairs)} 条，没配上的单 {len(sales_orders) - len(taken)} 张')
    print(f'对照表 {ALIASES_CSV}：{len(rows)} 行，其中可直接用 {sum(1 for r in rows if not r["check"])} 行')

    # 这张对照表放回全部历史订单上，能认出多少
    single = {line1 for line1 in line1_votes if len(outlets[line1]) <= 1}
    known1 = {norm(r['alias_line1']) for r in rows if not r['alias_line2']} & single
    known2 = {f"{norm(r['alias_line1'])}|{norm(r['alias_line2'])}" for r in rows}
    for chat in sorted({o['chat'] for o in orders.values()}):
        part = [o for o in orders.values() if o['chat'] == chat]
        hit = sum(1 for o in part if alias_keys(o)[0] in known1 or alias_keys(o)[1] in known2)
        print(f'  {chat}: 历史订单 {len(part)} 条，叫法在对照表里的 {hit} 条（{hit / len(part):.0%}）')


if __name__ == '__main__':
    main()
