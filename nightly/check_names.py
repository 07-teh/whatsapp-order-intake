# python3 check_names.py output/orders_YYYYMMDD.json   列出"消息里的名字/地址和录进去的客户、分店一个词都对不上"的单；顺便按录入脚本的规则查格式
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
ms, _ = load()
by5 = {m['id'][-5:]: m for m in ms if m['time'] > '2026-10-09'}
bs = json.load(open('data/master_branches.json', encoding='utf-8-sig'))
STOP = set('sdn bhd the and plt m restaurant restoran cafe food foods kitchen enterprise management holding chicken fresh frozen keras delivery jalan'.split())
def words(t):
    return {w for w in re.findall(r'[a-z0-9]{3,}', t.lower()) if w not in STOP}
n = 0
for o in json.load(open(sys.argv[1], encoding='utf-8')):
    m = by5[o['ref'].split('-')[2]]
    assert re.match(r'^[A-Za-z0-9._/-]+$', o['code']) and re.match(r'^\d\d/\d\d/\d{4}$', o['doc_date']) and o['items']
    for i in o['items']:
        assert re.match(r'^[A-Za-z0-9 ._/+()&-]+$', i['item_code']) and float(i['qty']) >= 0 and not (i.get('uom') and not i['uom'].isalpha())
    if m['type'] != 'text':
        continue
    head = ' '.join(l for l in m['text'].split('\n')[:9] if not LI.item_line(l))
    addr = ' '.join(' '.join([b['Address1'], b['Address2'], b['Address3'], b['Address4']]) for b in bs if b['Code'] == o['code'] and b['BranchName'] == o.get('branch_name', ''))
    if not (words(head) & words(o['company_name'] + ' ' + o.get('company_name_override', '') + ' ' + o.get('branch_name', '') + ' ' + addr)):
        n += 1
        print(f"对不上: {o['ref']} | {head[:90]!r} → {o['company_name']} / {o.get('branch_name')}")
print('对不上的', n, '张')
