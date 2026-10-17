#!/usr/bin/env python3
"""The local half of a dashboard refresh: analyze, bundle, compare, report.

Runs pipeline/analyze.py and pipeline/bundle.py, then writes data/out/refresh_summary.json
with the as-of date, the sanity numbers, how they differ from the committed headline
(HEAD:data/out/headline.json) and which data/out/ds/*.json files changed, so an unattended
run can decide what to upload and can report differences instead of patching them.

Usage: python3 pipeline/refresh.py [--as-of YYYY-MM-DD]
Exit 0: ran clean. Exit 2: analyze.py reported an integrity problem (a finished read with
no pages counted, a read whose Book is missing, an edition with no Book or Format) or failed;
nothing should be published until someone looks. The summary is written either way.
"""
import json, os, re, subprocess, sys

OUT = 'data/out'; DS = f'{OUT}/ds'
HEADLINE_KEYS = ['reads_finished', 'books_read', 'pages_read', 'dnf', 'reading_now', 'owned_paper', 'owned_kindle',
                 'classic', 'canonical', 'books_no_genre', 'books_no_pages', 'books_total', 'owned_copies',
                 'owned_unread', 'wishlist_books', 'last_read']

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr

def main():
    extra = sys.argv[1:]
    code, out, err = run([sys.executable, 'pipeline/analyze.py'] + extra)
    sys.stdout.write(out); sys.stderr.write(err)
    problems = []
    if code != 0:
        problems.append(f'analyze.py exited {code}: {err.strip().splitlines()[-1] if err.strip() else ""}')
    lines = out.splitlines()
    def line(prefix):
        return next((l for l in lines if l.startswith(prefix)), None)
    zero_pages = line('fin reads with 0 pages counted')
    if zero_pages and not zero_pages.endswith('[]'): problems.append(zero_pages)
    for prefix in ('reads missing book', 'editions no book', 'editions no format'):
        l = line(prefix)
        if l and not l.endswith(' 0'): problems.append(l)
    if code == 0:
        code_b, out_b, err_b = run([sys.executable, 'pipeline/bundle.py'])
        sys.stdout.write(out_b); sys.stderr.write(err_b)
        if code_b != 0: problems.append(f'bundle.py exited {code_b}')
    # sanity numbers, as printed
    sanity = {k: line(k) for k in ('as of', 'rows', 'finished', 'finished date range', 'tracking from', 'pages by year',
                                  'reads by year', 'owned now by format', 'classic', 'books no pages', 'status')}
    # headline now vs committed
    head_new = json.load(open(f'{OUT}/headline.json'))[0] if os.path.exists(f'{OUT}/headline.json') else {}
    code_g, out_g, _ = run(['git', 'show', 'HEAD:data/out/headline.json'])
    head_old = json.loads(out_g)[0] if code_g == 0 and out_g.strip() else {}
    diffs = {k: {'committed': head_old.get(k), 'now': head_new.get(k)} for k in HEADLINE_KEYS if head_old.get(k) != head_new.get(k)}
    # changed datasets (what the routine has to upload)
    code_s, out_s, _ = run(['git', 'status', '--porcelain', '--', DS])
    changed = sorted(re.sub(r'^.{3}', '', l).split('/')[-1][:-5] for l in out_s.splitlines() if l.strip())
    summary = {'as_of': head_new.get('as_of'), 'ok': not problems, 'problems': problems, 'sanity': sanity,
               'headline_changes': diffs, 'changed_datasets': changed}
    json.dump(summary, open(f'{OUT}/refresh_summary.json', 'w'), indent=1, ensure_ascii=False)
    print('\n== refresh summary ==')
    print('as of', summary['as_of'])
    print('headline changes vs HEAD:', json.dumps(diffs) if diffs else 'none')
    print('changed datasets:', ', '.join(changed) if changed else 'none')
    if problems:
        print('PROBLEMS:\n  ' + '\n  '.join(problems)); sys.exit(2)
    print('ok')

if __name__ == '__main__':
    main()
