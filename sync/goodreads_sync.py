#!/usr/bin/env python3
"""Nightly Goodreads -> Notion sync.

Reads Dan's public Goodreads RSS feeds (the updates stream and the currently-reading
shelf), turns "started reading" / "is on page N of M" / "is N% done" / "finished reading"
items into events, matches each to a Book and its open Read in Notion, and

  * appends one row per event to the Reading log database (append-only event stream),
  * sets Reads.Started (and clears Dates estimated) when a Started event lands on an open
    Read whose Started is blank.

It never creates Books or Reads: Notion is canonical for what book and edition is being
read. Anything it cannot match is written to sync/unmatched.json and printed.

Config comes from sync/.env (see .env.example). State (seen item ids, match cache) lives
in sync/state.json. Only stdlib is used so it runs under the system python3 from launchd.

Usage:
  goodreads_sync.py                 fetch, match, write
  goodreads_sync.py --dry-run       fetch and match, print the writes, write nothing
  goodreads_sync.py --fixture f.xml parse a saved feed file instead of fetching
  goodreads_sync.py --parse-only    parse the feed(s) and print events; no Notion at all
  goodreads_sync.py --fixture f.xml --shelf-fixture s.xml --offline-library data/raw
                                    match against the repo's raw pull; prints what it would write
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / '.env'
STATE_FILE = HERE / 'state.json'
UNMATCHED_FILE = HERE / 'unmatched.json'

NOTION_API = 'https://api.notion.com/v1'
NOTION_VERSION = '2025-09-03'
USER_AGENT = 'reading-library-sync/1.0'  # generic on purpose: no personal details leave the machine

# Data sources from the runbook ("Library runbook (for Claude)"). The Reading log id is set in .env
# once the database exists.
DEFAULTS = {
    'NOTION_BOOKS_DS': '21e3da73-9608-41f8-a857-bd226043a391',
    'NOTION_READS_DS': '05439130-377c-4e0b-948e-7c1d7ffb065e',
    'NOTION_AUTHORS_DS': 'f96682e9-8772-4493-abff-ec079148f1b2',
    'NOTION_LOG_DS': 'ced88101-f6c5-4e4e-9c1f-8305afeb2e7e',  # Reading log, created 2026-10-16 under Media › Library
}


# ---------------------------------------------------------------- config / state
def load_env(path=ENV_FILE):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cfg(name, required=False):
    v = os.environ.get(name) or DEFAULTS.get(name)
    if required and not v:
        sys.exit(f'missing {name} in {ENV_FILE}')
    return v


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    return {'seen': {}, 'book_cache': {}, 'runs': []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding='utf-8')


def log(*a):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- Goodreads feeds
def feed_urls():
    uid = cfg('GOODREADS_USER_ID', required=True)
    key = os.environ.get('GOODREADS_RSS_KEY', '')
    q = ('&key=' + urllib.parse.quote(key)) if key else ''
    return {
        'updates': f'https://www.goodreads.com/user/updates_rss/{uid}?{q.lstrip("&")}'.rstrip('?'),
        'reading': f'https://www.goodreads.com/review/list_rss/{uid}?shelf=currently-reading{q}',
    }


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml, application/xml, text/xml'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rss_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter('item'):
        def t(tag):
            el = it.find(tag)
            return (el.text or '').strip() if el is not None and el.text else ''
        out.append({'title': html.unescape(t('title')), 'description': t('description'), 'link': t('link'),
                    'guid': t('guid') or t('link'), 'pubDate': t('pubDate'),
                    'book_id': t('book_id'), 'isbn': t('isbn'), 'isbn13': t('isbn13'),
                    'author_name': t('author_name'), 'num_pages': t('num_pages'),
                    'user_read_at': t('user_read_at'), 'user_date_added': t('user_date_added')})
    return out


TAG = re.compile(r'<[^>]+>')


def strip_tags(s):
    return html.unescape(TAG.sub(' ', s or '')).replace('\xa0', ' ')


def squeeze(s):
    return re.sub(r'\s+', ' ', s or '').strip()


# The sentences Goodreads writes into the updates feed. The feed's wording is not documented, so
# every pattern is anchored loosely and the raw title is kept on the event for checking.
PATTERNS = [
    ('progress', re.compile(r'^(?P<who>.+?) is on page (?P<page>\d+) of (?P<total>\d+) of (?P<title>.+?)\s*\.?\s*$', re.I)),
    ('progress', re.compile(r'^(?P<who>.+?) is (?P<pct>\d+)% done with (?P<title>.+?)\s*\.?\s*$', re.I)),
    ('started', re.compile(r'^(?P<who>.+?) (?:started reading|is currently reading) (?P<title>.+?)\s*$', re.I)),
    ('finished', re.compile(r'^(?P<who>.+?) finished reading (?P<title>.+?)\s*$', re.I)),
    ('dnf', re.compile(r'^(?P<who>.+?) did not finish (?P<title>.+?)\s*$', re.I)),
]
BOOK_ANCHOR = re.compile(r'<a[^>]*/book/show/(?P<id>\d+)[^>]*>(?P<title>[^<]+)</a>', re.I)
NOTE_IN_DESC = re.compile(r'</a>\.?\s*(?:<br\s*/?>|\s)*["\u201c](?P<note>[^"\u201d]{1,500})["\u201d]', re.I)


def unquote(t):
    t = squeeze(t)
    return t[1:-1].strip() if len(t) > 2 and t[0] in "'\u2018\u201c\"" and t[-1] in "'\u2019\u201d\"" else t
AUTHOR_IN_DESC = re.compile(r'\bby\s+<a[^>]*author/show[^>]*>(?P<author>[^<]+)</a>', re.I)
AUTHOR_PLAIN = re.compile(r'\bby\s+(?P<author>[A-Z][^<,;\n]{1,60}?)\s*(?:$|[\n<(])')
BOOK_LINK = re.compile(r'/book/show/(?P<id>\d+)')


def parse_update(item):
    """Turn one updates-feed item into an event dict, or None for items we don't track."""
    desc_html = html.unescape(item['description'] or '')
    desc_text = squeeze(strip_tags(desc_html))
    # the <title> element wraps and truncates long sentences; the description carries the whole one
    text = desc_text or squeeze(item['title'])
    if re.search(r'\bliked\b', text) or str(item['guid']).startswith(('Rating', 'Comment', 'Friend')):
        return None  # likes, comments and friendings mention other people's reading
    for kind, pat in PATTERNS:
        m = pat.match(text) or pat.match(squeeze(item['title']))
        if not m or len(m.group('who').split()) > 3:
            continue
        title = unquote(m.group('title'))
        anchor = BOOK_ANCHOR.search(desc_html)
        if anchor:
            title = squeeze(anchor.group('title'))
        # the feed sometimes appends " by Author" to the title sentence
        author = None
        mb = re.match(r'^(?P<t>.+?)\s+by\s+(?P<a>[^()]+?)$', title)
        if mb and len(mb.group('a')) < 60:
            title, author = squeeze(mb.group('t')), squeeze(mb.group('a'))
        if not author:
            ma = AUTHOR_IN_DESC.search(desc_html) or AUTHOR_PLAIN.search(desc_text)
            if ma:
                author = squeeze(ma.group('author'))
        mid = BOOK_LINK.search(desc_html) or BOOK_LINK.search(item['link'] or '')
        note = NOTE_IN_DESC.search(desc_html)
        at = None
        if item['pubDate']:
            try:
                at = parsedate_to_datetime(item['pubDate'])
            except Exception:
                at = None
        ev = {'kind': kind, 'title': title, 'author': author, 'gr_book_id': mid.group('id') if mid else None,
              'guid': item['guid'], 'at': at.isoformat() if at else None, 'raw': text, 'note': squeeze(note.group('note')) if note else None,
              'review_url': item['link'] if '/review/show/' in (item['link'] or '') else None}
        if 'page' in m.groupdict() and m.group('page'):
            ev['page'], ev['total'] = int(m.group('page')), int(m.group('total'))
            ev['percent'] = round(100 * ev['page'] / ev['total']) if ev['total'] else None
        elif 'pct' in m.groupdict() and m.group('pct'):
            ev['percent'] = int(m.group('pct'))
        return ev
    return None


