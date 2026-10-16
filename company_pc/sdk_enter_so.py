"""Enter reviewed orders into SQL Account as Sales Orders through the SQL Account SDK (COM, Windows).

Three modes, from safe to real: check codes only / --preview (fill in memory, do not save) /
--write (save, then read back and verify). A bad order is skipped and listed; the whole batch stops
only if a saved order does not match or it is unclear whether it was saved. entered_log.json makes
reruns idempotent. Comments in this file are in Chinese.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
import traceback
import zipfile
import win32com.client

# =============================================
# SQL ACC SDK Live 录入脚本：把 orders.json 录成 Sales Order
# 先手动打开并登录 SQL ACC，再运行。三种模式，从安全到真正写入：
#   python sdk_enter_so.py orders.json            只检查客户码/货号是否存在（只读）
#   python sdk_enter_so.py orders.json --preview  在内存里填好一张单，打印系统带出的内容，不保存
#   python sdk_enter_so.py orders.json --write    真正保存，保存后读回来核对
# 某张单有问题就跳过它接着录下一张（那张单不会存进系统），最后列出哪些没录。
# 只有"存进去了却对不上"或"这一次不确定存没存进去"才整批停下。有单没录成时退出码是 1。
# 上一次运行留下的"不确定存没存进去"的单，这次不会再录，会列在最后让人去查。
# =============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_JSON = os.path.join(BASE_DIR, 'entered_log.json')   # 已录的单，重跑时跳过，避免重复录
LOCK_FILE = os.path.join(BASE_DIR, 'entering.lock')     # 防止两个脚本同时录
EXPECTED_COMPANY = ''   # 填了就会核对当前账套的公司名里有没有这几个字，例如 'ACME POULTRY'
DOC_DESCRIPTION = 'Sales Order'   # 单头的 Description，手动新增时系统会自动填，SDK 不会
CODE_PATTERN = re.compile(r'^[A-Za-z0-9._/-]+$')
# 货号里有 B+W、E/DUCK-2.4、SC 800G 这种，比客户码多几种字符（引号一律不许，查询是拼字符串的）
ITEM_CODE_PATTERN = re.compile(r'^[A-Za-z0-9 ._/+()&-]+$')
HEADER_FIELDS = ['DocNo', 'DocDate', 'PostDate', 'TaxDate', 'Code', 'CompanyName', 'Description', 'Agent', 'Terms',
                 'DocRef1', 'BranchName', 'DAddress1', 'DAddress2', 'DAddress3', 'DAddress4', 'DocAmt']
DETAIL_FIELDS = ['Seq', 'ItemCode', 'Description', 'Qty', 'UOM', 'UnitPrice', 'Amount', 'DeliveryDate']


class Tee:
    """屏幕上照常显示，同时存一份到文本文件，跑完可以直接把文件带走。存文件出任何问题都不影响录入。"""

    def __init__(self, stream, path):
        self.stream = stream
        try:
            self.file = open(path, 'w', encoding='utf-8')
        except OSError:
            self.file = None

    def write(self, text):
        self.stream.write(text)
        if self.file:
            try:
                self.file.write(text)
                self.file.flush()
            except (OSError, ValueError):
                self.file = None

    def flush(self):
        self.stream.flush()

    def close(self):
        if self.file:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None


def pack_results(stamp, result_txt):
    """把这次的结果和 entered_log.json 打成一个 zip，带回去只要拿这一个文件。"""
    path = os.path.join(BASE_DIR, f'back_{stamp}.zip')
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in (result_txt, LOG_JSON):
                if os.path.exists(name):
                    z.write(name, os.path.basename(name))
        return path
    except OSError as e:
        return f'<打包失败: {e}>'


class OrderError(Exception):
    """这张单没录进去（系统里什么都没留下），可以接着录下一张。"""


class StopBatch(Exception):
    """系统里的状态说不准了，整批停下等人看。"""


# -----------------------------
# 读写配置
# -----------------------------
def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    # 先写临时文件再替换，中途断电也不会把原文件写坏
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def now_text():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def fingerprint(order):
    text = json.dumps(order, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def to_float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


# -----------------------------
# 只读查询
# -----------------------------
def exists(app, table, code):
    ds = app.DBManager.NewDataSet(f"SELECT Code FROM {table} WHERE Code = '{code}'")
    return ds.RecordCount > 0


def max_dockey(app):
    ds = app.DBManager.NewDataSet('SELECT MAX(DocKey) AS MaxKey FROM SL_SO')
    return ds.FindField('MaxKey').AsString


def count_orders(app, order):
    """这个客户在这个送货日已经有几张单。和 max_dockey 一起用来判断一次保存到底存没存进去。"""
    day = parse_date(order['doc_date']).strftime('%Y-%m-%d')
    ds = app.DBManager.NewDataSet(
        f"SELECT COUNT(*) AS N FROM SL_SO WHERE Code = '{order['code'].strip()}' AND DocDate = '{day}'")
    return int(ds.FindField('N').AsString)


def current_company(app):
    try:
        ds = app.DBManager.NewDataSet('SELECT CompanyName FROM SY_PROFILE')
        return ds.FindField('CompanyName').AsString
    except Exception as e:
        return f'<读不到: {e}>'


def read_fields(ds, names):
    row = {}
    for name in names:
        field = ds.FindField(name)
        row[name] = field.AsString if field is not None else '<无此字段>'
    return row


def read_rows(ds, names):
    rows = []
    ds.First()
    while not ds.eof:
        rows.append(read_fields(ds, names))
        ds.Next()
    return rows


# -----------------------------
# 检查 orders.json（不碰系统数据）
# -----------------------------
OLE_DAY_ZERO = datetime.datetime(1899, 12, 30)


def ole_date(day):
    """Delphi/COM 的日期就是"距 1899-12-30 的天数"。直接给天数，不经过时区换算。"""
    return float((day - OLE_DAY_ZERO).days)


def parse_date(text):
    try:
        return datetime.datetime.strptime(str(text).strip(), '%d/%m/%Y')
    except ValueError:
        raise OrderError(f'日期要写成 日/月/年，例如 09/10/2026，现在是: {text!r}')


def check_order(app, order):
    code = str(order.get('code', '')).strip()
    if not CODE_PATTERN.match(code):
        raise OrderError(f'客户码格式不对: {code!r}')
    if not exists(app, 'AR_CUSTOMER', code):
        raise OrderError(f'系统里没有这个客户码: {code}')
    parse_date(order.get('doc_date', ''))
    items = order.get('items', [])
    if not items:
        raise OrderError('没有品项')
    for n, item in enumerate(items, start=1):
        item_code = str(item.get('item_code', '')).strip()
        if not ITEM_CODE_PATTERN.match(item_code):
            raise OrderError(f'第 {n} 行货号格式不对: {item_code!r}')
        if not exists(app, 'ST_ITEM', item_code):
            raise OrderError(f'第 {n} 行系统里没有这个货号: {item_code}')
        qty = to_float(item.get('qty'))
        if qty is None or qty < 0:
            raise OrderError(f'第 {n} 行数量不对: {item.get("qty")!r}')
        if item.get('unit_price') not in (None, ''):
            price = to_float(item.get('unit_price'))
            if price is None or price < 0:
                raise OrderError(f'第 {n} 行单价不对: {item.get("unit_price")!r}')
        if item.get('description') and item.get('desc_suffix'):
            raise OrderError(f'第 {n} 行 description 和 desc_suffix 只能填一个')
        if item.get('uom') and not re.match(r'^[A-Za-z]+$', str(item['uom']).strip()):
            raise OrderError(f'第 {n} 行单位不对: {item["uom"]!r}')


# -----------------------------
# 录入逻辑
# -----------------------------
def fill_order(biz, order):
    """在内存里填好一张新单，返回 (单头, 明细) 两个 dataset。还没保存。"""
    main = biz.DataSets.Find('MainDataSet')
    detail = biz.DataSets.Find('cdsDocDetail')
    doc_date = parse_date(order['doc_date'])

    biz.New()
    main.FindField('Code').AsString = order['code'].strip()
    # 公用的 cash debtor 户口，单上的公司名要改成 "CASH DEBTOR / MEI - 某某店"
    if order.get('company_name_override'):
        main.FindField('CompanyName').AsString = str(order['company_name_override']).strip()
    main.FindField('Description').AsString = DOC_DESCRIPTION
    for name in ('DocDate', 'PostDate', 'TaxDate'):
        field = main.FindField(name)
        if field is not None:
            # 不能直接给 datetime：pywin32 会先换算成 UTC，零点的日期会变成前一天
            field.AsFloat = ole_date(doc_date)
    if order.get('doc_ref1'):
        main.FindField('DocRef1').AsString = str(order['doc_ref1']).strip()
    if order.get('agent'):
        main.FindField('Agent').AsString = str(order['agent']).strip()
    if order.get('branch_name'):
        main.FindField('BranchName').AsString = str(order['branch_name']).strip()

    for n, item in enumerate(order['items'], start=1):
        detail.Append()
        detail.FindField('Seq').Value = n
        detail.FindField('ItemCode').AsString = item['item_code'].strip()
        # 货号填进去后系统会带出品名；要改品名必须在这之后
        if item.get('description'):
            detail.FindField('Description').AsString = str(item['description']).strip()
        elif item.get('desc_suffix'):
            base = detail.FindField('Description').AsString
            detail.FindField('Description').AsString = f"{base} {str(item['desc_suffix']).strip()}"
        # 单位要在数量和单价之前改：个别客户按只数录（单位 NOS）
        if item.get('uom'):
            detail.FindField('UOM').AsString = str(item['uom']).strip().upper()
        detail.FindField('Qty').AsFloat = float(item['qty'])
        if item.get('unit_price') not in (None, ''):
            detail.FindField('UnitPrice').AsFloat = float(item['unit_price'])
        detail.Post()
    return main, detail


def print_order(header, rows):
    for name in HEADER_FIELDS:
        print(f'    {name} = {header.get(name, "")}')
    for row in rows:
        print('    - ' + ' | '.join(f'{name}={row.get(name, "")}' for name in DETAIL_FIELDS))


def compare(order, header, rows):
    """把系统里的一张单和 orders.json 逐项比对，返回对不上的地方。"""
    problems = []
    want_date = parse_date(order['doc_date']).strftime('%d/%m/%Y')
    wanted = [('Code', order['code'].strip()), ('Description', DOC_DESCRIPTION), ('DocDate', want_date),
              ('PostDate', want_date), ('TaxDate', want_date)]
    # company_name 是对照表里记的系统公司名：客户码填错成另一家真实客户时，靠它拦下来
    # d_address1 是分店主档里的送货地址第一行：分店名填进去后地址有没有跟着变，靠它查
    company = order.get('company_name_override') or order.get('company_name')
    if company:
        wanted.append(('CompanyName', str(company).strip()))
    for json_key, field in (('doc_ref1', 'DocRef1'), ('agent', 'Agent'),
                            ('branch_name', 'BranchName'), ('d_address1', 'DAddress1')):
        if order.get(json_key):
            wanted.append((field, str(order[json_key]).strip()))
    for field, want in wanted:
        # 主档里有的公司名、地址结尾带空格或换行，比较时两边都去掉
        if header[field].strip() != want:
            problems.append(f'{field}: 要 {want}，系统里是 {header[field].strip()}')

    if len(rows) != len(order['items']):
        problems.append(f"行数: 要 {len(order['items'])} 行，系统里是 {len(rows)} 行")
    else:
        for n, (item, row) in enumerate(zip(order['items'], rows), start=1):
            if row['ItemCode'] != item['item_code'].strip():
                problems.append(f"第 {n} 行货号: 要 {item['item_code']}，系统里是 {row['ItemCode']}")
            got_qty = to_float(row['Qty'])
            if got_qty is None or abs(got_qty - float(item['qty'])) > 0.0001:
                problems.append(f"第 {n} 行数量: 要 {item['qty']}，系统里是 {row['Qty']!r}")
            if item.get('uom') and row['UOM'] != str(item['uom']).strip().upper():
                problems.append(f"第 {n} 行单位: 要 {item['uom']}，系统里是 {row['UOM']}")
            if item.get('unit_price') not in (None, ''):
                got_price = to_float(row['UnitPrice'])
                if got_price is None or abs(got_price - float(item['unit_price'])) > 0.0001:
                    problems.append(f"第 {n} 行单价: 要 {item['unit_price']}，系统里是 {row['UnitPrice']!r}")
            if item.get('description') and row['Description'] != str(item['description']).strip():
                problems.append(f"第 {n} 行品名: 要 {item['description']}，系统里是 {row['Description']}")
            if item.get('desc_suffix') and not row['Description'].endswith(str(item['desc_suffix']).strip()):
                problems.append(f"第 {n} 行品名: 结尾要有 {item['desc_suffix']}，系统里是 {row['Description']}")
    return problems


def read_saved(app, doc_key):
    """保存后按 DocKey 从数据库把这张单读回来。"""
    key = int(doc_key)
    head_ds = app.DBManager.NewDataSet(f'SELECT * FROM SL_SO WHERE DocKey = {key}')
    if head_ds.RecordCount != 1:
        raise OrderError(f'保存后按 DocKey={key} 读不到这张单')
    rows = read_rows(app.DBManager.NewDataSet(
        f'SELECT * FROM SL_SODTL WHERE DocKey = {key} ORDER BY Seq'), DETAIL_FIELDS)
    return read_fields(head_ds, HEADER_FIELDS), rows


def enter_order(app, order, mode, ref, log):
    biz = app.BizObjects.Find('SL_SO')
    try:
        try:
            main, detail = fill_order(biz, order)
        except OrderError:
            raise
        except Exception as e:
            # 还没保存，Close 之后系统里不会留下东西，所以只算这一张失败
            raise OrderError(f'填单时系统报错: {e}')
        # 保存前先在内存里核对一次，对不上就直接放弃，不会存进系统
        header, rows = read_fields(main, HEADER_FIELDS), read_rows(detail, DETAIL_FIELDS)
        problems = compare(order, header, rows)
        if mode == 'preview' or problems:
            print_order(header, rows)
        if problems:
            raise OrderError('填好的单和 orders.json 对不上，没有保存:\n      ' + '\n      '.join(problems))
        if mode == 'preview':
            print('    预览核对通过（没有保存）')
            return None

        # 保存前先记一笔"正在保存"。万一保存到一半断掉，重跑时会停下来让人确认，不会重复录
        before = (max_dockey(app), count_orders(app, order))
        log[ref] = {'status': 'saving', 'fingerprint': fingerprint(order), 'time': now_text()}
        save_json(LOG_JSON, log)
        try:
            biz.Save()
        except Exception as e:
            try:
                unchanged = (max_dockey(app), count_orders(app, order)) == before
            except Exception:
                unchanged = False
            if unchanged:
                # 最大单号没变、这个客户当天的单数也没变，可以确定这张没存进去
                del log[ref]
                save_json(LOG_JSON, log)
                raise OrderError(f'保存被系统拒绝: {e}')
            raise StopBatch(f'保存时出错，而且系统里的单有变化，不确定是不是这张: {e}')
        # 走到这里就是存进去了。先把"已保存"记下，后面任何一步出错都整批停
        log[ref]['status'] = 'saved'
        save_json(LOG_JSON, log)
        try:
            doc_no = main.FindField('DocNo').AsString
            doc_key = str(int(main.FindField('DocKey').AsString))
        except Exception as e:
            raise StopBatch(f'已经保存，但拿不到单号: {e}')
        log[ref].update(doc_no=doc_no, doc_key=doc_key)
        save_json(LOG_JSON, log)
    finally:
        # 没 Save 就 Close = 放弃这张单，系统里不会留下任何东西。
        # Close 自己出错的话，单据对象的状态就说不准了，不能接着录下一张
        try:
            biz.Close()
        except Exception as e:
            raise StopBatch(f'关闭单据时出错，不确定系统里有没有留下东西: {e}')

    try:
        header, rows = read_saved(app, doc_key)
    except Exception as e:
        raise StopBatch(f'已保存为 {doc_no}，但读不回来核对: {e}')
    problems = compare(order, header, rows)
    if problems:
        print_order(header, rows)
        raise StopBatch(f'已保存为 {doc_no}，但读回来和 orders.json 对不上，请人工检查这张单:\n      '
                        + '\n      '.join(problems))
    log[ref]['company'] = header['CompanyName']
    log[ref]['status'] = 'verified'
    save_json(LOG_JSON, log)
    return log[ref]


def already_done(ref, order, log):
    """这张单之前录过就返回 True；状态不明就抛错，这张不录。"""
    if ref not in log:
        return False
    entry = log[ref]
    if entry.get('fingerprint') != fingerprint(order):
        raise OrderError(f'ref 和之前录过的 {entry.get("doc_no", "一张单")} 相同，但内容不一样。'
                         '改过的单要人工处理，新单要换一个 ref。')
    if entry.get('status') == 'saving':
        raise OrderError('上次保存这张单时中断了，不确定有没有存进去。请到 SQL ACC 里查这个客户当天的单，'
                         f'确认后再手动改 {os.path.basename(LOG_JSON)} 里这个 ref。')
    note = '' if entry.get('status') == 'verified' else '（当时核对没通过，需人工检查）'
    print(f'[跳过] {ref} 之前已录为 {entry.get("doc_no")}{note}')
    return True


def run(orders_json, mode, limit):
    data = load_json(orders_json)
    orders = data if isinstance(data, list) else [data]
    refs = [o.get('ref').strip() if isinstance(o.get('ref'), str) else '' for o in orders]
    if '' in refs or len(set(refs)) != len(refs):
        print('每张单都要有一个不重复的 ref（文字）。')
        return False

    app = win32com.client.Dispatch('SQLAcc.BizApp')
    if not app.IsLogin:
        print('SQL ACC 还没登录，请先手动登录再运行。')
        return False
    company = current_company(app)
    print(f'当前账套公司名: {company}')
    if EXPECTED_COMPANY and EXPECTED_COMPANY.upper() not in company.upper():
        print(f'公司名里没有 {EXPECTED_COMPANY!r}，可能登录错了账套，不继续。')
        return False

    log = load_json(LOG_JSON) if os.path.exists(LOG_JSON) else {}
    done = skipped = 0
    failed = []
    stopped = False
    for order, ref in zip(orders, refs):
        if limit and done >= limit:
            break
        try:
            if mode == 'write' and already_done(ref, order, log):
                skipped += 1
                continue
            print(f'[{ref}] {order.get("code", "")} {order.get("company_name", "")} {order.get("doc_date", "")}')
            check_order(app, order)
            if mode == 'check':
                print('    检查通过')
            else:
                result = enter_order(app, order, mode, ref, log)
                if result:
                    print(f'    已保存并核对: {result["doc_no"]}')
            done += 1
        except OrderError as e:
            print(f'    [没录] {e}')
            failed.append((ref, str(e).split('\n')[0]))
        except Exception as e:
            print(f'    [整批停下] {e}')
            print(f'在 {ref} 停下，后面的单没有处理。')
            failed.append((ref, str(e).split('\n')[0]))
            stopped = True
            break

    print('=' * 60)
    print(f'模式 {mode}：通过 {done} 张，之前已录跳过 {skipped} 张，有问题 {len(failed)} 张'
          + ('，整批中途停下' if stopped else ''))
    for ref, reason in failed:
        print(f'  {ref}: {reason}')
    return not failed and not stopped


def main():
    parser = argparse.ArgumentParser(description='用 SDK Live 把 orders.json 录成 Sales Order')
    parser.add_argument('orders_json', nargs='?', default='orders.json', help='订单 JSON 文件')
    parser.add_argument('--preview', action='store_true', help='填好但不保存，打印系统带出的内容')
    parser.add_argument('--write', action='store_true', help='真正保存')
    parser.add_argument('--limit', type=int, default=0, help='最多处理几张单，0 = 不限')
    args = parser.parse_args()
    if args.preview and args.write:
        parser.error('--preview 和 --write 只能选一个')
    mode = 'write' if args.write else 'preview' if args.preview else 'check'

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    result_txt = os.path.join(BASE_DIR, f'result_{mode}_{stamp}.txt')
    console = sys.stdout
    sys.stdout = Tee(console, result_txt)
    try:
        ok = run_locked(args.orders_json, mode, args.limit)
    except Exception:
        # 例如订单文件读不了、SQL ACC 连不上：把出错信息也存进结果文件，照样打包带回去
        print('[出错] 脚本没能跑完:')
        traceback.print_exc(file=sys.stdout)
        ok = False
    finally:
        sys.stdout.close()
        sys.stdout = console
    print(f'这次的输出已存到: {result_txt}')
    print(f'要带回去的文件: {pack_results(stamp, result_txt)}')
    return ok


def run_locked(orders_json, mode, limit):
    if mode != 'write':
        return run(orders_json, mode, limit)
    try:
        os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        print(f'已经有一个录入在跑（或上次异常退出）。确认没有在跑之后，删掉 {LOCK_FILE} 再试。')
        return False
    try:
        return run(orders_json, mode, limit)
    finally:
        os.remove(LOCK_FILE)


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
