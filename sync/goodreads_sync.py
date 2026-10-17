#!/usr/bin/env python3
"""Nightly Goodreads -> Notion sync.

Reads Dan's public Goodreads RSS feeds (the updates stream and the currently-reading shelf),
turns "started reading" / "is on page N of M" / "is N% done" / "finished reading" /
"did not finish" items into events, matches each to a Book and its open Read in Notion, and

  * appends one row per event to the Reading log database (append-only event stream),
  * sets Reads.Started (and clears Dates estimated) when a Started event lands on an open
    Read whose Started is blank or estimated.

Invariants (docs/SYNC_REVIEW.md, "Dan's decisions"):
  1. Never deletes anything. Never changes a Book, an Edition, or a Read's Edition once set.
  2. Changes to a Read are limited to: Started when blank or estimated; Ended/Outcome when open
     (close) or when closed by the sync within 14 days (reopen); Pages read on DNF only; Notes appended.
  3. Every automatic change is written to the Read's Notes with the date and "from Goodreads", and
     every uncertain case becomes a question rather than a guess.
  4. A rerun with the same feed produces no new rows and no new changes (dedupe by Goodreads id and
     by Read + Kind + date + Page).

Dates: feed timestamps are Pacific; every date written to Notion (Started, Ended, the log row's Name)
is the event's date in Dan's zone (SYNC_TZ, default America/New_York). The full timestamp is kept in When.

Config comes from the environment, then sync/.env (see .env.example). State (seen item ids, match
cache) lives in sync/state.json. Only stdlib is used.

Usage:
  goodreads_sync.py                 fetch, match, write
  goodreads_sync.py --dry-run       fetch and match, read Notion, print the writes, write nothing
  goodreads_sync.py --fixture f.xml parse a saved feed file instead of fetching
  goodreads_sync.py --parse-only    parse the feed(s) and print events; no Notion at all
  goodreads_sync.py --fixture f.xml --shelf-fixture s.xml --offline-library data/raw [--plan out.json]
                                    match against the repo's raw pull; prints (and saves) what it would write
"""
import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / '.env'
STATE_FILE = HERE / 'state.json'
UNMATCHED_FILE = HERE / 'unmatched.json'

NOTION_API = 'https://api.notion.com/v1'
NOTION_VERSION = '2025-09-03'
USER_AGENT = 'reading-library-sync/1.0'  # generic on purpose: no personal details leave the machine

# Data sources from the runbook ("Library runbook (for Claude)").
DEFAULTS = {
    'NOTION_BOOKS_DS': '21e3da73-9608-41f8-a857-bd226043a391',
    'NOTION_READS_DS': '05439130-377c-4e0b-948e-7c1d7ffb065e',
    'NOTION_AUTHORS_DS': 'f96682e9-8772-4493-abff-ec079148f1b2',
    'NOTION_EDITIONS_DS': 'd6b4c6a6-4099-4b89-b135-0fa50a918c57',
    'NOTION_LOG_DS': 'ced88101-f6c5-4e4e-9c1f-8305afeb2e7e',  # Reading log, created 2026-10-16 under Media › Library
    'SYNC_TZ': 'America/New_York',
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


QUIET = False  # tests set this to keep the log out of the test output


def log(*a):
    if not QUIET:
        print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- time
def tz():
    return ZoneInfo(cfg('SYNC_TZ'))


def local_date(iso):
    """The calendar date, in Dan's zone, of an ISO timestamp. A date-only string is returned as is."""
    if not iso:
        return None
    if len(iso) == 10:
        return iso
    dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz()).date().isoformat()