# ---------------------------------------------------------------- Notion
class Notion:
    def __init__(self, token, dry_run=False):
        self.token, self.dry_run = token, dry_run

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(NOTION_API + path, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                detail = e.read().decode('utf-8', 'replace')[:500]
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f'Notion {method} {path} -> {e.code}: {detail}')
        raise RuntimeError(f'Notion {method} {path}: gave up after retries')

    def query_all(self, ds_id, flt=None, page_size=100):
        cursor, out = None, []
        while True:
            body = {'page_size': page_size}
            if flt:
                body['filter'] = flt
            if cursor:
                body['start_cursor'] = cursor
            res = self._call('POST', f'/data_sources/{ds_id}/query', body)
            out.extend(res.get('results', []))
            if not res.get('has_more'):
                return out
            cursor = res.get('next_cursor')

    def create_page(self, ds_id, props):
        if self.dry_run:
            log('DRY create', json.dumps(props, ensure_ascii=False)[:300])
            return {'id': 'dry-run'}
        return self._call('POST', '/pages', {'parent': {'data_source_id': ds_id}, 'properties': props})

    def update_page(self, page_id, props):
        if self.dry_run:
            log('DRY update', page_id, json.dumps(props, ensure_ascii=False)[:300])
            return {'id': page_id}
        return self._call('PATCH', f'/pages/{page_id}', {'properties': props})


