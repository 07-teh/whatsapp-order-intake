"""Read-only probe: dump every field of the given Sales Orders, used while learning the SDK.
Comments in this file are in Chinese.
"""
import re
import sys
import win32com.client

# =============================================
# SQL ACC SDK Live 只读测试：把已有的 Sales Order 原样读出来
# 只读，不新增、不修改、不删除，也不会登出或关闭 SQL ACC
# 先手动打开并登录 SQL ACC，再运行:
#   python sdk_dump_so.py SO-58195 SO-58200
# 结果写在同目录的 so_dump.txt
# =============================================

OUTPUT_TXT = 'so_dump.txt'
DOCNO_PATTERN = re.compile(r'^[A-Za-z0-9/_-]+$')


def dump_dataset(ds, out, title):
    """把一个结果集的所有字段名和值写出来"""
    out.append(f'--- {title} (共 {ds.RecordCount} 笔)')
    if ds.RecordCount == 0:
        return
    field_count = ds.Fields.Count
    names = [ds.Fields.Items(i).FieldName for i in range(field_count)]
    ds.First()
    row = 0
    while not ds.eof:
        row += 1
        out.append(f'[{row}]')
        for name in names:
            try:
                value = ds.FindField(name).AsString
            except Exception as e:
                value = f'<读不出: {e}>'
            if value != '':
                out.append(f'  {name} = {value}')
        ds.Next()


def dump_order(app, doc_no, out):
    out.append('=' * 60)
    out.append(f'DocNo: {doc_no}')
    header = app.DBManager.NewDataSet(
        f"SELECT * FROM SL_SO WHERE DocNo = '{doc_no}'")
    dump_dataset(header, out, 'SL_SO 单头')
    if header.RecordCount == 0:
        return
    header.First()
    doc_key = header.FindField('DocKey').AsString
    detail = app.DBManager.NewDataSet(
        f'SELECT * FROM SL_SODTL WHERE DocKey = {int(doc_key)} ORDER BY Seq')
    dump_dataset(detail, out, 'SL_SODTL 明细')


def main():
    doc_nos = [a.strip() for a in sys.argv[1:] if a.strip()]
    if not doc_nos:
        doc_nos = input('输入要读取的 S/O No（多个用空格隔开）: ').split()
    bad = [d for d in doc_nos if not DOCNO_PATTERN.match(d)]
    if bad or not doc_nos:
        print(f'单号格式不对: {bad}')
        return

    app = win32com.client.Dispatch('SQLAcc.BizApp')
    if not app.IsLogin:
        print('SQL ACC 还没登录，请先手动登录再运行。')
        return

    out = []
    for doc_no in doc_nos:
        try:
            dump_order(app, doc_no, out)
        except Exception as e:
            out.append(f'<{doc_no} 读取失败: {e}>')

    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print(f'完成，已写入 {OUTPUT_TXT}（{len(doc_nos)} 张单）')


if __name__ == '__main__':
    main()