def now_iso():
    return datetime.now(timezone.utc).astimezone(tz()).isoformat(timespec='seconds')


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
                    'author_name': t('author_name'), 'num_pages': t('book/num_pages') or t('num_pages'),
                    'user_read_at': t('user_read_at'), 'user_date_added': t('user_date_added'),
                    'user_date_created': t('user_date_created'), 'user_shelves': t('user_shelves')})
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
    ('wishlisted', re.compile(r'^(?P<who>.+?) wants to read (?P<title>.+?)\s*$', re.I)),
]
BOOK_ANCHOR = re.compile(r'<a[^>]*/book/show/(?P<id>\d+)[^>]*>(?P<title>[^<]+)</a>', re.I)
NOTE_IN_DESC = re.compile(r'</a>\.?\s*(?:<br\s*/?>|\s)*["“](?P<note>[^"”]{1,500})["”]', re.I)
AUTHOR_IN_DESC = re.compile(r'\bby(?:</span>)?\s+<a[^>]*author/show[^>]*>(?P<author>[^<]+)</a>', re.I)
AUTHOR_PLAIN = re.compile(r'\bby\s+(?P<author>[A-Z][^<,;\n]{1,60}?)\s*(?:$|[\n<(])')
BOOK_LINK = re.compile(r'/book/show/(?P<id>\d+)')


def unquote(t):
    t = squeeze(t)
    return t[1:-1].strip() if len(t) > 2 and t[0] in "'‘“\"" and t[-1] in "'’”\"" else t


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
            ma = AUTHOR_IN_DESC.search(desc_html) or AUTHOR_PLAIN.search(desc_text) or AUTHOR_PLAIN.search(squeeze(item['title']))
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
            ev['percent'] = round(100 * ev['page'] / ev['total'], 1) if ev['total'] else None
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

    def retrieve_page(self, page_id):
        return self._call('GET', f'/pages/{page_id}')

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
    return ''.join(t.get('plain_text') or (t.get('text') or {}).get('content') or '' for t in arr)  # read or write shape


def prop_rel(p):
    return [r['id'] for r in (p or {}).get('relation', [])]


def prop_date(p):
    d = (p or {}).get('date')
    return d.get('start') if d else None


def prop_select(p):
    s = (p or {}).get('select')
    return s.get('name') if s else None


def prop_checkbox(p):
    return bool((p or {}).get('checkbox'))


def prop_number(p):
    return (p or {}).get('number')


def rt(s):
    return {'rich_text': [{'text': {'content': (s or '')[:2000]}}]}


# ---------------------------------------------------------------- matching
STOP = re.compile(r'^(the|a|an)\s+', re.I)
YEAR_RANGE = re.compile(r',?\s*\d{4}\s*[-–]\s*\d{4}\s*$')
VOLUME = re.compile(r'\b(?:vol(?:ume)?\.?|book)\s*\d+\b', re.I)


# Goodreads title -> Notion title, both normalised; only for works whose titles genuinely differ
ALIASES = {'nineteen eighty four': '1984', 'autobiography and other writings': 'benjamin franklin the autobiography'}


def norm(s):
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace('&', 'and')
    s = re.sub(r'[‘’“”]', "'", s)
    s = re.sub(r'[^a-z0-9\' ]+', ' ', s)
    s = STOP.sub('', squeeze(s))
    s = squeeze(s)
    return ALIASES.get(s, s)


def main_title(s):
    return norm(re.split(r'[:(—]| - ', s or '', 1)[0])


def segments(title):
    """The pieces of a Goodreads title: main title, subtitle, series, minus volume numbers and year ranges."""
    t = YEAR_RANGE.sub('', title or '')
    out = []
    for piece in re.split(r'[:(—]| - ', t):
        piece = YEAR_RANGE.sub('', piece)
        for p in (piece, VOLUME.sub(' ', piece)):
            n = norm(p)
            if n and n not in out:
                out.append(n)
    return out


def surname(s):
    parts = squeeze(s or '').split(' ')
    return norm(parts[-1]) if parts else ''