def prop_text(p):
    if not p:
        return ''
    arr = p.get('title') or p.get('rich_text') or []
    return ''.join(t.get('plain_text', '') for t in arr)


def prop_rel(p):
    return [r['id'] for r in (p or {}).get('relation', [])]


def prop_date(p):
    d = (p or {}).get('date')
    return d.get('start') if d else None


def prop_select(p):
    s = (p or {}).get('select')
    return s.get('name') if s else None


# ---------------------------------------------------------------- matching
STOP = re.compile(r'^(the|a|an)\s+', re.I)


def norm(s):
    s = (s or '').lower().replace('&', 'and')
    s = re.sub(r'[‘’“”]', "'", s)
    s = re.sub(r'[^a-z0-9\' ]+', ' ', s)
    s = STOP.sub('', squeeze(s))
    return squeeze(s)


def main_title(s):
    return norm(re.split(r'[:(—]| - ', s or '', 1)[0])


def surname(s):
    parts = squeeze(s or '').split(' ')
    return norm(parts[-1]) if parts else ''


class Library:
    """Books and Authors pulled once per run, for matching feed titles to Notion pages."""

    def __init__(self, notion):
        self.n = notion
        self.books = []
        self.authors = {}

    def load(self):
        for a in self.n.query_all(cfg('NOTION_AUTHORS_DS')):
            self.authors[a['id']] = prop_text(a['properties'].get('Name'))
        for b in self.n.query_all(cfg('NOTION_BOOKS_DS')):
            title = prop_text(b['properties'].get('Title'))
            names = [self.authors.get(i, '') for i in prop_rel(b['properties'].get('Author'))]
            self.books.append({'id': b['id'], 'title': title, 'norm': norm(title), 'main': main_title(title),
                               'authors': names, 'surnames': {surname(x) for x in names if x}})
        log(f'library: {len(self.books)} books, {len(self.authors)} authors')

    def load_raw(self, raw_dir):
        """Books, Authors and Reads from data/raw/*.json (the dashboard's pull), for offline runs."""
        rd = Path(raw_dir)
        pid = lambda u: u[23:]
        for a in json.loads(rd.joinpath('authors.json').read_text(encoding='utf-8')):
            self.authors[pid(a['url'])] = a.get('Name') or ''
        for b in json.loads(rd.joinpath('books.json').read_text(encoding='utf-8')):
            title = b.get('Title') or ''
            names = [self.authors.get(pid(u), '') for u in json.loads(b.get('Author') or '[]')]
            self.books.append({'id': pid(b['url']), 'title': title, 'norm': norm(title), 'main': main_title(title), 'authors': names, 'surnames': {surname(x) for x in names if x}})
        self.raw_reads = []
        for r in json.loads(rd.joinpath('reads.json').read_text(encoding='utf-8')):
            bk = json.loads(r.get('Book') or '[]')
            self.raw_reads.append({'id': pid(r['url']), 'book': pid(bk[0]) if bk else None, 'title': r.get('Read') or '', 'outcome': r.get('Outcome') or None,
                                   'started': r.get('date:Started:start') or None, 'ended': r.get('date:Ended:start') or None})
        log(f'library (offline, {rd}): {len(self.books)} books, {len(self.authors)} authors, {len(self.raw_reads)} reads')

    def match(self, title, author):
        nt, mt, sn = norm(title), main_title(title), surname(author) if author else ''
        cands = [b for b in self.books if b['norm'] == nt] or [b for b in self.books if b['main'] == mt and mt] \
            or [b for b in self.books if mt and (b['norm'].startswith(mt) or nt.startswith(b['main']))]
        if len(cands) > 1 and sn:
            by_author = [b for b in cands if sn in b['surnames']]
            if by_author:
                cands = by_author
        if len(cands) == 1:
            return cands[0], 'ok'
        if not cands:
            return None, 'no book with that title'
        return None, 'several books match: ' + '; '.join(f"{b['title']} ({', '.join(b['authors'])})" for b in cands[:4])


