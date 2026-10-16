"""Knowledge base for the LLM prompt: candidate customers, branches, recent orders, item hints.

Everything is cut at a point in time: when replaying a past night, only messages, pairs and
Sales Orders from before that night are visible, so evaluation never peeks at the answer.
Candidate customers are scored from exact first-line matches and IDF-weighted word overlap with
past order headers and master-data names/addresses. Inactive customers and items are never offered.
Comments in this file are in Chinese.
"""
import collections
import csv
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import learn_aliases as LA  # noqa: E402
import learn_items as LI  # noqa: E402

# =============================================
# 给 DeepSeek 准备上下文：每条订单消息配上候选客户、分店、最近的单、每行品项以前怎么录。
# 所有资料都按"截止时间"切：回放过去某一晚时，只能看到那一晚之前的消息、配对和单，避免偷看答案。
# =============================================

MASTER_ITEMS_CSV = os.path.join('mapping', 'master_items.csv')
STOP = set('sdn bhd the and plt m restaurant restoran cafe food foods kitchen enterprise management holding '
           'chicken fresh frozen keras delivery deliver jalan jln taman order orders tomorrow besok esok '
           'please pls thank thanks for to of at by on in no lot'.split())
CANDIDATES = 6


def words(text):
    return {w for w in re.findall(r'[a-z0-9]{3,}', text.lower()) if w not in STOP and not w.isdigit()}


def datekey(d):
    """dd/mm/yyyy → yyyymmdd，排序用。"""
    return d[6:] + d[3:5] + d[:2]


def so_number(doc_no):
    m = re.search(r'(\d+)$', doc_no)
    return int(m.group(1)) if m else 0


def head_lines(message, n=5):
    """消息开头不是品项的几行：名字、分店、日期、地址。"""
    out = []
    for raw in message['text'].split('\n'):
        line = LA.clean(raw)
        if not line or LI.item_line(raw):
            continue
        # 日期行（Mei 的第一行）、Delivery 这类标记行不是名字
        if LA.DATE.match(line) and len(line) <= 30:
            continue
        letters = re.sub(r'[^A-Za-z ]', '', line).strip()
        if not letters or LA.MARKER.match(letters):
            continue
        out.append(line)
        if len(out) >= n:
            break
    return out


