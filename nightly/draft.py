# python3 draft.py "起始时间" > review.txt    给每条新订单消息出一张"核对用"的草稿
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
START = sys.argv[1]
ms, sos = load()
rows, table = aliases()
smp, gen = samples(ms, sos)
mine = my_orders()
bycode = collections.defaultdict(list)
for o in sos:
    bycode[o['Code']].append(o)
general, overrides = LI.build(LI.collect(json.load(open('data/pairs.json')), {m['id']: m for m in ms}, {o['DocNo']: o for o in sos}))
n = 0
for m in ms:
    if m['time'] <= START or m['type'] != 'text' or m['reply_to']:
        continue
    if re.match(r'^\W*(got o|—)', m['text'], re.I):
        continue
    n += 1
    print(f"\n######## {n}. [{m['chat'][:3]} {m['time'][5:16]} {m['id'][-5:]}]")
    print('   ' + ' / '.join(l.strip() for l in m['text'].split('\n') if l.strip()))
    po = LA.parse_order(m)
    cands = []
    if po:
        k1, k2 = LA.alias_keys(po)
        cands = table.get(k2, []) or table.get(k1, [])
        if not cands:
            cands = [r for k, rs in table.items() if k.split('|')[0] == k1 for r in rs]
        print(f"   日期 {po['date']} 名字行 {po['names']}")
    else:
        print('   !! parse_order 认不出')
    for r in cands[:6]:
        print(f"   ALIAS → {r['customer_code']} {r['system_company_name']} | 分店 {r['branch_name']} | 见过 {r['times_seen']} 次 {r['agreement']} 最后 {r['last_seen']} {r['check']} | 第二行 {r['alias_line2']!r}")
    code = cands[0]['customer_code'] if cands else None
    if code:
        hist = sorted(bycode[code], key=lambda o: (datekey(o['DocDate']), o['DocNo']))[-2:]
        for o in hist:
            print(f"   H {o['DocNo']} {o['DocDate']} 分店 {o['BranchName']} ref1 {o['DocRef1']}: " + ' ; '.join(f"[{i['ItemCode']}] {i['Description']} | {i['Qty']} {i['UOM']}" for i in o['items']))
        for o in [o for o in mine if o['code'] == code][-2:]:
            print(f"   M {o['ref']} {o['doc_date']} 分店 {o.get('branch_name')} ref1 {o.get('doc_ref1','')}: " + ' ; '.join(f"[{i['item_code']}] {i['description']} | {i['qty']} {i.get('uom','')}" for i in o['items']))
    for raw in m['text'].split('\n'):
        x = LI.item_line(raw)
        if not x:
            continue
        alias, frozen, sig = x
        g = LI.lookup(general, overrides, code, (alias, frozen))
        print(f"   L {LA.clean(raw)!r} → {alias!r} {'冻' if frozen else ''} {sig} → {g}")
        own = sorted(smp.get((code, alias, frozen), []))[-2:]
        for t, ic, d, line, q, u in own:
            print(f"        这家 {t[:10]} [{ic}] {d} | {q} {u}   ← {line}")
        if not own:
            other = sorted(smp.get((code, alias, not frozen), []))[-1:]
            for t, ic, d, line, q, u in other:
                print(f"        这家(冻鲜相反) {t[:10]} [{ic}] {d} | {q} {u}   ← {line}")
            gg = collections.Counter((ic, ) for t, ic, d, line, q, u in gen.get((alias, frozen), []))
            if gg:
                print(f"        别家 {dict(gg.most_common(3))}  例: {sorted(gen[(alias, frozen)])[-1][2]}")
