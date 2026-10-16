"""Amend already-entered Sales Orders (date, quantities/prices, remove or add lines) through the SDK.

Only touches an order that still matches its recorded "before" state; orders edited by someone
else, cancelled, past delivery, or already converted to DO/invoice are left for a human.
Same three modes as sdk_enter_so.py. Comments in this file are in Chinese.
"""
import argparse
import datetime
import os
import sys
import traceback
import win32com.client
import sdk_enter_so as so

# =============================================
# SQL ACC SDK Live 改单脚本：改已经录进去的 Sales Order（改日期、改数量和单价、删行、加行）
# 要和 sdk_enter_so.py 放在同一个文件夹，读单、核对用的是它里面的函数。
# 先手动打开并登录 SQL ACC，再运行。三种模式，从安全到真正写入：
#   python sdk_amend_so.py amend.json            只读：看系统里这张单现在的样子、能不能改
#   python sdk_amend_so.py amend.json --preview  在内存里改好，打印改完的样子，不保存
#   python sdk_amend_so.py amend.json --write    真正保存，保存后读回来核对
# 只改"现在和 before 一模一样"的单。别人动过的、已取消的、送货日已到的、已经转成 DO/发票的，
# 一律不动，留给人处理。
# amend.json 里每张单：
#   ref / doc_no / doc_key / code   这次改单的编号、要改的单号、它的 DocKey、客户码
#   before  {doc_date, items}       改之前这张单应该是什么样（当初录进去的内容；可以再带 doc_ref1、branch_name）
#   doc_date                        改完之后的日期（不改日期就不写）
#   items                           改完之后整张单的全部品项，每行都要写完整的 description
# 品项按"货号 + 品名"认行：两边都有的行留着（数量、单价、单位不一样就改），before 有而 items 没有的删掉，
# items 里新出现的加在最后。
# =============================================

LOG_JSON = os.path.join(so.BASE_DIR, 'amend_log.json')   # 已改的单，重跑时跳过
# 改完之后这些单头字段必须和改之前一样
KEEP_HEADER_FIELDS = ['DocNo', 'Code', 'CompanyName', 'Description', 'Agent', 'Terms', 'DocRef1', 'BranchName',
                      'DAddress1', 'DAddress2', 'DAddress3', 'DAddress4']
NOT_CANCELLED = ('', 'false', 'f', '0')
TRANSFER_TABLES = ('SL_DODTL', 'SL_IVDTL')   # SO 转成 DO / 发票后，这两张表的明细会指回 SO


def item_key(item_code, description):
    return str(item_code).strip(), str(description).strip()


def json_key(item):
    return item_key(item.get('item_code', ''), item.get('description', ''))


def same_number(a, b):
    a, b = so.to_float(a), so.to_float(b)
    return a is not None and b is not None and abs(a - b) <= 0.0001


# -----------------------------
# 检查 amend.json（不碰系统数据）
# -----------------------------
def check_items(items, where):
    if not isinstance(items, list) or not items:
        raise so.OrderError(f'{where} 没有品项')
    for n, item in enumerate(items, start=1):
        item_code = str(item.get('item_code', '')).strip()
        if not so.ITEM_CODE_PATTERN.match(item_code):
            raise so.OrderError(f'{where} 第 {n} 行货号格式不对: {item_code!r}')
        if not str(item.get('description', '')).strip():
            raise so.OrderError(f'{where} 第 {n} 行没写 description（改单要写完整品名）')
        qty = so.to_float(item.get('qty'))
        if qty is None or qty < 0:
            raise so.OrderError(f'{where} 第 {n} 行数量不对: {item.get("qty")!r}')
        if item.get('unit_price') not in (None, ''):
            price = so.to_float(item.get('unit_price'))
            if price is None or price < 0:
                raise so.OrderError(f'{where} 第 {n} 行单价不对: {item.get("unit_price")!r}')
        if item.get('uom') and not str(item['uom']).strip().isalpha():
            raise so.OrderError(f'{where} 第 {n} 行单位不对: {item["uom"]!r}')
    keys = [json_key(item) for item in items]
    if len(set(keys)) != len(keys):
        raise so.OrderError(f'{where} 里有两行货号和品名完全一样，脚本分不清是哪一行，要人工改')


