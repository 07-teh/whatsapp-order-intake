"""Read the order-group chats from the local WhatsApp Desktop database (macOS).

Copies ChatStorage.sqlite to a temp file first, reads only the configured order groups, and
decodes reply links from the protobuf metadata so an "Add ..." reply can be tied to its order.
Output: data/wa_messages.json (one record per message: id, chat, time, type, text, file, reply_to).
Comments in this file are in Chinese.
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import tempfile

# =============================================
# 从本机 Mac 版 WhatsApp 的数据库里读订单群的消息，写成 JSON
# 只读三个订单群，别的聊天一条都不碰。先复制一份数据库再读，不动 WhatsApp 正在用的那份。
#   python3 tools/wa_extract.py                      读全部
#   python3 tools/wa_extract.py --since 08/10/2026   只读这天零点之后的
# 结果写在 data/wa_messages.json（data/ 不进版本库）
# 每条消息带 reply_to：它回复的是哪一条。Add 加给哪张单就靠这个。
# =============================================

ORDER_CHATS = ['Mei - Ordering Group', 'Amin - Ordering Group', 'Office Ordering Group']
WA_DIR = os.path.expanduser('~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared')
DB_NAME = 'ChatStorage.sqlite'
APPLE_EPOCH = 978307200          # 数据库里的时间是从 2001-01-01 起算的秒数
MYT = datetime.timezone(datetime.timedelta(hours=8))
TYPE_NAMES = {0: 'text', 1: 'image', 8: 'document', 14: 'deleted', 6: 'system', 10: 'system', 59: 'system'}
QUOTED_ID_FIELD = 5              # 元数据（protobuf）里，被回复那条消息的编号


def read_varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, pos


def quoted_stanza_id(metadata):
    """从消息的元数据里取出它回复的那条消息的编号；不是回复就返回 None。"""
    if not metadata:
        return None
    pos = 0
    try:
        while pos < len(metadata):
            key, pos = read_varint(metadata, pos)
            field, wire = key >> 3, key & 7
            if wire == 0:
                _, pos = read_varint(metadata, pos)
            elif wire == 2:
                size, pos = read_varint(metadata, pos)
                if field == QUOTED_ID_FIELD:
                    return metadata[pos:pos + size].decode('ascii')
                pos += size
            elif wire == 1:
                pos += 8
            elif wire == 5:
                pos += 4
            else:
                return None
    except (IndexError, UnicodeDecodeError):
        return None
    return None


def copy_db(tmp_dir):
    for suffix in ('', '-wal', '-shm'):
        src = os.path.join(WA_DIR, DB_NAME + suffix)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp_dir, DB_NAME + suffix))
    return os.path.join(tmp_dir, DB_NAME)


def extract(since):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = sqlite3.connect(copy_db(tmp_dir))
        marks = ','.join('?' * len(ORDER_CHATS))
        chats = dict(db.execute(
            f'SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZPARTNERNAME IN ({marks})', ORDER_CHATS))
        missing = [name for name in ORDER_CHATS if name not in chats.values()]
        if missing:
            raise SystemExit(f'数据库里找不到这些群: {missing}')

        marks = ','.join('?' * len(chats))
        since_apple = since.timestamp() - APPLE_EPOCH if since else -1e18
        rows = db.execute(f'''
            SELECT m.ZCHATSESSION, m.ZSTANZAID, m.ZMESSAGEDATE, m.ZISFROMME, g.ZMEMBERJID, m.ZPUSHNAME,
                   m.ZMESSAGETYPE, m.ZTEXT, mi.ZTITLE, mi.ZMEDIALOCALPATH, mi.ZMETADATA
            FROM ZWAMESSAGE m
            LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
            LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
            WHERE m.ZCHATSESSION IN ({marks}) AND m.ZMESSAGEDATE >= ?
            ORDER BY m.ZMESSAGEDATE, m.Z_PK''', (*chats, since_apple)).fetchall()
        known_ids = {row[0] for row in db.execute(
            f'SELECT ZSTANZAID FROM ZWAMESSAGE WHERE ZCHATSESSION IN ({marks})', tuple(chats))}
        db.close()

    messages = []
    for chat, stanza_id, when, from_me, member, push_name, mtype, text, title, path, metadata in rows:
        kind = TYPE_NAMES.get(mtype, f'other-{mtype}')
        reply_to = quoted_stanza_id(metadata) if kind in ('text', 'image', 'document') else None
        # 图片的配文在 title 里，PDF 的文件名在 text 里
        body = (title if kind == 'image' else text) or ''
        messages.append({
            'id': stanza_id,
            'chat': chats[chat],
            'time': datetime.datetime.fromtimestamp(when + APPLE_EPOCH, MYT).strftime('%Y-%m-%d %H:%M:%S'),
            'sender': 'me' if from_me else (member or push_name or ''),
            'type': kind,
            'text': body,
            'file': path or '',
            'reply_to': reply_to,
            'reply_found': reply_to in known_ids if reply_to else None,
        })
    return messages


def main():
    parser = argparse.ArgumentParser(description='读本机 WhatsApp 里三个订单群的消息')
    parser.add_argument('--since', help='只读这天零点之后的消息，日/月/年')
    parser.add_argument('--out', default=os.path.join('data', 'wa_messages.json'))
    args = parser.parse_args()
    since = datetime.datetime.strptime(args.since, '%d/%m/%Y').replace(tzinfo=MYT) if args.since else None

    messages = extract(since)
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=1)
    print(f'已写入 {args.out}：{len(messages)} 条')
    for name in ORDER_CHATS:
        part = [m for m in messages if m['chat'] == name]
        replies = [m for m in part if m['reply_to']]
        print(f'  {name}: {len(part)} 条，其中回复 {len(replies)} 条（原文找得到 {sum(1 for m in replies if m["reply_found"])}）')


if __name__ == '__main__':
    main()
