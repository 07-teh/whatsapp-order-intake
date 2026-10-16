"""Learn how each product line is written in the chat -> item code.

Inside each message/Sales Order pair, a message line and an order line with the same unique
quantity are taken to be the same product. Aggregates these into a general alias table plus
per-customer overrides (a customer who always means a different item code by the same word).
Output: mapping/item_aliases.csv, mapping/item_aliases_by_customer.csv
Comments in this file are in Chinese.
"""
import collections
import csv
import json
import os
import re

from learn_aliases import (DATE, KG_PER_CTN, KG_PER_PKT, MESSAGES_JSON, PAIRS_JSON, QTY,
                           clean, load_sales_orders, parse_order)

# =============================================
# 从已经配好对的消息和 Sales Order 里，学出"群里的品项写法 -> 货号"
# 做法：同一对里，消息的某一行和单里的某一行数量相同且都只出现一次，就认为是同一个品项。
#   python3 tools/learn_items.py        （先跑过 tools/learn_aliases.py）
# 写: mapping/item_aliases.csv              通用的写法 -> 货号
#     mapping/item_aliases_by_customer.csv  某个客户固定录成另一个货号的例外
# =============================================

ITEMS_CSV = os.path.join('mapping', 'item_aliases.csv')
BY_CUSTOMER_CSV = os.path.join('mapping', 'item_aliases_by_customer.csv')
MASTER_ITEMS_CSV = os.path.join('mapping', 'master_items.csv')
FROZEN = re.compile(r'\b(keras|frozen|froze)\b', re.I)
NOISE = re.compile(r'\b(fresh|keras|frozen|froze|size|ikut|rm)\b', re.I)
# 切法会决定货号（同样写 boneless leg，切块的是另一个货号），写在带数字的括号里也要认出来
CUTS = [(re.compile(r'\bcube|cut into', re.I), 'cube'), (re.compile(r'\bslice', re.I), 'slice'),
        (re.compile(r'\bbutterfl', re.I), 'butterfly')]
MIN_SEEN = 2          # 至少见过几次才算数
MIN_AGREEMENT = 0.9   # 至少几成录成同一个货号才算数
MIN_OVERRIDE = 3      # 客户例外至少见过几次


def item_line(line):
    """把一行品项拆成 (写法, 是否冻货, 数量签名)。不是品项行、或一行里有两个数量，就返回 None。"""
    text = clean(line)
    # 带数字的括号是规格、包装或价钱，去掉；不带数字的括号是品项的一部分，例如 MM (BB) 和 MM (Leg)
    body = re.sub(r'\([^)]*\d[^)]*\)', ' ', text)
    body = re.sub(r'\d+(\.\d+)?\s*(kg|g)?\s*-\s*\d+(\.\d+)?\s*(kg|g)\b(\s*up)?', ' ', body, flags=re.I)
    body = re.sub(r'\d+(\.\d+)?\s*(kg|g)\s*up', ' ', body, flags=re.I)
    found = QTY.findall(body)
    if len(found) != 1 or DATE.match(text):
        return None
    number, unit = float(found[0][0]), found[0][1].lower()
    if unit.startswith('kg'):
        sig = ('kg', number)
    elif unit.startswith('ctn'):
        sig = ('kg', number * KG_PER_CTN)
    elif unit.startswith(('pkt', 'pack')):
        sig = ('kg', number * KG_PER_PKT)
    else:
        sig = ('cnt', number)
    alias = QTY.sub(' ', body)
    alias = NOISE.sub(' ', alias)
    alias = re.sub(r'\d+(\.\d+)?\s*g\b', ' ', alias, flags=re.I)      # 单独的克数规格，如 280g
    alias = re.sub(r'[^a-z0-9& ]', ' ', alias.lower())
    alias = re.sub(r'\s+', ' ', alias).strip()
    if len(alias) < 2:
        return None
    for pattern, tag in CUTS:
        if pattern.search(text) and tag not in alias:
            alias += f' +{tag}'
    return alias, bool(FROZEN.search(text)), sig


def so_line_sig(item):
    count = re.search(r'(\d+)\s*(NOS|PCS)\b', item['Description'])
    qty = float(item['Qty'] or 0)
    if count:
        return ('cnt', float(count.group(1)))
    if qty > 0:
        return ('cnt' if item['UOM'] == 'NOS' else 'kg', qty)
    return None


def collect(pairs, messages, sales_orders):
    """把每一对里能对上的品项行列出来。"""
    for pair in pairs:
        if pair['how'] != 'exact':
            continue
        message, so = messages[pair['message_id']], sales_orders[pair['doc_no']]
        lines = [(raw, item_line(raw)) for raw in message['text'].split('\n')]
        lines = [(raw, parsed) for raw, parsed in lines if parsed]
        so_lines = [(item, so_line_sig(item)) for item in so['items']]
        line_count = collections.Counter(parsed[2] for _, parsed in lines)
        so_count = collections.Counter(sig for _, sig in so_lines if sig)
        for raw, (alias, frozen, sig) in lines:
            if line_count[sig] == 1 and so_count[sig] == 1:
                item = next(item for item, s in so_lines if s == sig)
                yield {'key': (alias, frozen), 'customer': so['Code'], 'company': so['CompanyName'].strip(),
                       'code': item['ItemCode'], 'line': clean(raw), 'entered_as': item['Description']}