def check_amend(app, amend):
    if not str(amend.get('doc_no', '')).strip():
        raise so.OrderError('没写 doc_no')
    try:
        int(str(amend.get('doc_key', '')).strip())
    except ValueError:
        raise so.OrderError(f'doc_key 不对: {amend.get("doc_key")!r}')
    code = str(amend.get('code', '')).strip()
    if not so.CODE_PATTERN.match(code):
        raise so.OrderError(f'客户码格式不对: {code!r}')
    before = amend.get('before')
    if not isinstance(before, dict):
        raise so.OrderError('没写 before（改之前这张单的样子）')
    so.parse_date(before.get('doc_date', ''))
    if amend.get('doc_date'):
        so.parse_date(amend['doc_date'])
    check_items(before.get('items'), 'before')
    check_items(amend.get('items'), 'items')
    for n, item in enumerate(amend['items'], start=1):
        if not so.exists(app, 'ST_ITEM', item['item_code'].strip()):
            raise so.OrderError(f'items 第 {n} 行系统里没有这个货号: {item["item_code"]}')


# -----------------------------
# 读系统里现在的单，判断能不能改
# -----------------------------
def differences(header, rows, doc_date_text, items):
    """系统里的单和一份 {日期, 品项} 逐项比，返回不一样的地方。品项不看顺序。"""
    problems = []
    want_date = so.parse_date(doc_date_text).strftime('%d/%m/%Y')
    if header['DocDate'] != want_date:
        problems.append(f"日期: 要 {want_date}，系统里是 {header['DocDate']}")
    wanted = {json_key(item): item for item in items}
    seen = set()
    for row in rows:
        key = item_key(row['ItemCode'], row['Description'])
        if key in seen:
            problems.append(f'系统里有两行一样的: [{key[0]}] {key[1]}')
        seen.add(key)
        item = wanted.get(key)
        if item is None:
            problems.append(f"系统里多了一行: [{key[0]}] {key[1]} | {row['Qty']} {row['UOM']}")
            continue
        if not same_number(row['Qty'], item['qty']):
            problems.append(f"[{key[0]}] {key[1]} 数量: 要 {item['qty']}，系统里是 {row['Qty']}")
        if item.get('unit_price') not in (None, '') and not same_number(row['UnitPrice'], item['unit_price']):
            problems.append(f"[{key[0]}] {key[1]} 单价: 要 {item['unit_price']}，系统里是 {row['UnitPrice']}")
        if item.get('uom') and row['UOM'].strip().upper() != str(item['uom']).strip().upper():
            problems.append(f"[{key[0]}] {key[1]} 单位: 要 {item['uom']}，系统里是 {row['UOM']}")
    for key in wanted:
        if key not in seen:
            problems.append(f'系统里少了一行: [{key[0]}] {key[1]}')
    return problems


def is_cancelled(app, key):
    ds = app.DBManager.NewDataSet(f'SELECT Cancelled FROM SL_SO WHERE DocKey = {key}')
    field = ds.FindField('Cancelled')
    return field is not None and field.AsString.strip().lower() not in NOT_CANCELLED


def transfer_count(app, key):
    """这张 SO 有几行已经转成 DO / 发票。查不了就返回 None。"""
    total = 0
    for table in TRANSFER_TABLES:
        try:
            ds = app.DBManager.NewDataSet(
                f"SELECT COUNT(*) AS N FROM {table} WHERE FromDocType = 'SO' AND FromDocKey = {key}")
            total += int(ds.FindField('N').AsString)
        except Exception:
            return None
    return total


def snapshot_changes(before_header, before_rows, header, rows):
    """两次从数据库读出来的同一张单逐字段比，返回变了的地方。用来确认"没保存"真的什么都没留下。"""
    changes = [f'{name}: {before_header[name]} → {header[name]}' for name in so.HEADER_FIELDS
               if header[name] != before_header[name]]
    if len(rows) != len(before_rows):
        changes.append(f'行数: {len(before_rows)} → {len(rows)}')
    else:
        for n, (old, new) in enumerate(zip(before_rows, rows), start=1):
            changes += [f'第 {n} 行 {name}: {old[name]} → {new[name]}' for name in so.DETAIL_FIELDS
                        if new[name] != old[name]]
    return changes


