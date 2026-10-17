#!/usr/bin/env python3
"""One-time backfill: Goodreads review timelines -> Reading log rows and Started dates.

Input
  sync/backfill/reviews.json          the read / currently-reading / did-not-finish shelves (from the RSS feeds)
  sync/backfill/timelines/batch_*.json  each review's "Reading Progress" rows, read from the signed-in browser
  data/raw/{books,authors,reads}.json   the dashboard's Notion pull (Notion is canonical; nothing is created here)

Output (sync/backfill/plan.json)
  log_rows        Reading log pages to create (Notion property payloads), one per timeline event
  started_updates Reads whose Started should be set from the timeline (blank or Dates estimated ✓)
  report          what could not be placed: unmatched books, events outside any Read, finish-date mismatches

Usage: python3 sync/backfill.py [--raw data/raw]            build the plan
       python3 sync/backfill.py --apply [--dry-run]           write the plan to Notion over the API (token in sync/.env)
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from goodreads_sync import Library, Notion, cfg, load_env, log, log_props, squeeze  # noqa: E402

HERE = Path(__file__).resolve().parent / 'backfill'
ROW = re.compile(r'^(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4}) – (?P<what>.+)$')
PAGE = re.compile(r'^page (?P<page>\d+)(?: (?P<pct>[\d.]+)%)?(?: ["“](?P<note>.*)["”])?$')
PCT = re.compile(r'^(?P<pct>[\d.]+)%(?: ["“](?P<note>.*)["”])?$')


def parse_rows(rows):
    """Timeline rows -> events [{kind, date, page?, percent?, note?, raw}] in page order."""
    out = []
    for raw in rows:
        m = ROW.match(raw)
        if not m:
            continue  # "Finished Reading Add a date" and other undated rows
        d = datetime.strptime(m.group('date'), '%B %d, %Y').date().isoformat()
        what = m.group('what').strip()
        ev = None
        if what == 'Started Reading':
            ev = {'kind': 'started'}
        elif what == 'Finished Reading':
            ev = {'kind': 'finished'}
        elif what.startswith('Shelved as: did-not-finish'):
            ev = {'kind': 'dnf'}
        elif what.startswith('page '):
            pm = PAGE.match(what)
            if pm:
                ev = {'kind': 'progress', 'page': int(pm.group('page')), 'percent': round(float(pm.group('pct'))) if pm.group('pct') else None, 'note': squeeze(pm.group('note')) if pm.group('note') else None}
        elif PCT.match(what):
            pm = PCT.match(what)
            ev = {'kind': 'progress', 'percent': round(float(pm.group('pct'))), 'note': squeeze(pm.group('note')) if pm.group('note') else None}
        if ev:
            ev.update({'date': d, 'raw': raw})
            out.append(ev)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', default='data/raw')
    ap.add_argument('--timelines', default=str(HERE / 'timelines'), help='directory of batch_*.json files')
    ap.add_argument('--out', default=str(HERE / 'plan.json'))
    ap.add_argument('--apply', action='store_true', help='write plan.json to Notion (Reading log rows, then Started dates)')
    ap.add_argument('--dry-run', action='store_true', help='with --apply: print the writes, change nothing')
    args = ap.parse_args()
    if args.apply:
        return apply(Path(args.out), args.dry_run)
    reviews = {r['review_id']: r for r in json.loads((HERE / 'reviews.json').read_text(encoding='utf-8'))}
    timelines = {}
    for f in sorted(Path(args.timelines).glob('batch_*.json')):
        for t in json.loads(f.read_text(encoding='utf-8')):
            timelines[str(t['id'])] = t
    lib = Library(None)
    lib.load_raw(args.raw)
    reads_by_book = defaultdict(list)
    for r in lib.raw_reads:
        reads_by_book[r['book']].append(r)
    raw_reads = {r['id']: r for r in lib.raw_reads}
    raw_est = {}
    for r in json.loads(Path(args.raw, 'reads.json').read_text(encoding='utf-8')):
        raw_est[r['url'][23:]] = r.get('Dates estimated') == '__YES__'

    plan = {'log_rows': [], 'started_updates': [], 'report': {'no_timeline': [], 'unmatched_book': [], 'outside_any_read': [], 'finish_mismatch': [], 'started_kept': []}}
    seen_guid = set()
    for rid, rv in reviews.items():
        t = timelines.get(rid)
        if not t or t.get('error') or t.get('signin') or t.get('status') != 200:
            plan['report']['no_timeline'].append({'review_id': rid, 'title': rv['title']})
            continue
        events = parse_rows(t['rows'])
        if not events:
            continue
        book, why = lib.match(t.get('title') or rv['title'], (t.get('authors') or [rv['author']])[0] if (t.get('authors') or rv['author']) else None)
        if not book:
            plan['report']['unmatched_book'].append({'review_id': rid, 'title': t.get('title') or rv['title'], 'author': rv['author'], 'reason': why, 'events': len(events)})
            continue
        reads = sorted(reads_by_book.get(book['id'], []), key=lambda r: (r['ended'] or '9999', r['started'] or ''))
        open_reads = [r for r in reads if not r['outcome']]
        closed = [r for r in reads if r['outcome']]

        def read_for(d):
            for r in closed:
                if r['ended'] and d <= r['ended']:
                    return r
            if open_reads:
                return open_reads[0]
            late = [r for r in closed if r['ended'] and 0 < (datetime.fromisoformat(d) - datetime.fromisoformat(r['ended'])).days <= 14]
            return late[-1] if late else None

        gr_book = (t.get('book') or '').split('/book/show/')[-1].split('-')[0]
        for i, ev in enumerate(events):
            rd = read_for(ev['date'])
            if not rd:
                plan['report']['outside_any_read'].append({'title': book['title'], 'date': ev['date'], 'kind': ev['kind'], 'raw': ev['raw']})
                continue
            guid = f'Timeline{rid}:{i}'
            if guid in seen_guid:
                continue
            seen_guid.add(guid)
            e = {'kind': ev['kind'], 'title': book['title'], 'author': None, 'gr_book_id': gr_book or None, 'guid': guid, 'at': ev['date'],
                 'raw': ev['raw'], 'note': ev.get('note'), 'page': ev.get('page'), 'total': None, 'percent': ev.get('percent')}
            plan['log_rows'].append({'read': rd['id'], 'read_title': rd['title'], 'props': log_props(e, book, {'id': rd['id']})})
            if ev['kind'] == 'started':
                if not rd['started'] or raw_est.get(rd['id']):
                    if not rd['ended'] or ev['date'] <= rd['ended']:
                        plan['started_updates'].append({'read': rd['id'], 'read_title': rd['title'], 'started': ev['date'], 'was': rd['started'], 'was_estimated': raw_est.get(rd['id'], False)})
                elif rd['started'] != ev['date']:
                    plan['report']['started_kept'].append({'read_title': rd['title'], 'notion': rd['started'], 'goodreads': ev['date']})
            if ev['kind'] == 'finished' and rd['ended'] and abs((datetime.fromisoformat(ev['date']) - datetime.fromisoformat(rd['ended'])).days) > 1:
                plan['report']['finish_mismatch'].append({'read_title': rd['title'], 'notion_ended': rd['ended'], 'goodreads_finished': ev['date']})
    # one Started per read: keep the earliest timeline start that fits
    best = {}
    for u in plan['started_updates']:
        if u['read'] not in best or u['started'] < best[u['read']]['started']:
            best[u['read']] = u
    plan['started_updates'] = sorted(best.values(), key=lambda u: u['started'])
    Path(args.out).write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding='utf-8')
    kinds = defaultdict(int)
    for r in plan['log_rows']:
        kinds[r['props']['Kind']['select']['name']] += 1
    print(f"timelines: {len(timelines)} of {len(reviews)} reviews")
    print(f"log rows: {len(plan['log_rows'])} ({dict(kinds)}); started updates: {len(plan['started_updates'])}")
    for k, v in plan['report'].items():
        print(f"report.{k}: {len(v)}")


def apply(plan_path, dry_run):
    load_env()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    notion = Notion(cfg('NOTION_TOKEN', required=True), dry_run=dry_run)
    log_ds = cfg('NOTION_LOG_DS', required=True)
    done_path = plan_path.with_name('applied.json')
    done = json.loads(done_path.read_text(encoding='utf-8')) if done_path.exists() else {'rows': [], 'started': []}
    # rows already in the log (by Goodreads id) are skipped, so the run can be repeated safely
    existing = set()
    for pg in notion.query_all(log_ds, {'property': 'Goodreads id', 'rich_text': {'starts_with': 'Timeline'}}):
        existing.add(''.join(t.get('plain_text', '') for t in pg['properties'].get('Goodreads id', {}).get('rich_text', [])))
    log(f'{len(plan["log_rows"])} rows in plan, {len(existing)} timeline rows already in the log')
    n = 0
    for row in plan['log_rows']:
        guid = row['props']['Goodreads id']['rich_text'][0]['text']['content']
        if guid in existing or guid in done['rows']:
            continue
        notion.create_page(log_ds, row['props'])
        n += 1
        if not dry_run:
            done['rows'].append(guid)
            if n % 50 == 0:
                done_path.write_text(json.dumps(done), encoding='utf-8')
                log(f'  {n} rows written')
    log(f'{n} Reading log rows created')
    m = 0
    for u in plan['started_updates']:
        if u['read'] in done['started']:
            continue
        notion.update_page(u['read'], {'Started': {'date': {'start': u['started']}}, 'Dates estimated': {'checkbox': False}})
        m += 1
        if not dry_run:
            done['started'].append(u['read'])
    if not dry_run:
        done_path.write_text(json.dumps(done), encoding='utf-8')
    log(f'{m} Reads got a Started date' + (' (dry run)' if dry_run else ''))
    return 0


if __name__ == '__main__':
    main()
