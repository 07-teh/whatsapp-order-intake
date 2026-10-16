"""Generate the SYNTHETIC demo workspace (demo/workspace/).

Every customer, product, address, message and order produced here is invented. The generator
imitates the *patterns* of the real chats so the real pipeline code has something realistic to
chew on:

- three sales chats, each salesperson with their own header layout (name first / date first /
  "Delivery:" line), outlet on the second line for multi-branch customers;
- English, Malay and a little Chinese in the same message (kepak = wing, peha = leg, ekor = head count);
- units mixed freely: kg, ctn (12 kg), pkt (2 kg), pcs / ekor (head count, entered as qty 0);
- a customer that always means a *different* item code by the same word (per-customer override);
- a shared CASH DEBTOR account, an inactive customer, a customer with no history, PO numbers;
- chatter ("got order", "noted") between orders.

Output (all under demo/workspace/):
  data/wa_messages.json, data/so_history/so_history_demo.json,
  data/master_customers.json, data/master_branches.json, mapping/master_items.csv,
  output/orders_20261015.json   <- the human-verified answer for the demo night (used by eval)
"""
import csv
import datetime
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(HERE, 'workspace')
NIGHT = '20261015'
HIST_FROM = datetime.date(2026, 9, 1)
HIST_TO = datetime.date(2026, 10, 14)          # last delivery date in history
CHATS = {'A': 'Amin - Ordering Group', 'M': 'Mei - Ordering Group', 'O': 'Office Ordering Group'}

# code, description, active
ITEMS = [
    ('CHK-WHOLE', 'WHOLE CHICKEN CLEAN + PROCESS', 'A'),
    ('CHK-WING', 'CHIC WING', 'A'),
    ('CHK-WING-MID', 'CHIC MID WING', 'A'),
    ('CHK-BL', 'CHIC BONELESS LEG', 'A'),
    ('CHK-BL-CUBE', 'CHIC BONELESS LEG CUBE', 'A'),
    ('CHK-BREAST', 'CHIC BREAST FILLET', 'A'),
    ('CHK-WLEG', 'CHIC WHOLE LEG', 'A'),
    ('CHK-DRUM', 'CHIC DRUMSTICK', 'A'),
    ('CHK-FEET', 'CHIC FEET', 'A'),
    ('CHK-MINCE', 'CHIC MINCE', 'A'),
    ('DUCK-WHOLE', 'WHOLE DUCK', 'A'),
    ('E/DUCK-WHOLE', 'EDEN DUCK WHOLE', 'A'),
    ('CHK-THIGH', 'CHIC THIGH (OLD CODE)', 'I'),
]
# how each item is written into the Sales Order description
DESC = {
    'CHK-WHOLE': 'WHOLE CHICKEN CLEAN + PROCESS (1.8-2.0KG)',
    'CHK-WING': 'CHIC WING (90-100G)',
    'CHK-WING-MID': 'CHIC MID WING',
    'CHK-BL': 'CHIC BONELESS LEG (240-260G)',
    'CHK-BL-CUBE': 'CHIC BONELESS LEG CUBE (2CM)',
    'CHK-BREAST': 'CHIC BREAST FILLET',
    'CHK-WLEG': 'CHIC WHOLE LEG (230-250G)',
    'CHK-DRUM': 'CHIC DRUMSTICK',
    'CHK-FEET': 'CHIC FEET',
    'CHK-MINCE': 'CHIC MINCE',
    'DUCK-WHOLE': 'WHOLE DUCK (2.2-2.5KG)',
    'E/DUCK-WHOLE': 'EDEN DUCK WHOLE (2.4KG UP)',
}