def target_date(amend):
    return amend.get('doc_date') or amend['before']['doc_date']


def read_current(app, amend):
    """返回 (单头, 明细, 状态)。状态是 'ready'（可以改）或 'done'（已经是改好的样子）；不能改就抛 OrderError。"""
    key = int(str(amend['doc_key']).strip())
    header, rows = so.read_saved(app, key)
    doc_no = str(amend['doc_no']).strip()
    if header['DocNo'].strip() != doc_no:
        raise so.OrderError(f"DocKey={key} 在系统里是 {header['DocNo']}，不是 {doc_no}，没动")
    if header['Code'].strip() != amend['code'].strip():
        raise so.OrderError(f"{doc_no} 的客户码是 {header['Code']}，不是 {amend['code']}，没动")
    if is_cancelled(app, key):
        raise so.OrderError(f'{doc_no} 已经取消了，没动')
    if not differences(header, rows, target_date(amend), amend['items']):
        return header, rows, 'done'
    changed = differences(header, rows, amend['before']['doc_date'], amend['before']['items'])
    for json_name, field in (('doc_ref1', 'DocRef1'), ('branch_name', 'BranchName')):
        if json_name in amend['before'] and header[field].strip() != str(amend['before'][json_name]).strip():
            changed.append(f"{field}: 当初是 {amend['before'][json_name]}，系统里是 {header[field].strip()}")
    for row in rows:
        # 当初录的时候每一行的送货日都等于单头日期；不一样就是有人单独改过某一行
        if row['DeliveryDate'] not in (header['DocDate'], '<无此字段>'):
            changed.append(f"[{row['ItemCode']}] {row['Description']} 的 DeliveryDate 是 {row['DeliveryDate']}，和单头日期不一样")
    if changed:
        raise so.OrderError(f'{doc_no} 现在的内容和当初录的不一样（有人改过），没动，要人工处理:\n      '
                            + '\n      '.join(changed))
    if so.parse_date(header['DocDate']).date() <= datetime.date.today():
        raise so.OrderError(f"{doc_no} 的送货日 {header['DocDate']} 已经到了，可能已经出货，没动，要人工处理")
    transferred = transfer_count(app, key)
    if transferred:
        raise so.OrderError(f'{doc_no} 已经有 {transferred} 行转成 DO / 发票，没动，要人工处理')
    if transferred is None:
        print('    （查不了这张单有没有转成 DO / 发票，靠"送货日还没到"这一条把关）')
    return header, rows, 'ready'


# -----------------------------
# 改单逻辑
# -----------------------------
def make_plan(rows, amend):
    """对着系统里现在的明细，算出要删的行、要留的行、要加的行，以及改完之后每一行应该是什么样。"""
    wanted = {json_key(item): item for item in amend['items']}
    current = {item_key(row['ItemCode'], row['Description']): row for row in rows}
    remove = [key for key in current if key not in wanted]
    keep = [key for key in current if key in wanted]
    add = [item for item in amend['items'] if json_key(item) not in current]
    final_items = []
    for key in keep:
        item, row = dict(wanted[key]), current[key]
        # 没写单价、单位的行，改完应该和改之前一样：改日期、改数量时系统要是偷偷重算了，靠这个查出来
        if item.get('unit_price') in (None, ''):
            item['unit_price'] = so.to_float(row['UnitPrice'])
        if not item.get('uom'):
            item['uom'] = row['UOM'].strip()
        final_items.append(item)
    return remove, keep, add, final_items + add


def locate(detail, key):
    """把明细的光标移到这一行上。每次都从头找，不依赖删行、改行之后光标停在哪。"""
    detail.First()
    while not detail.eof:
        if item_key(detail.FindField('ItemCode').AsString, detail.FindField('Description').AsString) == key:
            return
        detail.Next()
    raise so.OrderError(f'打开的单里找不到这一行: [{key[0]}] {key[1]}')


