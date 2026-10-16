"""Build the nightly zip that is carried to the company PC.

Contains the reviewed orders file, the entry/export scripts and two double-click .bat files
(1_preview.bat, 2_write.bat). After writing, the company PC exports recent Sales Orders and master
data into back_*.zip, which comes back to the Mac (see pipeline/ingest_back.py).
Comments in this file are in Chinese.
"""
import argparse
import os
import zipfile

# =============================================
# 把当天要带去公司电脑的东西打成一个 zip：订单文件、对照清单、录入脚本、导出脚本、两个双击就能跑的 bat
#   python3 tools/pack.py 20261009
# 写: output/poultry_20261009.zip
# 公司电脑上解压到一个文件夹，先双击 1_preview.bat，没问题再双击 2_write.bat
# 2_write.bat 录完会接着导出前 3 天到后 7 天的单和客户、分店、货号，一起放进 back_*.zip
# =============================================

OUTPUT_DIR = 'output'
ENTRY_SCRIPT = os.path.join('company_pc', 'sdk_enter_so.py')
DUMP_SCRIPT = os.path.join('company_pc', 'sdk_dump_master.py')
# bat 里只用英文，避免公司电脑的编码把中文弄乱
BAT = '@echo off\r\ncd /d "%~dp0"\r\n{commands}\r\necho.\r\npause\r\n'
ENTER = 'call python sdk_enter_so.py {orders} {flag}'
# 录入有单没录成时退出码是 1。导出照样跑，但最后要再提醒一次，免得录入的汇总被后面的输出顶上去看漏
DUMP = ('set RC=%ERRORLEVEL%\r\n'
        'call python sdk_dump_master.py --so-days-back 3 --so-days-ahead 7 --zip\r\n'
        'if not "%RC%"=="0" echo ***** ENTRY HAD PROBLEMS. SCROLL UP AND CHECK. *****')


def main():
    parser = argparse.ArgumentParser(description='把当天的产出打成一个 zip')
    parser.add_argument('day', help='日期，例如 20261009')
    args = parser.parse_args()

    orders = f'orders_{args.day}.json'
    listing = f'orders_{args.day}.txt'
    for name in (orders, listing):
        if not os.path.exists(os.path.join(OUTPUT_DIR, name)):
            raise SystemExit(f'找不到 {OUTPUT_DIR}/{name}')

    path = os.path.join(OUTPUT_DIR, f'poultry_{args.day}.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(OUTPUT_DIR, orders), orders)
        z.write(os.path.join(OUTPUT_DIR, listing), listing)
        z.write(ENTRY_SCRIPT, os.path.basename(ENTRY_SCRIPT))
        z.write(DUMP_SCRIPT, os.path.basename(DUMP_SCRIPT))
        z.writestr('1_preview.bat', BAT.format(commands=ENTER.format(orders=orders, flag='--preview')))
        z.writestr('2_write.bat', BAT.format(commands=ENTER.format(orders=orders, flag='--write') + '\r\n' + DUMP))
    print(f'已打包: {path}')
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            print(f'  {info.filename}  {info.file_size} 字节')


if __name__ == '__main__':
    main()
