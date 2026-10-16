"""Shared loaders for the human-in-the-loop review helpers in nightly/ (messages, Sales Orders,
alias table, previously entered orders). Comments in this file are in Chinese.
"""
import sys, os, json, re, csv, glob, collections
sys.path.insert(0, 'tools')
import learn_aliases as LA
import learn_items as LI

def load():
    ms = json.load(open('data/wa_messages.json', encoding='utf-8'))
    sos = LA.load_sales_orders()
    return ms, sos

def datekey(d):
    return d[6:] + d[3:5] + d[:2]

def aliases():
    rows = list(csv.DictReader(open('mapping/customer_aliases.csv', encoding='utf-8')))
    t = collections.defaultdict(list)
    for r in rows:
        k = LA.norm(r['alias_line1']) + ('|' + LA.norm(r['alias_line2']) if r['alias_line2'] else '')
        t[k].append(r)
    return rows, t

def my_orders():
    out = []
    for f in sorted(glob.glob('output/orders_2*.json')):
        for o in json.load(open(f, encoding='utf-8')):
            out.append(o)
    return out

def samples(ms, sos):
    """(客户码, 写法, 冻) -> [(时间, 货号, 录成的品名, 消息行)]"""
    byid = {m['id']: m for m in ms}
    bydoc = {o['DocNo']: o for o in sos}
    pairs = json.load(open('data/pairs.json'))
    out = collections.defaultdict(list)
    gen = collections.defaultdict(list)
    for p in pairs:
        if p['how'] != 'exact' or p['message_id'] not in byid or p['doc_no'] not in bydoc:
            continue
        m, so = byid[p['message_id']], bydoc[p['doc_no']]
        lines = [(raw, LI.item_line(raw)) for raw in m['text'].split('\n')]
        lines = [(raw, x) for raw, x in lines if x]
        sl = [(it, LI.so_line_sig(it)) for it in so['items']]
        lc = collections.Counter(x[2] for _, x in lines)
        sc = collections.Counter(s for _, s in sl if s)
        for raw, (alias, frozen, sig) in lines:
            if lc[sig] == 1 and sc[sig] == 1:
                it = next(i for i, s in sl if s == sig)
                rec = (m['time'], it['ItemCode'], it['Description'], LA.clean(raw), it['Qty'], it['UOM'])
                out[(so['Code'], alias, frozen)].append(rec)
                gen[(alias, frozen)].append(rec)
    return out, gen