def set_item_values(detail, item):
    # 单位要在数量和单价之前改（和录新单一样）
    if item.get('uom'):
        detail.FindField('UOM').AsString = str(item['uom']).strip().upper()
    detail.FindField('Qty').AsFloat = float(item['qty'])
    if item.get('unit_price') not in (None, ''):
        detail.FindField('UnitPrice').AsFloat = float(item['unit_price'])


def apply_changes(biz, amend, header, rows):
    """打开这张单，在内存里改好，返回 (单头, 明细, 改完应有的品项)。还没保存。"""
    main = biz.DataSets.Find('MainDataSet')
    detail = biz.DataSets.Find('cdsDocDetail')
    biz.Params.Find('DocKey').Value = int(str(amend['doc_key']).strip())
    biz.Open()
    if main.FindField('DocNo').AsString.strip() != str(amend['doc_no']).strip():
        raise so.OrderError(f"按 DocKey 打开的是 {main.FindField('DocNo').AsString}，不是 {amend['doc_no']}")
    # 从刚才读库到现在打开，中间可能有人改过：打开之后、动手之前再比一次
    opened = differences(so.read_fields(main, so.HEADER_FIELDS), so.read_rows(detail, so.DETAIL_FIELDS),
                         amend['before']['doc_date'], amend['before']['items'])
    if opened:
        raise so.OrderError('打开之后发现这张单和刚才读到的不一样（有人刚改过），没动:\n      ' + '\n      '.join(opened))
    remove, keep, add, final_items = make_plan(rows, amend)
    wanted = {json_key(item): item for item in amend['items']}
    new_date = so.parse_date(target_date(amend))
    date_changed = new_date.strftime('%d/%m/%Y') != header['DocDate']

    biz.Edit()
    main.Edit()
    if date_changed:
        for name in ('DocDate', 'PostDate', 'TaxDate'):
            field = main.FindField(name)
            if field is not None:
                # 不能直接给 datetime：pywin32 会先换算成 UTC，零点的日期会变成前一天
                field.AsFloat = so.ole_date(new_date)
    for key in remove:
        locate(detail, key)
        detail.Delete()
    for key in keep:
        locate(detail, key)
        detail.Edit()
        set_item_values(detail, wanted[key])
        if date_changed and detail.FindField('DeliveryDate') is not None:
            detail.FindField('DeliveryDate').AsFloat = so.ole_date(new_date)
        detail.Post()
    last_seq = max([int(so.to_float(row['Seq']) or 0) for row in rows] or [0])
    for n, item in enumerate(add, start=1):
        detail.Append()
        detail.FindField('Seq').Value = last_seq + n
        detail.FindField('ItemCode').AsString = item['item_code'].strip()
        # 货号填进去后系统会带出品名；要改品名必须在这之后
        detail.FindField('Description').AsString = str(item['description']).strip()
        set_item_values(detail, item)
        detail.Post()
    return main, detail, final_items


def compare_after(amend, before_header, final_items, header, rows):
    """改完的单（内存里的，或保存后读回来的）和应有的样子比，返回对不上的地方。"""
    order = {'code': amend['code'], 'doc_date': target_date(amend), 'items': final_items}
    problems = so.compare(order, header, rows)
    for name in KEEP_HEADER_FIELDS:
        if header[name].strip() != before_header[name].strip():
            problems.append(f'{name} 不该变: 原来是 {before_header[name].strip()}，现在是 {header[name].strip()}')
    want_date = so.parse_date(target_date(amend)).strftime('%d/%m/%Y')
    total = 0.0
    for n, row in enumerate(rows, start=1):
        if row['DeliveryDate'] not in (want_date, '<无此字段>'):
            problems.append(f"第 {n} 行 DeliveryDate: 要 {want_date}，系统里是 {row['DeliveryDate']}")
        # 数量、单价改了之后金额要跟着重算：这些单没有折扣和税，行金额 = 数量 × 单价，单头金额 = 各行相加
        qty, price, amount = (so.to_float(row[name]) for name in ('Qty', 'UnitPrice', 'Amount'))
        if None in (qty, price, amount) or abs(qty * price - amount) > 0.011:
            problems.append(f"第 {n} 行金额没跟着算: 数量 {row['Qty']} × 单价 {row['UnitPrice']}，金额却是 {row['Amount']}")
        total += amount or 0.0
    doc_amt = so.to_float(header['DocAmt'])
    if doc_amt is None or abs(doc_amt - total) > 0.011:
        problems.append(f"单头金额 DocAmt 是 {header['DocAmt']}，各行加起来是 {round(total, 2)}")
    return problems


