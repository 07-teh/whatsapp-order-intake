# python3 look.py "消息正则" [n] [第二个正则(也要匹配)]  看以前含这个写法的消息，以及它配上的那张单怎么录的
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
pat = re.compile(sys.argv[1], re.I | re.S)
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
pat2 = re.compile(sys.argv[3], re.I | re.S) if len(sys.argv) > 3 else None
ms, sos = load()
bydoc = {o['DocNo']: o for o in sos}
pairs = {p['message_id']: p for p in json.load(open('data/pairs.json'))}
hits = [m for m in ms if m['type'] == 'text' and pat.search(m['text']) and (not pat2 or pat2.search(m['text'])) and m['time'] < '2026-10-15 08:56']
for m in hits[-n:]:
    print(f"--- {m['chat'][:3]} {m['time'][:16]} {m['id'][-5:]} {'(回复)' if m['reply_to'] else ''}")
    print('   ' + ' / '.join(l.strip() for l in m['text'].split('\n') if l.strip())[:600])
    p = pairs.get(m['id'])
    if p and p['doc_no'] in bydoc:
        o = bydoc[p['doc_no']]
        print(f"   => {o['DocNo']} {o['DocDate']} {o['Code']} {o['CompanyName'].strip()} 分店 {o['BranchName']} ref1 {o['DocRef1']} ({p['how']}) 地址 {o['DAddress1']}")
        for i in o['items']:
            print(f"        [{i['ItemCode']}] {i['Description']} | {i['Qty']} {i['UOM']}")