# unit: kg = weight (kg / ctn / pkt in the chat), pcs = head count entered as qty 0 with "- nPCS",
#       nos = head count entered as qty n, UOM NOS
WING = (['Wing', 'Chicken wing', 'wings'], 'CHK-WING', 'kg')
BL = (['Boneless leg', 'BL', 'boneless leg'], 'CHK-BL', 'kg')
CUSTOMERS = [
    dict(code='300-G0001', company='GOLDEN SPOON CAFE SDN BHD', agent='AMIN', chat='A', style='date_first',
         branches=[('BILLING', 'NO 12, JALAN AMPANG HILIR'), ('MELAWATI', 'NO 3, JALAN MELAWATI 2'),
                   ('WANGSA MAJU', 'NO 8, JALAN WANGSA 1/4')],
         heads={'MELAWATI': ['Golden Spoon', 'Melawati'], 'WANGSA MAJU': ['Golden Spoon', 'Wangsa Maju']},
         items=[WING, BL], weight=5),
    dict(code='300-K0002', company='KEDAI MAKAN AMINAH', agent='AMIN', chat='A', style='name_first',
         branches=[], heads={'': ['Kedai Makan Aminah']},
         items=[(['Kepak', 'kepak ayam'], 'CHK-WING', 'kg'), (['Peha', 'peha ayam'], 'CHK-WLEG', 'pcs')], weight=4),
    dict(code='300-H0003', company='HAPPY WOK RESTAURANT', agent='MEI', chat='M', style='name_date',
         branches=[('BILLING', 'NO 21, JALAN PUDU LAMA')], heads={'BILLING': ['Happy Wok 快乐镬']},
         items=[(['Drumstick', 'drum stick'], 'CHK-DRUM', 'kg'), WING], weight=4),
    dict(code='300-B0004', company='BURGER STATION ENTERPRISE', agent='MEI', chat='M', style='delivery_line',
         branches=[('SETAPAK', 'NO 5, JALAN GENTING KELANG'), ('KEPONG', 'NO 40, JALAN KEPONG BARU')],
         heads={'SETAPAK': ['Burger Station Setapak'], 'KEPONG': ['Burger Station Kepong']},
         items=[(['Breast fillet', 'Fillet'], 'CHK-BREAST', 'kg'), (['Mince', 'minced chicken'], 'CHK-MINCE', 'kg'), WING],
         weight=4),
    dict(code='300-N0005', company='NASI KANDAR SRI MAJU', agent='AMIN', chat='A', style='name_date',
         branches=[], heads={'': ['Sri Maju NK']},
         items=[(['Ayam', 'ayam bersih'], 'CHK-WHOLE', 'nos'), (['Kaki ayam', 'Feet'], 'CHK-FEET', 'kg')], weight=4),
    dict(code='300-T0006', company='TOMYAM HOUSE (M) SDN BHD', agent='MEI', chat='M', style='name_date',
         branches=[('BILLING', 'NO 9, JALAN SS2/24')], heads={'BILLING': ['Tomyam House']},
         items=[(['Drumstick'], 'CHK-DRUM', 'kg'), (['Feet', 'chicken feet'], 'CHK-FEET', 'kg')], weight=3),
    # always means the cubed item when it writes "boneless leg" -> per-customer override
    dict(code='300-P0007', company='SAFFRON BISTRO SDN BHD', agent='MEI', chat='M', style='delivery_line',
         branches=[('BANGSAR', 'NO 2, JALAN TELAWI 3'), ('MONT KIARA', 'NO 1, JALAN KIARA 1')],
         heads={'BANGSAR': ['Saffron Bistro Bangsar'], 'MONT KIARA': ['Saffron Bistro Mont Kiara']},
         items=[(['Boneless leg'], 'CHK-BL-CUBE', 'kg'), (['Breast fillet'], 'CHK-BREAST', 'kg')], weight=4),
    dict(code='300-C0008', company='CASH DEBTOR', agent='AMIN', chat='A', style='name_date',
         branches=[], heads={'': ['Kedai Pak Lah (cash)']}, override='CASH DEBTOR / AMIN - KEDAI PAK LAH',
         items=[WING, (['Drumstick'], 'CHK-DRUM', 'kg')], weight=2),
    dict(code='300-D0009', company='WONG KEE DUCK RICE', agent='MEI', chat='M', style='name_date',
         branches=[], heads={'': ['Wong Kee Duck']},
         items=[(['Eden duck', 'Eden'], 'E/DUCK-WHOLE', 'nos'), (['Duck', 'itik'], 'DUCK-WHOLE', 'nos')], weight=3),
    dict(code='300-S0010', company='SUNRISE CATERING SDN BHD', agent='OFFICE', chat='O', style='po',
         branches=[('BILLING', 'LOT 7, JALAN PERUSAHAAN 2, BATU CAVES')], heads={'BILLING': ['SUNRISE CATERING SDN BHD']},
         items=[(['Whole leg'], 'CHK-WLEG', 'pcs'), (['Mid wing'], 'CHK-WING-MID', 'kg')], weight=3),
    dict(code='300-R0013', company='RASA KITA CAFE', agent='MEI', chat='M', style='name_date',
         branches=[('TTDI', 'NO 11, JALAN TUN MOHD FUAD 3')], heads={'TTDI': ['Rasa Kita TTDI']},
         items=[BL, (['Breast'], 'CHK-BREAST', 'kg')], weight=3),
]
EXTRA_MASTER = [   # in the customer master but never in the history
    dict(code='300-F0012', company='FRESHMART MINI MARKET', agent='AMIN', status='I', branches=[]),
    dict(code='300-Y0014', company='CRISPY CORNER SDN BHD', agent='OFFICE', status='A',
         branches=[('BILLING', 'NO 6, JALAN SETIAWANGSA 9')]),
]
CHATTER = ['Got order 👍', 'noted', 'ok tq', '———']


