"""Read-only export from SQL Account: items, customers, branches and Sales Orders in a date range.
Never exports prices, phone numbers or tax IDs. Comments in this file are in Chinese.
"""
import argparse
import datetime
import json
import os
import traceback
import zipfile
import win32com.client

# =============================================
# SQL ACC SDK Live 只读导出：做对照表用的货号、客户、分店，以及一段日期内已录的 Sales Order
# 只读，不新增、不修改、不删除。不导出单价、电话、税号。
# 先手动打开并登录 SQL ACC，再运行:
#   python sdk_dump_master.py                                   货号 + 客户 + 分店
#   python sdk_dump_master.py --so-from 01/10/2026 --so-to 07/10/2026   另外导出这段日期的单
#   python sdk_dump_master.py --so-days-back 3 --so-days-ahead 7 --zip    每晚录完用：导出前 3 天到后 7 天的单，
#                                                                         连同主档放进录入那次的 back_*.zip
# 结果写在脚本旁边的 master_*.json / so_history_起_止.json
# =============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ITEM_FIELDS = ['Code', 'Description', 'Description2', 'IsActive']
CUSTOMER_FIELDS = ['Code', 'CompanyName', 'CompanyName2', 'Agent', 'Terms', 'Status']
BRANCH_FIELDS = ['Code', 'BranchType', 'BranchName', 'Address1', 'Address2', 'Address3', 'Address4']
SO_FIELDS = ['DocKey', 'DocNo', 'DocDate', 'Code', 'CompanyName', 'Agent', 'DocRef1', 'BranchName',
             'DAddress1', 'DAddress2', 'DAddress3', 'DAddress4', 'Cancelled']
SO_LINE_FIELDS = ['DocKey', 'Seq', 'ItemCode', 'Description', 'Qty', 'UOM']


LOG = []


def say(text):
    """屏幕上显示，同时记下来。用 --zip 时会存成 result_dump_*.txt 一起带回去，出了问题能看到原因。"""
    print(text)
    LOG.append(text)