def amend_order(app, amend, mode, ref, log):
    doc_no = str(amend['doc_no']).strip()
    before_header, before_rows, state = read_current(app, amend)
    if state == 'done':
        print(f'    {doc_no} 已经是改好的样子，不用再改')
        return 'done'
    print('    现在:')
    so.print_order(before_header, before_rows)
    if mode == 'check':
        print('    可以改（这个模式只看不改）')
        return 'ready'

    biz = app.BizObjects.Find('SL_SO')
    save_error = None
    write_started = False
    try:
        try:
            main, detail, final_items = apply_changes(biz, amend, before_header, before_rows)
        except so.OrderError:
            raise
        except Exception as e:
            # 还没保存，Close 之后这张单还是原样，所以只算这一张失败
            raise so.OrderError(f'改单时系统报错（没有保存）: {e}')
        header, rows = so.read_fields(main, so.HEADER_FIELDS), so.read_rows(detail, so.DETAIL_FIELDS)
        problems = compare_after(amend, before_header, final_items, header, rows)
        print('    改完:')
        so.print_order(header, rows)
        if problems:
            raise so.OrderError('改好的单和 amend.json 对不上，没有保存:\n      ' + '\n      '.join(problems))
        if mode == 'write':
            # 保存前先记一笔"正在保存"。万一保存到一半断掉，重跑时会停下来让人确认
            write_started = True
            log[ref] = {'status': 'saving', 'doc_no': doc_no, 'time': so.now_text()}
            so.save_json(LOG_JSON, log)
            try:
                biz.Save()
            except Exception as e:
                save_error = e
    finally:
        # 没 Save 就 Close = 放弃这次改动，系统里的单还是原样。
        # Close 自己出错的话，单据对象的状态就说不准了，不能接着改下一张
        try:
            biz.Close()
        except Exception as e:
            raise so.StopBatch(f'关闭单据时出错，不确定 {doc_no} 有没有被改: {e}')

    try:
        header, rows = so.read_saved(app, int(str(amend['doc_key']).strip()))
    except Exception as e:
        raise so.StopBatch(f'{doc_no} 改完后读不回来核对: {e}')
    if not write_started:
        # 预览：打开、改、不保存就关掉。读回来逐字段比，确认系统里的单一点没变
        changes = snapshot_changes(before_header, before_rows, header, rows)
        if changes:
            raise so.StopBatch(f'预览没有保存，但 {doc_no} 读回来变了，请人工检查:\n      ' + '\n      '.join(changes))
        print('    预览核对通过（没有保存，读回来确认单没变）')
        return 'ready'
    if save_error is not None:
        # 保存报错后这张单到底变没变说不准（log 里留着"正在保存"），整批停下让人看
        print('    保存报错后读回来:')
        so.print_order(header, rows)
        changes = snapshot_changes(before_header, before_rows, header, rows)
        raise so.StopBatch(f'保存 {doc_no} 时出错: {save_error}\n      读回来'
                           + ('和改之前一样' if not changes else '已经变了: ' + '；'.join(changes))
                           + f'。请到 SQL ACC 里看这张单，确认后再手动改 {os.path.basename(LOG_JSON)} 里这个 ref。')
    problems = compare_after(amend, before_header, final_items, header, rows)
    if problems:
        so.print_order(header, rows)
        raise so.StopBatch(f'{doc_no} 已保存，但读回来和 amend.json 对不上，请人工检查这张单:\n      '
                           + '\n      '.join(problems))
    log[ref].update(status='verified', time=so.now_text())
    so.save_json(LOG_JSON, log)
    return 'saved'


