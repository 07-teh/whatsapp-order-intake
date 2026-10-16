"""Ingest the return package (zip) from the company PC into data/: master data, recent Sales
Orders and the entry log. Only whitelisted file names are extracted; existing files are backed up first.
Comments in this file are in Chinese.
"""
import argparse
import datetime
import glob
import io
import os
import re
import shutil
import sys
import zipfile

# =============================================
# 公司电脑带回来的回传包（zip）→ 放进 data/
#   python3 pipeline/ingest_back.py 某个.zip
#   python3 pipeline/ingest_back.py --pending         # 总控台上传后 n8n 调：output/inbox/ 里还没处理的按顺序全处理，处理完移到 inbox/done/
# 只认这几种文件，其他一律不解压（zip 里嵌套的 back_*.zip 也会看）：
#   master_customers/branches/items.json → data/
#   so_history_*.json                    → data/so_history/
#   entered_log.json、result_*.txt       → data/entered/<那一晚>/
# 覆盖之前先把旧文件改名成 .bak_时间。
# =============================================

INBOX = os.path.join('output', 'inbox')
MASTER = re.compile(r'^master_(customers|branches|items)\.json$')
HISTORY = re.compile(r'^so_history_\d{8}_\d{8}\.json$')
NIGHT_FILE = re.compile(r'^(entered_log\.json|result_[a-z]+_\d{8}_\d{6}\.txt)$')
WRITE_TIME = re.compile(r'^result_write_(\d{8})_(\d{2})')
MAX_FILE = 200 * 1024 * 1024


def collect(data, depth=0):
    """zip 内容 → {文件名: bytes}。外层的优先，嵌套 zip 只补外层没有的。"""
    found, nested = {}, []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = os.path.basename(info.filename)
            if info.is_dir() or name.startswith('.') or info.file_size > MAX_FILE:
                continue
            if name.lower().endswith('.zip') and depth < 2:
                nested.append(z.read(info))
            elif MASTER.match(name) or HISTORY.match(name) or NIGHT_FILE.match(name):
                found.setdefault(name, z.read(info))
    for inner in nested:
        for name, content in collect(inner, depth + 1).items():
            found.setdefault(name, content)
    return found


def night_of(files):
    """录入那一晚：看 result_write 的时间，凌晨 6 点前算前一晚。"""
    for name in sorted(files):
        m = WRITE_TIME.match(name)
        if m:
            day = datetime.datetime.strptime(m.group(1), '%Y%m%d')
            if int(m.group(2)) < 6:
                day -= datetime.timedelta(days=1)
            return day.strftime('%Y%m%d')
    return None


def put(path, content, stamp, report):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == content:
                report.append(f'  一样，没动  {path}')
                return
        shutil.move(path, f'{path}.bak_{stamp}')
    with open(path, 'wb') as f:
        f.write(content)
    report.append(f'  写入  {path}  ({len(content) // 1024} KB)')


def ingest(zip_path, data_root='data'):
    with open(zip_path, 'rb') as f:
        try:
            files = collect(f.read())
        except zipfile.BadZipFile:
            return f'{os.path.basename(zip_path)} 不是 zip 文件（或者坏了），没有处理'
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    report = [f'回传包 {os.path.basename(zip_path)}：认出 {len(files)} 个文件']
    night = night_of(files)
    for name, content in sorted(files.items()):
        if MASTER.match(name):
            put(os.path.join(data_root, name), content, stamp, report)
        elif HISTORY.match(name):
            put(os.path.join(data_root, 'so_history', name), content, stamp, report)
        elif night:
            put(os.path.join(data_root, 'entered', night, name), content, stamp, report)
        else:
            report.append(f'  跳过 {name}：包里没有 result_write_*.txt，不知道是哪一晚')
    if not files:
        report.append('  ⚠ 一个认识的文件都没有，是不是传错了包？')
    return '\n'.join(report)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('zip', nargs='?')
    p.add_argument('--pending', action='store_true')
    p.add_argument('--data-root', default='data')
    a = p.parse_args()
    if a.pending:
        files = sorted((f for f in glob.glob(os.path.join(INBOX, '*')) if os.path.isfile(f)), key=os.path.getmtime)
        if not files:
            print('没有新的文件要处理')
            return
        done = os.path.join(INBOX, 'done')
        os.makedirs(done, exist_ok=True)
        for f in files:
            print(ingest(f, a.data_root))
            shutil.move(f, os.path.join(done, os.path.basename(f)))
        return
    if not a.zip:
        p.error('给一个 zip，或者用 --pending')
    print(ingest(a.zip, a.data_root))


if __name__ == '__main__':
    main()