class Gen:
    def __init__(self, seed=42):
        self.r = random.Random(seed)
        self.messages, self.sos = [], []
        self.doc_no = 50000

    def mid(self):
        return '%032X' % self.r.getrandbits(128)

    def add_msg(self, chat, time, text, reply_to=None, mid=None):
        m = {'id': mid or self.mid(), 'chat': CHATS[chat], 'time': time, 'sender': f'sales-{chat}', 'type': 'text',
             'text': text, 'file': '', 'reply_to': reply_to, 'reply_found': True if reply_to else None}
        self.messages.append(m)
        return m

    def qty_text(self, unit, alias):
        """returns (text written in chat, Sales Order line fields)."""
        r = self.r
        if unit == 'kg':
            kind = r.choice(['kg', 'kg', 'ctn', 'pkt'])
            if kind == 'kg':
                n = r.choice([3, 4, 5, 6, 8, 10, 15, 20])
                return f'{alias} {n}kg', n, 'KG', ''
            if kind == 'ctn':
                n = r.choice([1, 2, 3])
                return f'{alias} {n} ctn', n * 12, 'KG', ''
            n = r.choice([2, 3, 5])
            return f'{alias} {n}pkt', n * 2, 'KG', ''
        n = r.choice([10, 20, 30, 40, 50, 100])
        if unit == 'pcs':
            return f'{alias} {n}pcs', round(n * 0.24, 2), 'KG', f' - {n}PCS'   # weight filled next morning
        return f'{alias} {n} ekor' if r.random() < 0.6 else f'{alias} {n}nos', n, 'NOS', ''

    def header(self, c, branch, date):
        heads = c['heads'][branch]
        dm = f'{date.day}/{date.month}'
        style = c['style']
        if style == 'date_first':
            return [dm] + heads
        if style == 'delivery_line':
            return heads + [f'Delivery: {dm}']
        if style == 'po':
            return heads + [f'PO: PO-2026-{self.r.randint(100, 999):04d}', f'Delivery {dm}']
        return heads + [dm]

    def order(self, c, date, time, n_items=None):
        branch = self.r.choice(list(c['heads']))
        lines = self.header(c, branch, date)
        so_items = []
        picks = self.r.sample(c['items'], n_items or self.r.randint(1, len(c['items'])))
        for aliases, code, unit in picks:
            text, qty, uom, suffix = self.qty_text(unit, self.r.choice(aliases))
            lines.append(text)
            so_items.append({'Seq': len(so_items) + 1, 'ItemCode': code, 'Description': DESC[code] + suffix,
                             'Qty': str(qty), 'UOM': uom})
        m = self.add_msg(c['chat'], time, '\n'.join(lines))
        self.doc_no += 1
        ref1 = next((l.split(': ')[1] for l in lines if l.startswith('PO: ')), '')
        addr = dict(c['branches']).get(branch, '')
        self.sos.append({'DocKey': str(self.doc_no), 'DocNo': f'SO-{self.doc_no}', 'DocDate': date.strftime('%d/%m/%Y'),
                         'Code': c['code'], 'CompanyName': c.get('override') or c['company'], 'Agent': c['agent'],
                         'DocRef1': ref1, 'BranchName': branch, 'DAddress1': addr, 'DAddress2': '', 'DAddress3': '',
                         'DAddress4': '', 'Cancelled': 'False', 'items': so_items})
        return m

    def history(self):
        day = HIST_FROM
        while day <= HIST_TO:
            sent = day - datetime.timedelta(days=1)
            todays = self._pick(6)
            for c in todays:
                t = f'{sent} {self.r.randint(9, 21):02d}:{self.r.randint(0, 59):02d}:{self.r.randint(0, 59):02d}'
                self.order(c, day, t)
                if self.r.random() < 0.25:
                    self.add_msg(c['chat'], t[:-2] + '59', self.r.choice(CHATTER))
            day += datetime.timedelta(days=1)

    def _pick(self, n):
        pool = [c for c in CUSTOMERS for _ in range(c['weight'])]
        out = []
        while len(out) < n:
            c = self.r.choice(pool)
            if c not in out:
                out.append(c)
        return out


