# python3 reconcile.py   拿系统里现在的单，和我这几晚录进去的逐张比；再列出系统里有、但不是我录的单
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
ms, sos = load()
bydoc = {o['DocNo']: o for o in sos}
allso = {}
for name in sorted(os.listdir('data/so_history')):
    for o in json.load(open('data/so_history/' + name, encoding='utf-8-sig')):
        allso[o['DocNo']] = o          # 含已取消的
mine = {}
for d in sorted(os.listdir('data/entered')):
    log = json.load(open(f'data/entered/{d}/entered_log.json', encoding='utf-8'))
    for ref, e in log.items():
        if e.get('doc_no'):
            mine[e['doc_no']] = ref
orders = {o['ref']: o for o in my_orders()}
print(f'我录的单 {len(mine)} 张；系统导出里找得到 {sum(1 for d in mine if d in allso)} 张')
n = 0
for doc, ref in sorted(mine.items()):
    so, o = allso.get(doc), orders.get(ref)
    if not so:
        if o and datekey(o['doc_date']) >= '20261009':
            print(f'!! {doc} {ref} 系统导出里没有（日期 {o["doc_date"]}）')
        continue
    if not o:
        continue
    diff = []
    if so['Cancelled'] != 'False':
        diff.append('已取消')
    for a, b, name in ((o['code'], so['Code'], '客户码'), (o['doc_date'], so['DocDate'], '日期'), (o.get('branch_name') or so['BranchName'], so['BranchName'], '分店'),
                       (o.get('doc_ref1', ''), so['DocRef1'].strip(), 'Ref1')):
        if a != b:
            diff.append(f'{name}: 我 {a!r} → 现在 {b!r}')
    a = [(i['item_code'], i['description'].strip()) for i in o['items']]
    b = [(i['ItemCode'], i['Description'].strip()) for i in so['items']]
    if sorted(a) != sorted(b):
        for x in a:
            if x not in b:
                diff.append(f'我的行没了/被改: [{x[0]}] {x[1]}')
        for x in b:
            if x not in a:
                q = next(i for i in so['items'] if (i['ItemCode'], i['Description'].strip()) == x)
                diff.append(f'现在多的行: [{x[0]}] {x[1]} | {q["Qty"]} {q["UOM"]}')
    else:
        for i in o['items']:
            j = next(j for j in so['items'] if (j['ItemCode'], j['Description'].strip()) == (i['item_code'], i['description'].strip()))
            if float(i['qty']) > 0 and abs(float(j['Qty'] or 0) - float(i['qty'])) > 0.0001 and '-v' in sys.argv:
                diff.append(f'数量: [{i["item_code"]}] {i["description"]} 我 {i["qty"]} → 现在 {j["Qty"]}')
    if diff:
        n += 1
        print(f'{doc} {so["DocDate"]} {so["CompanyName"].strip()} [{ref}]')
        for d in diff:
            print('     ' + d)
print(f'和我录的不一样的: {n} 张')
if '-o' in sys.argv:
    print('\n==== 系统里有、不是我录的（10/10 起）====')
    for doc, so in sorted(allso.items(), key=lambda kv: (datekey(kv[1]['DocDate']), kv[0])):
        if doc not in mine and datekey(so['DocDate']) >= '20261010':
            print(f"{doc} {so['DocDate']} {so['Code']} {so['CompanyName'].strip()} | {so['BranchName']} | ref1 {so['DocRef1']} | {'取消' if so['Cancelled'] != 'False' else ''} | " + ' ; '.join(f"[{i['ItemCode']}] {i['Description']} | {i['Qty']} {i['UOM']}" for i in so['items'])[:230])