def run(amend_json, mode):
    data = so.load_json(amend_json)
    amends = data if isinstance(data, list) else [data]
    refs = [a.get('ref').strip() if isinstance(a.get('ref'), str) else '' for a in amends]
    if '' in refs or len(set(refs)) != len(refs):
        print('每张要改的单都要有一个不重复的 ref（文字）。')
        return False

    app = win32com.client.Dispatch('SQLAcc.BizApp')
    if not app.IsLogin:
        print('SQL ACC 还没登录，请先手动登录再运行。')
        return False
    company = so.current_company(app)
    print(f'当前账套公司名: {company}')
    if so.EXPECTED_COMPANY and so.EXPECTED_COMPANY.upper() not in company.upper():
        print(f'公司名里没有 {so.EXPECTED_COMPANY!r}，可能登录错了账套，不继续。')
        return False

    log = so.load_json(LOG_JSON) if os.path.exists(LOG_JSON) else {}
    done = skipped = 0
    failed = []
    stopped = False
    for amend, ref in zip(amends, refs):
        print(f'[{ref}] 改 {amend.get("doc_no", "")} {amend.get("code", "")} {amend.get("company_name", "")}')
        try:
            if mode == 'write' and log.get(ref, {}).get('status') == 'saving':
                raise so.OrderError('上次保存这张单时中断了，不确定有没有改成。请到 SQL ACC 里看这张单，'
                                    f'确认后再手动改 {os.path.basename(LOG_JSON)} 里这个 ref。')
            check_amend(app, amend)
            result = amend_order(app, amend, mode, ref, log)
            if result == 'saved':
                print(f'    已保存并核对: {amend["doc_no"]}')
            if result == 'done':
                skipped += 1
            else:
                done += 1
        except so.OrderError as e:
            print(f'    [没改] {e}')
            failed.append((ref, str(e).split('\n')[0]))
        except Exception as e:
            print(f'    [整批停下] {e}')
            print(f'在 {ref} 停下，后面的单没有处理。')
            failed.append((ref, str(e).split('\n')[0]))
            stopped = True
            break

    print('=' * 60)
    print(f'改单 模式 {mode}：通过 {done} 张，已经改好跳过 {skipped} 张，有问题 {len(failed)} 张'
          + ('，整批中途停下' if stopped else ''))
    for ref, reason in failed:
        print(f'  {ref}: {reason}')
    return not failed and not stopped


def run_locked(amend_json, mode):
    if mode != 'write':
        return run(amend_json, mode)
    # 和录入脚本用同一把锁：录入和改单不能同时跑
    try:
        os.close(os.open(so.LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        print(f'已经有一个录入或改单在跑（或上次异常退出）。确认没有在跑之后，删掉 {so.LOCK_FILE} 再试。')
        return False
    try:
        return run(amend_json, mode)
    finally:
        os.remove(so.LOCK_FILE)


def main():
    parser = argparse.ArgumentParser(description='用 SDK Live 改已经录进去的 Sales Order')
    parser.add_argument('amend_json', nargs='?', default='amend.json', help='改单 JSON 文件')
    parser.add_argument('--preview', action='store_true', help='在内存里改好但不保存，打印改完的样子')
    parser.add_argument('--write', action='store_true', help='真正保存')
    args = parser.parse_args()
    if args.preview and args.write:
        parser.error('--preview 和 --write 只能选一个')
    mode = 'write' if args.write else 'preview' if args.preview else 'check'

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    result_txt = os.path.join(so.BASE_DIR, f'result_amend_{mode}_{stamp}.txt')
    console = sys.stdout
    sys.stdout = so.Tee(console, result_txt)
    try:
        ok = run_locked(args.amend_json, mode)
    except Exception:
        print('[出错] 脚本没能跑完:')
        traceback.print_exc(file=sys.stdout)
        ok = False
    finally:
        sys.stdout.close()
        sys.stdout = console
    print(f'这次的输出已存到: {result_txt}')
    return ok


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