def demo_night(g):
    """The batch that the demo runs on: 2026-10-14 22:00 -> 2026-10-15 22:00, delivery 16/10.
    Returns the human-verified answer (what was finally entered after review)."""
    by = {c['code']: c for c in CUSTOMERS}
    answer = []

    def ans(m, code, items, branch='', ref1='', override='', staff=False, suffix=''):
        day = m['time'][:10].replace('-', '')
        o = {'ref': f"{day}-{m['chat'][:3]}-{m['id'][-5:]}{suffix}", 'code': code,
             'company_name': next((c['company'] for c in CUSTOMERS + EXTRA_MASTER if c['code'] == code)),
             'doc_date': '16/10/2026', 'items': items}
        if branch:
            o['branch_name'] = branch
        if ref1 or staff:
            o['doc_ref1'] = ref1 or 'STAFF'
        if override:
            o['company_name_override'] = override
        answer.append(o)
        return o

    def it(code, qty, uom='KG', suffix='', price=None):
        x = {'item_code': code, 'description': DESC[code] + suffix, 'qty': qty, 'uom': uom}
        if price is not None:
            x['unit_price'] = price
        return x

    m1 = g.add_msg('A', '2026-10-14 22:41:07', '16/10\nGolden Spoon\nMelawati\nWing 10kg\nBoneless leg 2 ctn')
    gs = ans(m1, '300-G0001', [it('CHK-WING', 10), it('CHK-BL', 24)], branch='MELAWATI')
    # same-day order posted after midnight: belongs to the early-morning shift, not this batch
    g.add_msg('M', '2026-10-15 05:32:40', 'Tomyam House\n15/10\nFeet 5kg\n*urgent pagi ni*')
    m = g.add_msg('A', '2026-10-15 08:12:55', 'Kedai Makan Aminah\nesok\nKepak 5kg\nPeha ayam 40 ekor')
    ans(m, '300-K0002', [it('CHK-WING', 5), it('CHK-WLEG', 0, suffix=' - 40PCS')])
    g.add_msg('A', '2026-10-15 08:13:30', 'Got order 👍')
    m = g.add_msg('M', '2026-10-15 09:05:12', 'Saffron Bistro Bangsar\nDelivery: 16/10\nBoneless leg 8kg')
    ans(m, '300-P0007', [it('CHK-BL-CUBE', 8)], branch='BANGSAR')
    m = g.add_msg('M', '2026-10-15 10:20:03', 'Burger Station Kepong\nDelivery: 16/10\nBreast fillet 3 ctn\nMince 5kg\n\n'
                  'Staff meal (separate invoice):\nWing 2kg')
    ans(m, '300-B0004', [it('CHK-BREAST', 36), it('CHK-MINCE', 5)], branch='KEPONG')
    ans(m, '300-B0004', [it('CHK-WING', 2)], branch='KEPONG', staff=True, suffix='-STAFF')
    m = g.add_msg('M', '2026-10-15 11:47:51', 'Happy Wok 快乐镬\n16/10\nDrumstick 10kg @ 9.80\nWing 5kg (RM11.50)')
    ans(m, '300-H0003', [it('CHK-DRUM', 10, price=9.8), it('CHK-WING', 5, price=11.5)], branch='BILLING')
    g.add_msg('A', '2026-10-15 12:30:18', 'Add wing 3kg', reply_to=m1['id'])
    gs['items'][0]['qty'] += 3
    m = g.add_msg('M', '2026-10-15 14:02:44', 'Wong Kee Duck\n16/10\nDuck 10 nos')
    ans(m, '300-D0009', [it('E/DUCK-WHOLE', 10, uom='NOS')])          # sales confirmed "Eden" when asked
    m = g.add_msg('O', '2026-10-15 15:10:09', 'CRISPY CORNER SDN BHD\nDelivery: 16/10\nWing 10kg\nDrumstick 5kg')
    ans(m, '300-Y0014', [it('CHK-WING', 10), it('CHK-DRUM', 5)], branch='BILLING')
    m = g.add_msg('A', '2026-10-15 16:25:37', 'sri maju nk\n16/10\nAyam 20 ekor\nkaki ayam 3pkt')
    ans(m, '300-N0005', [it('CHK-WHOLE', 20, uom='NOS'), it('CHK-FEET', 6)])
    m = g.add_msg('O', '2026-10-15 17:58:21', 'SUNRISE CATERING SDN BHD\nPO: PO-2026-0412\nDelivery 16/10\nWhole leg 100pcs\nMid wing 2 ctn')
    ans(m, '300-S0010', [it('CHK-WLEG', 0, suffix=' - 100PCS'), it('CHK-WING-MID', 24)], branch='BILLING', ref1='PO-2026-0412')
    m = g.add_msg('A', '2026-10-15 19:40:02', 'Kedai Pak Lah (cash)\n16/10\nWing 5kg')
    ans(m, '300-C0008', [it('CHK-WING', 5)], override=by['300-C0008']['override'])
    m = g.add_msg('M', '2026-10-15 21:15:46', 'Rasa Kita TTDI\n16/10\nBoneless leg 6kg\n鸡胸 Breast 4kg')
    ans(m, '300-R0013', [it('CHK-BL', 6), it('CHK-BREAST', 4)], branch='TTDI')
    g.add_msg('M', '2026-10-15 21:16:10', 'noted')
    return answer