class Library:
    """Books and Authors pulled once per run, for matching feed titles to Notion pages."""

    def __init__(self, notion):
        self.n = notion
        self.books = []
        self.authors = {}
        self.raw_reads = []

    def load_rows(self, rows):
        """rows: [{'id', 'title', 'authors': [names]}]"""
        for r in rows:
            names = [x for x in r.get('authors') or [] if x]
            self.books.append({'id': r['id'], 'title': r['title'], 'norm': norm(r['title']), 'main': main_title(r['title']),
                               'authors': names, 'surnames': {surname(x) for x in names}})

    def load(self):
        for a in self.n.query_all(cfg('NOTION_AUTHORS_DS')):
            self.authors[a['id']] = prop_text(a['properties'].get('Name'))
        rows = []
        for b in self.n.query_all(cfg('NOTION_BOOKS_DS')):
            rows.append({'id': b['id'], 'title': prop_text(b['properties'].get('Title')),
                         'authors': [self.authors.get(i, '') for i in prop_rel(b['properties'].get('Author'))]})
        self.load_rows(rows)
        log(f'library: {len(self.books)} books, {len(self.authors)} authors')

    def load_raw(self, raw_dir):
        """Books, Authors and Reads from data/raw/*.json (the dashboard's pull), for offline runs."""
        rd = Path(raw_dir)
        pid = lambda u: u[23:]  # noqa: E731
        for a in json.loads(rd.joinpath('authors.json').read_text(encoding='utf-8')):
            self.authors[pid(a['url'])] = a.get('Name') or ''
        rows = []
        for b in json.loads(rd.joinpath('books.json').read_text(encoding='utf-8')):
            rows.append({'id': pid(b['url']), 'title': b.get('Title') or '', 'authors': [self.authors.get(pid(u), '') for u in json.loads(b.get('Author') or '[]')]})
        self.load_rows(rows)
        for r in json.loads(rd.joinpath('reads.json').read_text(encoding='utf-8')):
            bk = json.loads(r.get('Book') or '[]')
            self.raw_reads.append({'id': pid(r['url']), 'book': pid(bk[0]) if bk else None, 'title': r.get('Read') or '', 'outcome': r.get('Outcome') or None,
                                   'started': r.get('date:Started:start') or None, 'ended': r.get('date:Ended:start') or None,
                                   'dates_estimated': r.get('Dates estimated') == '__YES__', 'edition': [pid(u) for u in json.loads(r.get('Edition') or '[]')],
                                   'edition_inferred': r.get('Edition inferred') == '__YES__', 'pages_read': r.get('Pages read'),
                                   'format_read': r.get('Format read') or None, 'notes': ''})
        log(f'library (offline, {rd}): {len(self.books)} books, {len(self.authors)} authors, {len(self.raw_reads)} reads')

    def match(self, title, author):
        nt, mt, sn = norm(title), main_title(title), surname(author) if author else ''
        # Goodreads often writes "Series title, Books 1-5: The Real Title" or "(Series, #1)": try each segment too
        segs = segments(title)
        cands = [b for b in self.books if b['norm'] == nt] or [b for b in self.books if b['norm'] in segs] \
            or [b for b in self.books if b['main'] in segs and b['main'] != b['norm']] \
            or [b for b in self.books if b['main'] == mt and mt] \
            or [b for b in self.books if mt and (b['norm'].startswith(mt) or nt.startswith(b['main']))]
        if len(cands) > 1 and sn:
            by_author = [b for b in cands if sn in b['surnames']]
            if by_author:
                cands = by_author
        if len(cands) > 1:
            # the longest Notion title that the Goodreads title starts with wins ("Stalin: Waiting for Hitler" over "Stalin")
            pref = sorted([b for b in cands if nt.startswith(b['norm'])], key=lambda b: -len(b['norm']))
            if pref and (len(pref) == 1 or len(pref[0]['norm']) > len(pref[1]['norm'])):
                cands = [pref[0]]
        if len(cands) > 1:
            # the Notion title that contains the most of the Goodreads title's pieces ("Paradoxes of Power" picks vol. 1)
            scored = [(sum(1 for s in segs if s in b['norm'] and s != b['main']), b) for b in cands]
            best = max(s for s, _ in scored)
            top = [b for s, b in scored if s == best]
            if best > 0 and len(top) == 1:
                cands = top
        if len(cands) == 1:
            return cands[0], 'ok'
        if not cands:
            return None, 'no book with that title'
        return None, 'several books match: ' + '; '.join(f"{b['title']} ({', '.join(b['authors'])})" for b in cands[:4])


def read_norm(page):
    p = page['properties']
    return {'id': page['id'], 'title': prop_text(p.get('Read')), 'outcome': prop_select(p.get('Outcome')),
            'started': prop_date(p.get('Started')), 'ended': prop_date(p.get('Ended')),
            'book': (prop_rel(p.get('Book')) or [None])[0], 'dates_estimated': prop_checkbox(p.get('Dates estimated')),
            'edition': prop_rel(p.get('Edition')), 'edition_inferred': prop_checkbox(p.get('Edition inferred')),
            'pages_read': prop_number(p.get('Pages read')), 'format_read': prop_select(p.get('Format read')),
            'notes': prop_text(p.get('Notes'))}


