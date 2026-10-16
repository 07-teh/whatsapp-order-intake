"""One-command demo on SYNTHETIC data: chat messages -> candidates -> DeepSeek draft -> checks -> eval.

    python3 demo/run_demo.py            # replay recorded DeepSeek answers (no API key, no network)
    python3 demo/run_demo.py --record   # call DeepSeek for real (needs DEEPSEEK_API_KEY) and save the answers

Runs the production code unchanged (tools/learn_*.py, pipeline/run_night.py, pipeline/eval.py) inside
demo/workspace/, which make_data.py fills with invented customers, products and messages.
The only things swapped out are where DeepSeek answers are cached (demo/recorded/) and the balance query.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WS = os.path.join(HERE, 'workspace')
RECORDED = os.path.join(HERE, 'recorded')
NIGHT, START, END = '20261015', '2026-10-14 22:00:00', '2026-10-15 22:00:00'
BANNER = '**SYNTHETIC DEMO DATA** - every customer, product and order here is invented.'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--record', action='store_true', help='call DeepSeek and save its answers to demo/recorded/')
    a = p.parse_args()
    # prompts must be byte-identical between record and replay, so fix set ordering
    if os.environ.get('PYTHONHASHSEED') != '0':
        os.execve(sys.executable, [sys.executable] + sys.argv, dict(os.environ, PYTHONHASHSEED='0'))

    print(BANNER)
    subprocess.run([sys.executable, os.path.join(HERE, 'make_data.py')], check=True)
    os.chdir(WS)
    for tool in ('learn_aliases.py', 'learn_items.py'):
        print(f'\n$ python3 tools/{tool}')
        subprocess.run([sys.executable, os.path.join(ROOT, 'tools', tool)], check=True)

    sys.path.insert(0, os.path.join(ROOT, 'pipeline'))
    import deepseek
    import balance
    import run_night
    import eval as ev

    deepseek.CACHE_DIR = RECORDED
    if not a.record:
        os.environ.pop('DEEPSEEK_API_KEY', None)
    original = deepseek.chat_json

    def chat_json(system, user, use_cache=True, images=()):
        try:
            return original(system, user, use_cache, images)
        except SystemExit:          # replay mode and this prompt was never recorded
            raise ValueError('no recorded answer for this prompt (run with --record)')

    deepseek.chat_json = chat_json
    balance.text = lambda info=None: 'DeepSeek balance: not queried in demo mode'

    print(f'\n$ python3 pipeline/run_night.py --night {NIGHT} --start "{START}" --end "{END}"'
          + ('' if a.record else '   (replaying demo/recorded/)'))
    run_night.run(NIGHT, START, END, out_root=os.path.join('output', 'ds'))
    _, report = ev.compare(NIGHT, os.path.join('output', 'ds'))

    out_dir = os.path.join(WS, 'output', 'ds', NIGHT)
    with open(os.path.join(out_dir, 'orders_ds.json'), encoding='utf-8') as f:
        orders = json.load(f)
    with open(os.path.join(out_dir, 'demo_result.json'), 'w', encoding='utf-8') as f:
        json.dump({'synthetic': True, 'note': BANNER, 'night': NIGHT, 'orders': orders}, f, ensure_ascii=False, indent=1)

    print('\n' + BANNER)
    print('\n==== review list (orders_ds.txt) ====')
    with open(os.path.join(out_dir, 'orders_ds.txt'), encoding='utf-8') as f:
        print(f.read())
    print('==== replay evaluation against the human-verified answer ====')
    print(report)
    print(f'\nfiles: {os.path.relpath(out_dir, ROOT)}/ (demo_result.json, orders_ds.txt, raw.json)')


if __name__ == '__main__':
    main()