def save_json(name, data):
    path = os.path.join(BASE_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    say(f'  已写入 {name}（{len(data)} 笔）')
    return path


def read_all(app, sql, names):
    """跑一条 SELECT，只留下 names 里列出的字段（系统里没有的字段自动略过）"""
    ds = app.DBManager.NewDataSet(sql)
    fields = [(name, ds.FindField(name)) for name in names]
    fields = [(name, field) for name, field in fields if field is not None]
    rows = []
    ds.First()
    while not ds.eof:
        rows.append({name: field.AsString for name, field in fields})
        ds.Next()
    return rows


def parse_day(text):
    return datetime.datetime.strptime(text.strip(), '%d/%m/%Y').date()


def so_range(args):
    """要导出哪段日期的单，返回 (起, 止)；没要求导出订单就返回 None。"""
    if args.so_from:
        return parse_day(args.so_from), parse_day(args.so_to)
    if args.so_days_back is not None:
        today = datetime.date.today()
        return today - datetime.timedelta(days=args.so_days_back), today + datetime.timedelta(days=args.so_days_ahead)
    return None


def is_write_back(path):
    """这个 back zip 是不是真正录入（--write）那次生成的。预览那次的不算。"""
    try:
        with zipfile.ZipFile(path) as z:
            return any(name.startswith('result_write_') for name in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def add_to_back_zip(paths, stamp):
    """把导出的文件放进录入那次生成的 back_*.zip，带回去还是只拿一个文件。
    找不到录入的 zip（例如录入中途被关掉）就新建一个，把文件夹里的录入结果和 entered_log.json 也放进去。"""
    backs = sorted((n for n in os.listdir(BASE_DIR) if n.startswith('back_') and n.endswith('.zip')), reverse=True)
    found = next((n for n in backs if is_write_back(os.path.join(BASE_DIR, n))), None)
    target = os.path.join(BASE_DIR, found or f'back_{stamp}.zip')
    if not found:
        loose = [n for n in os.listdir(BASE_DIR) if (n.startswith('result_') and n.endswith('.txt')) or n == 'entered_log.json']
        paths = paths + [os.path.join(BASE_DIR, n) for n in loose if os.path.join(BASE_DIR, n) not in paths]
    names = {os.path.basename(path) for path in paths}
    kept = {}
    if found:
        with zipfile.ZipFile(target) as z:
            kept = {name: z.read(name) for name in z.namelist() if name not in names}
    # 先写临时文件再替换，中途出错不会把原来的 zip 写坏
    tmp = target + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in kept.items():
            z.writestr(name, data)
        for path in paths:
            z.write(path, os.path.basename(path))
    os.replace(tmp, target)
    return target


def main():
    parser = argparse.ArgumentParser(description='只读导出做对照表用的资料')
    parser.add_argument('--so-from', help='导出已录订单的起始日期，日/月/年')
    parser.add_argument('--so-to', help='导出已录订单的结束日期，日/月/年')
    parser.add_argument('--so-days-back', type=int, help='从今天往前几天的单开始导出（按这台电脑的日期）')
    parser.add_argument('--so-days-ahead', type=int, default=0, help='导到今天往后几天的单为止，和 --so-days-back 一起用')
    parser.add_argument('--zip', action='store_true', help='把导出的文件放进录入那次生成的 back_*.zip')
    args = parser.parse_args()
    if bool(args.so_from) != bool(args.so_to):
        parser.error('--so-from 和 --so-to 要一起填')
    if args.so_from and args.so_days_back is not None:
        parser.error('--so-from 和 --so-days-back 只能选一种')
    if args.so_days_back is None and args.so_days_ahead:
        parser.error('--so-days-ahead 要和 --so-days-back 一起用')
    if args.so_days_back is not None and (args.so_days_back < 0 or args.so_days_ahead < 0):
        parser.error('天数不能是负数')
    days = so_range(args)

    written = []
    try:
        export(days, written)
    except Exception:
        # 例如 SQL ACC 连不上：把出错信息也记下来，照样带回去
        say('[出错] 导出没能跑完:\n' + traceback.format_exc())
    if args.zip:
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            log_path = os.path.join(BASE_DIR, f'result_dump_{stamp}.txt')
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(LOG) + '\n')
            print(f'要带回去的文件: {add_to_back_zip(written + [log_path], stamp)}')
        except Exception as e:
            print(f'  放进 back_*.zip 失败（导出的文件还在这个文件夹里，可以单独带回去）: {e}')
    print('导出结束。录入有没有问题，看上面录入那一段最后的汇总。')


def export(days, written):
    """导出主档和（要求了的话）一段日期的单，写成功的文件路径加进 written。"""
    app = win32com.client.Dispatch('SQLAcc.BizApp')
    if not app.IsLogin:
        say('SQL ACC 还没登录，请先手动登录再运行。')
        return

    jobs = [('master_items.json', 'SELECT * FROM ST_ITEM ORDER BY Code', ITEM_FIELDS),
            ('master_customers.json', 'SELECT * FROM AR_CUSTOMER ORDER BY Code', CUSTOMER_FIELDS),
            ('master_branches.json', 'SELECT * FROM AR_CUSTOMERBRANCH ORDER BY Code', BRANCH_FIELDS)]
    for name, sql, fields in jobs:
        try:
            written.append(save_json(name, read_all(app, sql, fields)))
        except Exception as e:
            say(f'  {name} 导出失败: {e}')

    if days:
        where = f"DocDate >= '{days[0]:%Y-%m-%d}' AND DocDate <= '{days[1]:%Y-%m-%d}'"
        so_json = f'so_history_{days[0]:%Y%m%d}_{days[1]:%Y%m%d}.json'
        try:
            orders = read_all(app, f'SELECT * FROM SL_SO WHERE {where} ORDER BY DocKey', SO_FIELDS)
            lines = read_all(app, 'SELECT * FROM SL_SODTL WHERE DocKey IN '
                                  f'(SELECT DocKey FROM SL_SO WHERE {where}) ORDER BY DocKey, Seq', SO_LINE_FIELDS)
            by_key = {o['DocKey']: dict(o, items=[]) for o in orders}
            for line in lines:
                # 两次查询之间别人刚存的新单，明细有、单头没有，略过
                order = by_key.get(line.pop('DocKey'))
                if order:
                    order['items'].append(line)
            written.append(save_json(so_json, list(by_key.values())))
        except Exception as e:
            say(f'  {so_json} 导出失败: {e}')


if __name__ == '__main__':
    main()