KIND_NAME = {'started': 'Started', 'progress': 'Progress', 'finished': 'Finished', 'dnf': 'DNF'}


def log_key(read_id, kind_name, date, page):
    return (read_id, kind_name, date, page)


class Store:
    """Where Reads are looked up and events written: Notion over the API, or the raw pull (read-only).

    Every write goes through here so dry runs, offline runs and tests see the same plan. Dedupe is
    by Goodreads id and by (Read, Kind, local date, Page), checked in Notion and within the run."""

    def __init__(self, notion=None, lib=None, dry_run=False, today=None):
        self.notion, self.lib, self.dry_run = notion, lib, dry_run
        self.today = today or local_date(now_iso())
        self.seen_guids, self.seen_keys, self.plan = set(), set(), []

    # ---- reads
    def _reads(self, flt):
        return [read_norm(r) for r in self.notion.query_all(cfg('NOTION_READS_DS'), flt)]

    def reads_for_book(self, book_id):
        if self.notion:
            return self._reads({'property': 'Book', 'relation': {'contains': book_id}})
        return [dict(r) for r in self.lib.raw_reads if r['book'] == book_id]

    def open_read(self, book_id):
        rs = [r for r in self.reads_for_book(book_id) if not r['outcome']]
        rs.sort(key=lambda r: r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def recent_read(self, book_id):
        rs = sorted(self.reads_for_book(book_id), key=lambda r: r['ended'] or r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def all_open(self):
        if self.notion:
            return self._reads({'property': 'Outcome', 'select': {'is_empty': True}})
        return [dict(r) for r in self.lib.raw_reads if not r['outcome']]

    # ---- reading log
    def already_logged(self, guid):
        if guid in self.seen_guids:
            return True
        if not self.notion:
            return False
        return bool(self.notion.query_all(cfg('NOTION_LOG_DS'), {'property': 'Goodreads id', 'rich_text': {'equals': guid}}, page_size=1))

    def log_exists(self, read_id, kind_name, date, page):
        """The secondary dedupe key: a row on the same Read with the same Kind, local date and Page."""
        if log_key(read_id, kind_name, date, page) in self.seen_keys:
            return True
        if not self.notion:
            return False
        rows = self.notion.query_all(cfg('NOTION_LOG_DS'), {'and': [
            {'property': 'Read', 'relation': {'contains': read_id}}, {'property': 'Kind', 'select': {'equals': kind_name}}]})
        for r in rows:
            p = r['properties']
            if local_date(prop_date(p.get('When'))) == date and prop_number(p.get('Page')) == page:
                return True
        return False

    def create_log(self, ev, book, read):
        props = log_props(ev, book, read)
        self.seen_guids.add(ev['guid'])
        self.seen_keys.add(log_key(read['id'], KIND_NAME[ev['kind']], local_date(ev['at']), ev.get('page')))
        return self._create(cfg('NOTION_LOG_DS'), props)

    # ---- writes
    def _create(self, ds, props):
        self.plan.append({'op': 'create', 'ds': ds, 'props': props})
        if self.dry_run or not self.notion:
            log('PLAN create', json.dumps(props, ensure_ascii=False)[:300])
            return {'id': 'planned'}
        return self.notion.create_page(ds, props)

    def _update(self, page_id, props):
        self.plan.append({'op': 'update', 'page': page_id, 'props': props})
        if self.dry_run or not self.notion:
            log('PLAN update', page_id, json.dumps(props, ensure_ascii=False)[:300])
            return {'id': page_id}
        return self.notion.update_page(page_id, props)

    def update_read(self, read, props, note):
        """Change a Read and append the reason to its Notes (invariant 3)."""
        notes = (read.get('notes') or '').rstrip()
        line = f'{note} from Goodreads {self.today}'
        props = dict(props, Notes=rt((notes + '\n' + line) if notes else line))
        read['notes'] = (notes + '\n' + line) if notes else line
        return self._update(read['id'], props)


# ---------------------------------------------------------------- writes
def log_props(ev, book, read):
    when = ev['at'] or now_iso()
    day = local_date(when)
    name = f"{book['title']} · {KIND_NAME[ev['kind']]}" + (f" p.{ev['page']}" if ev.get('page') else '') + f" · {day}"
    props = {
        'Name': {'title': [{'text': {'content': name[:200]}}]},
        'Book': {'relation': [{'id': book['id']}]},
        'When': {'date': {'start': when}},
        'Kind': {'select': {'name': KIND_NAME[ev['kind']]}},
        'Source': {'select': {'name': 'Goodreads'}},
        'Goodreads id': rt(ev['guid']),
        'Raw': rt(ev['raw']),
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
        props['Note'] = rt(ev['note'])
    return props


def check_reading(shelf_items, lib, store):
    """Goodreads' currently-reading shelf against Notion's open Reads, both ways."""
    out = []
    seen_books = set()
    for it in shelf_items:
        book, why = lib.match(it['title'], it['author_name'])
        shelved = it['user_date_added'][:16] if it['user_date_added'] else None
        if not book:
            out.append({'what': 'Goodreads: currently reading, but no matching Book in Notion', 'title': it['title'], 'author': it['author_name'], 'shelved': shelved, 'reason': why})
            continue
        seen_books.add(book['id'])
        rd = store.open_read(book['id'])
        if not rd:
            last = store.recent_read(book['id'])
            out.append({'what': 'Goodreads: currently reading, but no open Read in Notion', 'title': book['title'], 'shelved': shelved,
                        'last_read_in_notion': (last['title'] + ' (' + (last['outcome'] or 'open') + (', ended ' + last['ended'] if last['ended'] else '') + ')') if last else None})
    for rd in store.all_open():
        if rd.get('book') not in seen_books:
            out.append({'what': 'Notion: open Read, but not on the Goodreads currently-reading shelf', 'title': rd['title'], 'started': rd['started']})
    return out


# ---------------------------------------------------------------- the run
def run_sync(notion, items, shelf, state, *, dry_run=False, offline_lib=None, now=None):
    """Match and write one batch of feed items. `notion` is a Notion (or a test double); None with
    `offline_lib` for an offline plan. Returns the report; the writes are in report['plan']."""
    today = local_date(now or now_iso())
    events = [e for e in (parse_update(it) for it in items) if e]
    log(f'feed: {len(items)} items, {len(events)} tracked events; currently-reading shelf: {len(shelf)}')
    new = [e for e in events if e['guid'] not in state['seen']]
    lib = offline_lib or Library(notion)
    if not offline_lib:
        lib.load()
    store = Store(notion, lib, dry_run=dry_run, today=today)
    books_by_id = {b['id']: b for b in lib.books}
    unmatched, written, already = [], 0, 0
    for ev in sorted(new, key=lambda e: e['at'] or ''):
        if ev['kind'] not in KIND_NAME:
            log('skipped', ev['kind'], ev['title'], '(not handled yet)')
            continue
        book, why = None, ''
        cached = state['book_cache'].get(ev['gr_book_id'] or '')
        if cached:
            book = books_by_id.get(cached)
            if not book:
                log('book cache: dropping stale entry', ev['gr_book_id'], '->', cached)
                del state['book_cache'][ev['gr_book_id']]
        if not book:
            book, why = lib.match(ev['title'], ev['author'])
        if not book:
            unmatched.append({**ev, 'reason': why})
            log('UNMATCHED', ev['kind'], ev['title'], '-', why)
            continue
        if ev['gr_book_id']:
            state['book_cache'][ev['gr_book_id']] = book['id']
        read = store.open_read(book['id']) or (store.recent_read(book['id']) if ev['kind'] in ('finished', 'dnf') else None)
        if not read:
            unmatched.append({**ev, 'reason': f"no open Read in Notion for {book['title']}", 'book_id': book['id']})
            log('UNMATCHED', ev['kind'], book['title'], '- no open Read in Notion')
            continue
        day = local_date(ev['at'])
        if store.already_logged(ev['guid']):
            already += 1
            log('already in the log', ev['guid'])
        elif store.log_exists(read['id'], KIND_NAME[ev['kind']], day, ev.get('page')):
            already += 1
            log('already in the log (same Read, Kind, date and Page)', ev['guid'])
        else:
            store.create_log(ev, book, read)
            written += 1
            log('logged', ev['kind'], book['title'], ev.get('page') or ev.get('percent') or '', '->', read['title'])
        if ev['kind'] == 'started' and day and (not read.get('started') or read.get('dates_estimated')):
            old = read.get('started')
            note = f'Started {day}' + (f' (was estimated {old})' if old else '')
            store.update_read(read, {'Started': {'date': {'start': day}}, 'Dates estimated': {'checkbox': False}}, note)
            read['started'], read['dates_estimated'] = day, False
            log(f"replaced estimated Started {old} with {day} on {read['title']}" if old else f"set Started {day} on {read['title']}")
        state['seen'][ev['guid']] = ev['at'] or now_iso()
    if not new:
        log('no new events')
    checks = check_reading(shelf, lib, store)
    for c in checks:
        log('CHECK', json.dumps(c, ensure_ascii=False))
    return {'at': now or now_iso(), 'new': len(new), 'written': written, 'already': already, 'unmatched': unmatched, 'checks': checks, 'plan': store.plan}


def run(args):
    load_env()
    state = load_state()
    urls = feed_urls() if not (args.fixture and args.shelf_fixture) else {}
    try:
        items = rss_items(Path(args.fixture).read_bytes() if args.fixture else fetch(urls['updates']))
        shelf = rss_items(Path(args.shelf_fixture).read_bytes() if args.shelf_fixture else fetch(urls['reading'])) if not args.parse_only else []
    except Exception as e:
        sys.exit(f'could not fetch Goodreads: {e}')
    if args.parse_only:
        for e in (parse_update(it) for it in items):
            if e:
                print(json.dumps(e, ensure_ascii=False))
        return 0
    offline = bool(args.offline_library)
    notion = None if offline else Notion(cfg('NOTION_TOKEN', required=True), dry_run=args.dry_run)
    lib = None
    if offline:
        lib = Library(None)
        lib.load_raw(args.offline_library)
    report = run_sync(notion, items, shelf, state, dry_run=args.dry_run, offline_lib=lib)
    if args.plan:
        Path(args.plan).write_text(json.dumps(report['plan'], indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        log(f"{len(report['plan'])} planned write(s) saved to {args.plan}")
    view = {'at': report['at'], 'unmatched_events': report['unmatched'], 'reading_check': report['checks']}
    if report['unmatched'] or report['checks']:
        UNMATCHED_FILE.write_text(json.dumps(view, indent=1, ensure_ascii=False), encoding='utf-8')
        log(f"{len(report['unmatched'])} unmatched event(s), {len(report['checks'])} reading-check item(s) written to {UNMATCHED_FILE.name}")
    elif UNMATCHED_FILE.exists():
        UNMATCHED_FILE.unlink()
    if not args.dry_run and not offline:
        state['runs'] = (state.get('runs') or [])[-30:] + [{'at': report['at'], 'new': report['new'], 'written': report['written'],
                                                            'unmatched': len(report['unmatched']), 'checks': len(report['checks'])}]
        save_state(state)
    log(f"done: {report['written']} logged, {report['already']} already there, {len(report['unmatched'])} unmatched, {len(report['checks'])} check item(s)"
        + (' (dry run, nothing saved)' if args.dry_run or offline else ''))
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='read Notion, print the writes, save nothing')
    ap.add_argument('--fixture', help='parse this saved RSS file instead of fetching')
    ap.add_argument('--parse-only', action='store_true', help='print parsed events and stop; no Notion')
    ap.add_argument('--shelf-fixture', help='parse this saved currently-reading shelf RSS instead of fetching')
    ap.add_argument('--offline-library', metavar='DIR', help='match against data/raw/*.json instead of Notion; implies dry run')
    ap.add_argument('--plan', metavar='FILE', help='save the planned writes as JSON (any mode)')
    return ap.parse_args(argv)


if __name__ == '__main__':
    sys.exit(run(parse_args()))