class KB:
    def __init__(self, cutoff, so_before=None, night=None):
        """cutoff: 'YYYY-MM-DD HH:MM:SS'，只用这之前的消息。
        so_before: 'SO-71492' 这类，只用编号比它小的历史单（回放时用当晚第一张单号）。
        night: 'YYYYMMDD'，只用比这晚早的 output/orders_*.json（Claude 录的单）。"""
        self.cutoff = cutoff
        with open(LA.MESSAGES_JSON, encoding='utf-8') as f:
            self.messages = json.load(f)
        self.byid = {m['id']: m for m in self.messages}
        sos = LA.load_sales_orders()
        if so_before:
            limit = so_number(so_before)
            sos = [o for o in sos if so_number(o['DocNo']) < limit]
        self.sos = sos
        bydoc = {o['DocNo']: o for o in sos}

        with open(os.path.join('data', 'master_customers.json'), encoding='utf-8-sig') as f:
            self.customers = {c['Code']: c for c in json.load(f)}
        self.branches = collections.defaultdict(list)
        with open(os.path.join('data', 'master_branches.json'), encoding='utf-8-sig') as f:
            for b in json.load(f):
                self.branches[b['Code']].append(b)
        self.name_words = {}        # 客户码 → 公司名、分店名、地址里的词
        for code, c in self.customers.items():
            names = words(c['CompanyName'] + ' ' + c.get('CompanyName2', ''))
            for b in self.branches.get(code, []):
                names |= words(' '.join([b['BranchName'], b['Address1'], b['Address2'], b['Address3']]))
            self.name_words[code] = names
        with open(MASTER_ITEMS_CSV, encoding='utf-8') as f:
            rows = list(csv.DictReader(f, delimiter='|', quoting=csv.QUOTE_NONE))
        # 停用的货号、停用的客户在 SQL Account 开单画面里选不到，一律不给模型
        self.items = {r['item_code']: r['system_description'] for r in rows if r.get('active', 'A') == 'A'}

        # 历史单：按客户分，含 Claude 前几晚录的
        self.history = collections.defaultdict(list)
        for o in sos:
            self.history[o['Code']].append({
                'doc': o['DocNo'], 'date': o['DocDate'], 'branch': o['BranchName'], 'ref1': o['DocRef1'],
                'company': o['CompanyName'].strip(),
                'items': [(i['ItemCode'], i['Description'], i['Qty'], i['UOM']) for i in o['items']]})
        # Claude 录过的单：系统里对应的那张 SO 不再重复列（SO 里 pcs 行的数量早上会被改成称重后的重量，Claude 版才是录入时的样子）
        entered = {}
        for path in glob.glob(os.path.join('data', 'entered', '*', 'entered_log.json')):
            with open(path, encoding='utf-8') as f:
                entered.update({ref: e.get('doc_no') for ref, e in json.load(f).items()})
        self.mine = []
        mine_docs = set()
        for path in sorted(glob.glob(os.path.join('output', 'orders_2*.json'))):
            day = re.search(r'orders_(\d{8})', path).group(1)
            if night and day >= night:
                continue
            with open(path, encoding='utf-8') as f:
                for o in json.load(f):
                    self.mine.append(o)
                    if entered.get(o['ref']):
                        mine_docs.add(entered[o['ref']])
                    self.history[o['code']].append({
                        'doc': 'Claude ' + o['ref'], 'date': o['doc_date'], 'branch': o.get('branch_name', ''),
                        'ref1': o.get('doc_ref1', ''), 'company': o.get('company_name_override') or o['company_name'],
                        'items': [(i['item_code'], i['description'], i['qty'], i.get('uom', ''))
                                  for i in o['items']]})
        for code in self.history:
            self.history[code] = [h for h in self.history[code] if h['doc'] not in mine_docs]
            self.history[code].sort(key=lambda h: (datekey(h['date']), h['doc']))

        # 单位：每个货号历史上用过哪些（UNIT 不算，见 final.py 的说明）
        self.uoms = collections.defaultdict(collections.Counter)
        for o in sos:
            for i in o['items']:
                if i['UOM'] in ('KG', 'NOS'):
                    self.uoms[i['ItemCode']][i['UOM']] += 1
        # 品名写法：数字换成 #
        self.desc_shapes = collections.defaultdict(set)
        for o in sos:
            for i in o['items']:
                self.desc_shapes[i['ItemCode']].add(shape(i['Description']))

        # 名字证据：消息开头几行 → 录成的客户和分店（配对表 + Claude 录的单），只取截止前的消息
        pairs = []
        with open(LA.PAIRS_JSON, encoding='utf-8') as f:
            for p in json.load(f):
                m = self.byid.get(p['message_id'])
                if m and m['time'] < cutoff and p['doc_no'] in bydoc:
                    pairs.append(p)
        self.evidence = []          # (时间, 第一行 norm, 词集合, 客户码, 分店, 来源)
        for p in pairs:
            m, so = self.byid[p['message_id']], bydoc[p['doc_no']]
            self._add_evidence(m, so['Code'], so['BranchName'], 'pair-' + p['how'])
        by5 = collections.defaultdict(list)
        for m in self.messages:
            by5[m['id'][-5:]].append(m)
        for o in self.mine:
            day, _, id5 = o['ref'].split('-')[:3]
            for m in by5[id5]:
                if m['time'][:10].replace('-', '') == day and m['time'] < cutoff:
                    self._add_evidence(m, o['code'], o.get('branch_name', ''), 'claude')
        self.first_line = collections.defaultdict(collections.Counter)
        self.word_df = collections.Counter()
        for t, key, ws, code, branch, src in self.evidence:
            self.first_line[key][(code, branch)] += 1
            self.word_df.update(ws)

        # 品项：写法 → 货号（只用截止前、配得上的消息）
        samples = list(LI.collect(pairs, self.byid, bydoc))
        self.item_general, self.item_overrides = LI.build(samples)
        self.item_samples = collections.defaultdict(list)    # (客户码, 写法, 冻) → [(时间, 货号, 品名, 原文)]
        self.item_any = collections.defaultdict(list)        # (写法, 冻) → [...]
        for p in pairs:
            if p['how'] != 'exact':
                continue
            m = self.byid[p['message_id']]
            so = bydoc[p['doc_no']]
            for s in LI.collect([p], self.byid, bydoc):
                rec = (m['time'][:10], s['code'], s['entered_as'], s['line'])
                self.item_samples[(so['Code'],) + s['key']].append(rec)
                self.item_any[s['key']].append(rec)

    def _add_evidence(self, m, code, branch, src):
        if m['type'] != 'text':
            return
        heads = head_lines(m, 3)
        if not heads:
            return
        self.evidence.append((m['time'], LA.norm(heads[0]), words(' '.join(heads)), code, branch, src))

    # ---------- 候选客户 ----------
    def candidates(self, message, extra_text=''):
        """extra_text：附件抽出来的文字（PDF 抬头、送货地址），也拿来配客户。"""
        heads = head_lines(message, 5)
        text = ' '.join(heads) + ' ' + message.get('caption', '') + ' ' + extra_text[:1500]
        ws = words(text)
        score = collections.Counter()
        why = collections.defaultdict(list)
        if heads:
            exact = self.first_line.get(LA.norm(heads[0]))
            if exact:
                for (code, branch), n in exact.items():
                    score[code] += 10 + min(n, 10)
                    why[code].append(f'第一行一模一样的写法以前录成这家（分店 {branch}）{n} 次')
        n_docs = max(len(self.evidence), 1)

        def idf(w):
            return 1.0 / (1 + self.word_df.get(w, 0) / n_docs * 50)

        best = collections.defaultdict(float)
        for t, key, ews, code, branch, src in self.evidence:
            common = ws & ews
            if common:
                s = sum(idf(w) for w in common)
                if s > best[code]:
                    best[code] = s
        for code, s in best.items():
            score[code] += s * 3
        for code, names in self.name_words.items():
            common = ws & names
            if common:
                score[code] += sum(idf(w) for w in common) * 3
        out = []
        for code, s in score.most_common():
            if len(out) >= CANDIDATES:
                break
            if code not in self.customers or self.customers[code].get('Status') != 'A':
                continue
            out.append({'code': code, 'score': round(s, 2), 'why': why.get(code, [])})
        return out

    def describe_customer(self, code, n_hist=3):
        c = self.customers[code]
        hist = self.history.get(code, [])
        used = collections.Counter(h['branch'] for h in hist[-20:])
        branches = sorted(self.branches.get(code, []), key=lambda b: -used.get(b['BranchName'], 0))[:15]
        lines = [f"客户 {code} | {c['CompanyName'].strip()}" + (f" / {c['CompanyName2'].strip()}" if c.get('CompanyName2', '').strip() else '')
                 + f" | agent {c.get('Agent', '')} | 状态 {'启用' if c.get('Status') == 'A' else '停用(' + str(c.get('Status')) + ')'}"
                 + f" | 历史单 {len(hist)} 张"]
        for b in branches:
            addr = ' '.join(x for x in (b['Address1'], b['Address2'], b['Address3']) if x).replace('\r', ' ').replace('\n', ' ')
            lines.append(f"  - 分店名 = {b['BranchName']}    （地址: {addr[:90]}）" + (f"（最近20张里用过 {used[b['BranchName']]} 次）" if used.get(b['BranchName']) else ''))
        if len(self.branches.get(code, [])) > len(branches):
            lines.append(f"  （还有 {len(self.branches[code]) - len(branches)} 个分店没列出）")
        for h in hist[-n_hist:]:
            lines.append(f"  最近的单 {h['doc']} 日期 {h['date']} 公司名 {h['company']} 分店 [{h['branch']}] Ref1 {h['ref1'] or '-'}")
            for ic, desc, qty, uom in h['items']:
                # 件数单：系统里的重量是第二天称完补的，录单当时填 0。照录单时的样子给模型看，免得它以为要填重量
                if uom != 'NOS' and re.search(r'\d+\s*(PCS|NOS)\b', desc.upper()) and not h['doc'].startswith('Claude'):
                    qty, uom = 0, f'{uom}（录单时填 0）'
                lines.append(f"     [{ic}] {desc} | {qty} {uom}")
        return '\n'.join(lines)

    def item_hints(self, message, codes):
        out = []
        for raw in message['text'].split('\n'):
            x = LI.item_line(raw)
            if not x:
                continue
            alias, frozen, sig = x
            parts = [f"行 {LA.clean(raw)!r}（写法 {alias!r}{'，冻' if frozen else ''}）"]
            for code in codes[:2]:
                g = LI.lookup(self.item_general, self.item_overrides, code, (alias, frozen))
                own = sorted(self.item_samples.get((code, alias, frozen), []))[-2:]
                for t, ic, desc, line in own:
                    parts.append(f"   {code} 这家 {t} 录成 [{ic}] {desc}  ← 原文 {line!r}")
                if g and not own:
                    parts.append(f"   通用写法 → [{g}]")
            general = collections.Counter(ic for t, ic, d, l in self.item_any.get((alias, frozen), []))
            if general:
                ex = sorted(self.item_any[(alias, frozen)])[-1]
                parts.append(f"   所有客户这个写法录成 {dict(general.most_common(3))}，例 {ex[2]!r}")
            out.append('\n'.join(parts))
        return '\n'.join(out)

    def item_table(self):
        lines = []
        for code, desc in self.items.items():
            u = self.uoms.get(code)
            lines.append(f"{code} | {desc}" + (f" | 常用单位 {u.most_common(1)[0][0]}" if u else ''))
        return '\n'.join(lines)


def shape(desc):
    return re.sub(r'\d+', '#', re.sub(r'\s+', ' ', desc.strip()))
