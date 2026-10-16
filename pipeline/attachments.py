"""Read order attachments: PDFs are text-extracted first (cheap and exact for digital POs);
scanned PDFs and images are rendered to images for the multimodal model.
Comments in this file are in Chinese.
"""
import glob
import os
import subprocess
import tempfile

# =============================================
# 读 WhatsApp 消息里的附件：PDF 先抽文字（电子版 PO 都有文字层，又准又便宜）；
# 抽不到文字的扫描件和图片，转成图片给模型看。
# =============================================

WA_MESSAGE_DIR = os.path.expanduser('~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message')
MAX_PAGES = 3
MIN_TEXT = 80          # 抽出来的字少于这个数，当成扫描件


def path_of(message):
    if not message.get('file'):
        return ''
    path = os.path.realpath(os.path.join(WA_MESSAGE_DIR, message['file']))
    return path if path.startswith(os.path.realpath(WA_MESSAGE_DIR) + os.sep) else ''


def read(message):
    """返回 (文字, [(mime, bytes)], 问题)。文件不在（还没下载、被清掉）或读坏了，文字和图片都空，问题写原因。"""
    try:
        return _read(message)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return '', [], f'读附件出错: {e}'


def _read(message):
    path = path_of(message)
    if not path or not os.path.exists(path):
        return '', [], f'附件文件不在本机（{message.get("file") or "没有路径"}），可能 WhatsApp 还没下载'
    low = path.lower()
    if low.endswith('.pdf'):
        try:
            text = subprocess.run(['/opt/homebrew/bin/pdftotext', '-layout', '-l', str(MAX_PAGES), path, '-'],
                                  capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            text = ''
            problem = f'PDF 抽文字失败: {e}'
        else:
            problem = ''
        if len(text.strip()) >= MIN_TEXT:
            return text.strip(), [], problem
        return '', pdf_pages(path), problem
    if low.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
        mime = 'image/png' if low.endswith('.png') else 'image/jpeg'
        if low.endswith(('.heic', '.webp')):
            return '', convert_to_jpeg(path), ''
        with open(path, 'rb') as f:
            return '', [(mime, f.read())], ''
    return '', [], f'不认识的附件类型 {os.path.basename(path)}'


def pdf_pages(path):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(['/opt/homebrew/bin/pdftoppm', '-r', '110', '-png', '-l', str(MAX_PAGES), path, os.path.join(tmp, 'p')],
                       capture_output=True, timeout=120)
        out = []
        for name in sorted(glob.glob(os.path.join(tmp, 'p*.png'))):
            with open(name, 'rb') as f:
                out.append(('image/png', f.read()))
        return out


def convert_to_jpeg(path):
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, 'x.jpg')
        subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', path, '--out', dst], capture_output=True, timeout=60)
        if not os.path.exists(dst):
            return []
        with open(dst, 'rb') as f:
            return [('image/jpeg', f.read())]