def build(samples):
    """返回 (通用表, 客户例外表)。通用表: 写法 -> Counter(货号)；例外表: (客户码, 写法) -> 货号。"""
    general = collections.defaultdict(collections.Counter)
    by_customer = collections.defaultdict(collections.Counter)
    for sample in samples:
        general[sample['key']][sample['code']] += 1
        by_customer[(sample['customer'], sample['key'])][sample['code']] += 1
    overrides = {}
    for (customer, key), counter in by_customer.items():
        code, count = counter.most_common(1)[0]
        total = sum(counter.values())
        if code != general[key].most_common(1)[0][0] and total >= MIN_OVERRIDE and count / total >= MIN_AGREEMENT:
            overrides[(customer, key)] = code
    return general, overrides


def lookup(general, overrides, customer, key):
    """查一个品项写法的货号。没把握就返回 None（留给模型或人）。"""
    if (customer, key) in overrides:
        return overrides[(customer, key)]
    counter = collections.Counter(general.get(key, ()))
    # 已经算进客户例外的那些次数不算，不然会把通用写法拖成"不一致"
    for (_, other_key), code in overrides.items():
        if other_key == key:
            counter[code] = 0
    total = sum(counter.values())
    if total < MIN_SEEN:
        return None
    code, count = counter.most_common(1)[0]
    return code if count / total >= MIN_AGREEMENT else None


def main():
    with open(MESSAGES_JSON, encoding='utf-8') as f:
        messages = {m['id']: m for m in json.load(f)}
    sales_orders = {o['DocNo']: o for o in load_sales_orders()}
    with open(PAIRS_JSON, encoding='utf-8') as f:
        pairs = json.load(f)
    with open(MASTER_ITEMS_CSV, encoding='utf-8') as f:
        master = {r['item_code']: r['system_description'] for r in csv.DictReader(f, delimiter='|', quoting=csv.QUOTE_NONE)}

    samples = list(collect(pairs, messages, sales_orders))
    general, overrides = build(samples)
    example = {}
    for sample in samples:
        example.setdefault((sample['key'], sample['code']), sample)
        example.setdefault((sample['customer'], sample['key'], sample['code']), sample)

    rows = []
    for key, counter in general.items():
        decided = lookup(general, overrides, None, key)
        code = decided or counter.most_common(1)[0][0]
        total = sum(counter.values())
        sample = example[(key, code)]
        rows.append({'alias': key[0], 'frozen': 'Y' if key[1] else '', 'item_code': code,
                     'system_description': master.get(code, ''), 'times_seen': total,
                     'agreement': f'{counter[code] / total:.0%}',
                     'other_codes_seen': ' / '.join(f'{c}×{n}' for c, n in counter.most_common() if c != code)[:60],
                     'example_message_line': sample['line'], 'example_entered_as': sample['entered_as'],
                     'check': '' if decided else ('只见过一次' if total == 1 else '对到过不同货号')})
    rows.sort(key=lambda r: (-r['times_seen'], r['alias']))
    with open(ITEMS_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    override_rows = []
    for (customer, key), code in overrides.items():
        sample = example[(customer, key, code)]
        override_rows.append({'customer_code': customer, 'system_company_name': sample['company'], 'alias': key[0],
                              'frozen': 'Y' if key[1] else '', 'item_code': code,
                              'usual_item_code': general[key].most_common(1)[0][0],
                              'example_message_line': sample['line'], 'example_entered_as': sample['entered_as']})
    override_rows.sort(key=lambda r: (r['customer_code'], r['alias']))
    with open(BY_CUSTOMER_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(override_rows[0].keys()))
        writer.writeheader()
        writer.writerows(override_rows)

    usable = {(r['alias'], r['frozen'] == 'Y') for r in rows if not r['check']}
    print(f'对上品项行 {len(samples)} 行')
    print(f'{ITEMS_CSV}：{len(rows)} 种写法，可直接用 {len(usable)} 种，涉及货号 {len({r["item_code"] for r in rows})} 个')
    print(f'{BY_CUSTOMER_CSV}：{len(override_rows)} 条客户例外')

    # 放回全部历史订单：品项行的写法有多少在表里
    total = hit = 0
    missing = collections.Counter()
    for message in messages.values():
        if message['type'] != 'text' or message['reply_to'] or not parse_order(message):
            continue
        for raw in message['text'].split('\n'):
            parsed = item_line(raw)
            if parsed:
                total += 1
                hit += (parsed[0], parsed[1]) in usable
                if (parsed[0], parsed[1]) not in general:
                    missing[parsed[0]] += 1
    print(f'全部历史订单的品项行 {total} 行：写法可直接用的 {hit} 行（{hit / total:.0%}）')
    print('表里没有的写法里最常见的:', missing.most_common(10))


if __name__ == '__main__':
    main()
