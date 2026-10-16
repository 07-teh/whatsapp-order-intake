"""Replay evaluation: rerun past nights with only the data available at the time and compare
field by field (customer, branch, date, Ref1, items) against the human-verified orders.
Reports precision of released orders, coverage, and which fields were wrong.
Comments in this file are in Chinese.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb import so_number  # noqa: E402

# =============================================
# 回放评估：拿过去几晚 Claude 录的单（已录进系统、核对过）当答案，让 DeepSeek 只用当晚之前的资料重做一遍，逐字段比。
#   python3 pipeline/eval.py 20261011 20261012 20261013          # 调参用
#   python3 pipeline/eval.py 20261014                            # 留作最后检验，调参时不要看它
#   python3 pipeline/eval.py --compare-only 20261015             # 只比对 output/ds/<night> 已有的结果（影子模式第二天用）
# 写 output/ds/eval_<夜>.txt
# =============================================

FIRST_SO = {'20261009': 'SO-71492'}      # 第一晚还没有 entered_log 存档
FIRST_START = '2026-10-08 22:00:00'


def gt_orders(night):
    out = []
    for path in sorted(glob.glob(os.path.join('output', f'orders_{night}*.json'))):
        if re.search(rf'orders_{night}b?\.json$', path):
            with open(path, encoding='utf-8') as f:
                out += json.load(f)
    return out


def nights():
    return sorted({re.search(r'orders_(\d{8})', p).group(1) for p in glob.glob(os.path.join('output', 'orders_2*.json'))})


def last_time(night, by5):
    times = []
    for o in gt_orders(night):
        day, _, id5 = o['ref'].split('-')[:3]
        m = by5.get((id5, day))
        if m:
            times.append(m['time'])
    if not times:
        raise SystemExit(f'{night} 的答案单在 data/wa_messages.json 里一条消息都对不上，没法定回放窗口')
    return max(times)


def window(night, by5):
    all_n = nights()
    i = all_n.index(night)
    start = last_time(all_n[i - 1], by5) if i else FIRST_START
    end = last_time(night, by5)
    log = os.path.join('data', 'entered', night, 'entered_log.json')
    so_before = FIRST_SO.get(night)
    if os.path.exists(log):
        with open(log, encoding='utf-8') as f:
            docs = [e['doc_no'] for e in json.load(f).values() if e.get('doc_no')]
        so_before = min(docs, key=so_number)
    return start, end, so_before


def norm_desc(d):
    return re.sub(r'\s+', ' ', (d or '').upper()).strip()


def item_key(i, with_uom):
    q = float(i.get('qty') or 0)
    price = i.get('unit_price')
    return (i['item_code'], norm_desc(i['description']), round(q, 3), i.get('uom', '') if with_uom else '',
            round(float(price), 2) if price not in (None, '') else None)


def compare(night, out_root):
    gt = {o['ref']: o for o in gt_orders(night)}
    path = os.path.join(out_root, night, 'orders_ds.json')
    with open(path, encoding='utf-8') as f:
        ds = {o['ref']: o for o in json.load(f)}
    with open(os.path.join(out_root, night, 'raw.json'), encoding='utf-8') as f:
        raw = json.load(f)
    held = {h['ref']: h['why'] for h in raw['held']}
    held_base = {r.split('-STAFF')[0] for r in held}

    stats = collections.Counter()
    errs = collections.Counter()
    lines = []
    for ref, g in gt.items():
        stats['答案'] += 1
        d = ds.get(ref)
        if not d:
            base = ref.split('-STAFF')[0]
            if base in raw.get('manual', []):
                stats['附件'] += 1
                lines.append(f'附件 {ref}（PDF/图片，原型阶段交人工）')
            elif ref in held or base in held_base:
                stats['扣下'] += 1
                lines.append(f'扣下 {ref}: ' + '；'.join(held.get(ref) or held.get(base) or ['(同一条消息的另一张被扣)'])[:200])
            else:
                stats['漏了'] += 1
                lines.append(f'漏了 {ref} {g["code"]} {g["company_name"].strip()}')
            continue
        stats['出了'] += 1
        bad = []
        if d['code'] != g['code']:
            bad.append(f"客户 {d['code']} ≠ {g['code']}")
        if (d.get('branch_name') or '') != (g.get('branch_name') or ''):
            bad.append(f"分店 {d.get('branch_name')!r} ≠ {g.get('branch_name')!r}")
        if d['doc_date'] != g['doc_date']:
            bad.append(f"日期 {d['doc_date']} ≠ {g['doc_date']}")
        if (d.get('doc_ref1') or '') != (g.get('doc_ref1') or ''):
            bad.append(f"Ref1 {d.get('doc_ref1')!r} ≠ {g.get('doc_ref1')!r}")
        if (d.get('company_name_override') or '') != (g.get('company_name_override') or ''):
            bad.append('公司名改写不同')
        with_uom = all('uom' in i for i in g['items'])
        gi = collections.Counter(item_key(i, with_uom) for i in g['items'])
        di = collections.Counter(item_key(i, with_uom) for i in d['items'])
        if gi != di:
            bad.append('品项: 多了 ' + '; '.join(map(str, (di - gi).elements())) + ' | 少了 ' + '; '.join(map(str, (gi - di).elements())))
        for b in bad:
            errs[b.split(' ')[0].rstrip(':')] += 1
        if bad:
            stats['出了但有错'] += 1
            lines.append(f'错 {ref}: ' + ' || '.join(bad))
        else:
            stats['全对'] += 1
    extra = [r for r in ds if r not in gt]
    for r in extra:
        lines.append(f'多出 {r} {ds[r]["code"]} {ds[r]["company_name"].strip()}（答案里没有这张：可能是当日单、改单，或 DeepSeek 多开）')
    stats['多出'] = len(extra)
    head = (f"== {night}  答案 {stats['答案']} 张 | DeepSeek 出了 {stats['出了']}（全对 {stats['全对']}，有错 {stats['出了但有错']}）"
            f" | 扣下 {stats['扣下']} | 附件 {stats['附件']} | 漏了 {stats['漏了']} | 多出 {stats['多出']}")
    precision = stats['全对'] / stats['出了'] if stats['出了'] else 0
    head += f"\n   放行的单正确率 {precision:.1%}   覆盖率 {stats['出了'] / max(stats['答案'], 1):.1%}   错的字段 {dict(errs)}"
    report = head + '\n' + '\n'.join('   ' + x for x in lines)
    with open(os.path.join(out_root, f'eval_{night}.txt'), 'w', encoding='utf-8') as f:
        f.write(report + '\n')
    return stats, report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('nights', nargs='+')
    p.add_argument('--compare-only', action='store_true')
    p.add_argument('--out-root', default=os.path.join('output', 'ds', 'eval'))
    p.add_argument('--limit', type=int)
    a = p.parse_args()
    import run_night
    with open(os.path.join('data', 'wa_messages.json'), encoding='utf-8') as f:
        by5 = {(m['id'][-5:], m['time'][:10].replace('-', '')): m for m in json.load(f)}
    total = collections.Counter()
    for night in a.nights:
        if not a.compare_only:
            start, end, so_before = window(night, by5)
            print(f'回放 {night}: 消息 {start} 之后到 {end}，历史单只用 {so_before} 之前')
            run_night.run(night, start, end, so_before=so_before, limit=a.limit, out_root=a.out_root)
        stats, report = compare(night, a.out_root)
        print(report.split('\n   ', 2)[0] + '\n' + report.split('\n')[1])
        total += stats
    if len(a.nights) > 1:
        print(f"== 合计 答案 {total['答案']} | 出了 {total['出了']}（全对 {total['全对']}）| 扣下 {total['扣下']} | 附件 {total['附件']} | 漏了 {total['漏了']} | 多出 {total['多出']}"
              f" | 放行正确率 {total['全对'] / max(total['出了'], 1):.1%}")


if __name__ == '__main__':
    main()