def write(path, obj):
    path = os.path.join(WS, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def main():
    g = Gen()
    g.history()
    answer = demo_night(g)
    g.messages.sort(key=lambda m: m['time'])
    write('data/wa_messages.json', g.messages)
    write('data/so_history/so_history_demo.json', g.sos)
    customers, branches = [], []
    for c in CUSTOMERS + EXTRA_MASTER:
        customers.append({'Code': c['code'], 'CompanyName': c['company'], 'CompanyName2': '', 'Agent': c['agent'],
                          'Status': c.get('status', 'A')})
        for name, addr in c['branches']:
            branches.append({'Code': c['code'], 'BranchType': 'B', 'BranchName': name, 'Address1': addr,
                             'Address2': 'KUALA LUMPUR', 'Address3': '', 'Address4': ''})
    write('data/master_customers.json', customers)
    write('data/master_branches.json', branches)
    write(f'output/orders_{NIGHT}.json', answer)
    os.makedirs(os.path.join(WS, 'mapping'), exist_ok=True)
    with open(os.path.join(WS, 'mapping', 'master_items.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='|', quoting=csv.QUOTE_NONE)
        w.writerow(['item_code', 'system_description', 'active'])
        w.writerows(ITEMS)
    night = [m for m in g.messages if m['time'] > '2026-10-14 22:00:00']
    print(f'synthetic workspace: {len(g.messages) - len(night)} history messages, {len(g.sos)} Sales Orders, '
          f'{len(night)} messages in the demo night, {len(answer)} expected orders')


if __name__ == '__main__':
    main()
