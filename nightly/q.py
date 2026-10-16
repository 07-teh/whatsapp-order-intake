# python3 q.py "正则" [n] [品项正则]   查主档客户、分店、历史单（按公司名/分店/地址/客户码）
import sys, json, re
sys.path.insert(0, os.path.dirname(__file__)) if False else None
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
pat = re.compile(sys.argv[1], re.I)
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
ipat = re.compile(sys.argv[3], re.I) if len(sys.argv) > 3 else None
cs = json.load(open('data/master_customers.json', encoding='utf-8-sig'))
bs = json.load(open('data/master_branches.json', encoding='utf-8-sig'))
codes = set()
for c in cs:
    if pat.search(c['Code'] + ' ' + c['CompanyName'] + ' ' + c.get('CompanyName2', '')):
        codes.add(c['Code'])
for b in bs:
    if pat.search(' '.join([b['BranchName'], b['Address1'], b['Address2'], b['Address3'], b['Address4']])):
        codes.add(b['Code'])
cn = {c['Code']: c for c in cs}
_, sos = load()
bycode = collections.defaultdict(list)
for o in sos:
    bycode[o['Code']].append(o)
for o in sos:
    if pat.search(o['CompanyName'] + ' ' + o['BranchName'] + ' ' + o['DAddress1']) and o['Code'] not in codes:
        codes.add(o['Code'])
for code in sorted(codes):
    c = cn.get(code, {})
    print(f"== {code} {c.get('CompanyName','?')} | agent {c.get('Agent')} | status {c.get('Status')} | 历史单 {len(bycode[code])}")
    for b in bs:
        if b['Code'] == code:
            print(f"   分店 [{b['BranchName']}] {b['Address1']} {b['Address2']} {b['Address3']} {b['Address4']}".replace('\r', ' ').replace('\n', ' '))
    hist = sorted(bycode[code], key=lambda o: (datekey(o['DocDate']), o['DocNo']))
    if ipat:
        hist = [o for o in hist if any(ipat.search(i['ItemCode'] + ' ' + i['Description']) for i in o['items'])]
    for o in hist[-n:]:
        print(f"   {o['DocNo']} {o['DocDate']} [{o['CompanyName'].strip()}] 分店 {o['BranchName']} ref1 {o['DocRef1']} agent {o['Agent']}")
        for i in o['items']:
            if not ipat or ipat.search(i['ItemCode'] + ' ' + i['Description']):
                print(f"      [{i['ItemCode']}] {i['Description']} | {i['Qty']} {i['UOM']}")
for o in my_orders():
    if pat.search(o['code'] + ' ' + o['company_name'] + ' ' + o.get('branch_name', '')):
        print(f"   我录的 {o['ref']} {o['doc_date']} {o['code']} {o['company_name']} 分店 {o.get('branch_name')} ref1 {o.get('doc_ref1','')}")
        for i in o['items']:
            print(f"      [{i['item_code']}] {i.get('description')} | {i['qty']} {i.get('uom','')} {('@'+str(i['unit_price'])) if i.get('unit_price') else ''}")