def read_norm(page):
    p = page['properties']
    return {'id': page['id'], 'title': prop_text(p.get('Read')), 'outcome': prop_select(p.get('Outcome')),
            'started': prop_date(p.get('Started')), 'ended': prop_date(p.get('Ended'))}


class Store:
    """Where Reads are looked up and events written: Notion over the API, or the raw pull (read-only)."""

    def __init__(self, notion=None, lib=None):
        self.notion, self.lib = notion, lib

    def open_read(self, book_id):
        if self.notion:
            r = open_read(self.notion, book_id)
            return read_norm(r) if r else None
        rs = [r for r in self.lib.raw_reads if r['book'] == book_id and not r['outcome']]
        rs.sort(key=lambda r: r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def recent_read(self, book_id):
        if self.notion:
            r = recent_read(self.notion, book_id)
            return read_norm(r) if r else None
        rs = sorted([r for r in self.lib.raw_reads if r['book'] == book_id], key=lambda r: r['ended'] or r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def all_open(self):
        if self.notion:
            return [read_norm(r) for r in self.notion.query_all(cfg('NOTION_READS_DS'), {'property': 'Outcome', 'select': {'is_empty': True}})]
        return [r for r in self.lib.raw_reads if not r['outcome']]


def open_read(notion, book_id):
    """The Read on this Book with no Outcome yet (currently reading), newest first."""
    rows = notion.query_all(cfg('NOTION_READS_DS'), {'and': [
        {'property': 'Book', 'relation': {'contains': book_id}},
        {'property': 'Outcome', 'select': {'is_empty': True}}]})
    rows.sort(key=lambda r: prop_date(r['properties'].get('Started')) or '', reverse=True)
    return rows[0] if rows else None


def recent_read(notion, book_id):
    """Any Read on this Book, newest first (for a Finished event that Dan already logged)."""
    rows = notion.query_all(cfg('NOTION_READS_DS'), {'property': 'Book', 'relation': {'contains': book_id}})
    rows.sort(key=lambda r: prop_date(r['properties'].get('Ended')) or prop_date(r['properties'].get('Started')) or '', reverse=True)
    return rows[0] if rows else None


# ---------------------------------------------------------------- writes
KIND_NAME = {'started': 'Started', 'progress': 'Progress', 'finished': 'Finished', 'dnf': 'DNF'}


def log_props(ev, book, read):
    when = ev['at'] or datetime.now(timezone.utc).isoformat()
    name = f"{book['title']} · {KIND_NAME[ev['kind']]}" + (f" p.{ev['page']}" if ev.get('page') else '') + f" · {when[:10]}"
    props = {
        'Name': {'title': [{'text': {'content': name[:200]}}]},
        'Book': {'relation': [{'id': book['id']}]},
        'When': {'date': {'start': when}},
        'Kind': {'select': {'name': KIND_NAME[ev['kind']]}},
        'Source': {'select': {'name': 'Goodreads'}},
        'Goodreads id': {'rich_text': [{'text': {'content': ev['guid'][:2000]}}]},
        'Raw': {'rich_text': [{'text': {'content': ev['raw'][:2000]}}]},
    }
    if read:
        props['Read'] = {'relation': [{'id': read['id']}]}
    if ev.get('page') is not None:
        props['Page'] = {'number': ev['page']}
    if ev.get('total') is not None:
        props['Of pages'] = {'number': ev['total']}
    if ev.get('percent') is not None:
        props['Percent'] = {'number': ev['percent']}
    if ev.get('note'):
        props['Note'] = {'rich_text': [{'text': {'content': ev['note'][:2000]}}]}
    return props


def already_logged(notion, guid):
    rows = notion.query_all(cfg('NOTION_LOG_DS'), {'property': 'Goodreads id', 'rich_text': {'equals': guid}}, page_size=1)
    return bool(rows)


def check_reading(shelf_items, lib, store):
    """Goodreads' currently-reading shelf against Notion's open Reads, both ways."""
    out = []
    seen_books = set()
    for it in shelf_items:
        book, why = lib.match(it['title'], it['author_name'])
        since = it['user_date_added'][:16] if it['user_date_added'] else None
        if not book:
            out.append({'what': 'Goodreads: currently reading, but no matching Book in Notion', 'title': it['title'], 'author': it['author_name'], 'since': since, 'reason': why})
            continue
        seen_books.add(book['id'])
        rd = store.open_read(book['id'])
        if not rd:
            last = store.recent_read(book['id'])
            out.append({'what': 'Goodreads: currently reading, but no open Read in Notion', 'title': book['title'], 'since': since,
                        'last_read_in_notion': (last['title'] + ' (' + (last['outcome'] or 'open') + (', ended ' + last['ended'] if last['ended'] else '') + ')') if last else None})
    for rd in store.all_open():
        bid = rd.get('book')
        if bid is None and store.notion:
            continue  # the API shape carries no book id here; the shelf side already covers it
        if bid not in seen_books:
            out.append({'what': 'Notion: open Read, but not on the Goodreads currently-reading shelf', 'title': rd['title'], 'started': rd['started']})
    return out


def run(args):
    load_env()
    state = load_state()
    urls = feed_urls() if not (args.fixture and args.shelf_fixture) else {}
    try:
        items = rss_items(Path(args.fixture).read_bytes() if args.fixture else fetch(urls['updates']))
        shelf = rss_items(Path(args.shelf_fixture).read_bytes() if args.shelf_fixture else fetch(urls['reading'])) if not args.parse_only else []
    except Exception as e:
        sys.exit(f'could not fetch Goodreads: {e}')
    events = [e for e in (parse_update(it) for it in items) if e]
    log(f'feed: {len(items)} items, {len(events)} tracked events; currently-reading shelf: {len(shelf)}')
    if args.parse_only:
        for e in events:
            print(json.dumps(e, ensure_ascii=False))
        return 0

    new = [e for e in events if e['guid'] not in state['seen']]
    offline = bool(args.offline_library)
    notion = None if offline else Notion(cfg('NOTION_TOKEN', required=True), dry_run=args.dry_run)
    if not offline:
        cfg('NOTION_LOG_DS', required=True)
    lib = Library(notion)
    if offline:
        lib.load_raw(args.offline_library)
    else:
        lib.load()
    store = Store(notion, lib)
    writer = notion or Notion(None, dry_run=True)
    unmatched, written = [], 0
    for ev in sorted(new, key=lambda e: e['at'] or ''):
        book, why = None, ''
        cached = state['book_cache'].get(ev['gr_book_id'] or '')
        if cached:
            book = next((b for b in lib.books if b['id'] == cached), None)
        if not book:
            book, why = lib.match(ev['title'], ev['author'])
        if not book:
            unmatched.append({**ev, 'reason': why})
            log('UNMATCHED', ev['kind'], ev['title'], '-', why)
            continue
        if ev['gr_book_id'] and not offline:
            state['book_cache'][ev['gr_book_id']] = book['id']
        read = store.open_read(book['id']) or (store.recent_read(book['id']) if ev['kind'] in ('finished', 'dnf') else None)
        if not read:
            unmatched.append({**ev, 'reason': f"no open Read in Notion for {book['title']}", 'book_id': book['id']})
            log('UNMATCHED', ev['kind'], book['title'], '- no open Read in Notion')
            continue
        if notion and not args.dry_run and already_logged(notion, ev['guid']):
            log('already in the log', ev['guid'])
        else:
            writer.create_page(cfg('NOTION_LOG_DS') or 'reading-log', log_props(ev, book, read))
            written += 1
            log('logged', ev['kind'], book['title'], ev.get('page') or ev.get('percent') or '', '->', read['title'])
        if ev['kind'] == 'started' and ev['at'] and not read.get('started'):
            writer.update_page(read['id'], {'Started': {'date': {'start': ev['at'][:10]}}, 'Dates estimated': {'checkbox': False}})
            log('set Started on', read['title'], ev['at'][:10])
        if not offline:
            state['seen'][ev['guid']] = ev['at'] or datetime.now().isoformat()
    if not new:
        log('no new events')
    checks = check_reading(shelf, lib, store)
    for c in checks:
        log('CHECK', json.dumps(c, ensure_ascii=False))
    report = {'at': datetime.now().isoformat(timespec='seconds'), 'unmatched_events': unmatched, 'reading_check': checks}
    if unmatched or checks:
        UNMATCHED_FILE.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
        log(f'{len(unmatched)} unmatched event(s), {len(checks)} reading-check item(s) written to {UNMATCHED_FILE.name}')
    elif UNMATCHED_FILE.exists():
        UNMATCHED_FILE.unlink()
    if not args.dry_run and not offline:
        state['runs'] = (state.get('runs') or [])[-30:] + [{'at': report['at'], 'new': len(new), 'written': written, 'unmatched': len(unmatched), 'checks': len(checks)}]
        save_state(state)
    log(f'done: {written} logged, {len(unmatched)} unmatched, {len(checks)} check item(s)' + (' (dry run, nothing saved)' if args.dry_run or offline else ''))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='read Notion, print the writes, save nothing')
    ap.add_argument('--fixture', help='parse this saved RSS file instead of fetching')
    ap.add_argument('--parse-only', action='store_true', help='print parsed events and stop; no Notion')
    ap.add_argument('--shelf-fixture', help='parse this saved currently-reading shelf RSS instead of fetching')
    ap.add_argument('--offline-library', metavar='DIR', help='match against data/raw/*.json instead of Notion; implies dry run')
    sys.exit(run(ap.parse_args()))
