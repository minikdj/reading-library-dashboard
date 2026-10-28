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
from datetime import datetime, timedelta, timezone
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
    'NOTION_QUEUE_DS': '4e8f45b7-8f9c-4018-8108-7eff25029227',  # Sync queue, created 2026-10-17 by --init-queue under Media › Library
    'NOTION_LIBRARY_PAGE': '3fb1b15c8b7781ed8af3e5900806fd8e',  # Media › Library, the parent of every library database
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
    state = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    state.pop('seen', None)  # dedupe is done against Notion, not a local list
    return {'book_cache': state.get('book_cache') or {}, 'runs': state.get('runs') or []}


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


def to_dt(iso, end_of_day=False):
    """An ISO timestamp or date as an aware datetime (dates are midnight, or 23:59:59, in Dan's zone)."""
    if not iso:
        return None
    if len(iso) == 10:
        d = datetime.fromisoformat(iso).replace(tzinfo=tz())
        return d.replace(hour=23, minute=59, second=59) if end_of_day else d
    dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rfc_iso(s):
    """An RSS date ('Fri, 09 Oct 2026 19:04:19 -0700') as ISO with its offset; None when blank or unparsable."""
    try:
        return parsedate_to_datetime(s).isoformat() if s else None
    except Exception:
        return None


def rfc_date(s):
    """A Goodreads date field ('Thu, 15 Oct 2026 00:00:00 +0000' is a date, not a time) as YYYY-MM-DD."""
    try:
        return parsedate_to_datetime(s).date().isoformat() if s else None
    except Exception:
        return None


def days_between(a, b):
    return abs((datetime.fromisoformat(a[:10]) - datetime.fromisoformat(b[:10])).days)


# ---------------------------------------------------------------- Goodreads feeds
def feed_urls():
    uid = cfg('GOODREADS_USER_ID', required=True)
    key = os.environ.get('GOODREADS_RSS_KEY', '')
    q = ('&key=' + urllib.parse.quote(key)) if key else ''
    urls = {'updates': f'https://www.goodreads.com/user/updates_rss/{uid}?{q.lstrip("&")}'.rstrip('?')}
    for shelf in SHELVES:
        urls[shelf] = f'https://www.goodreads.com/review/list_rss/{uid}?shelf={shelf}&per_page=200&page={{page}}{q}'
    return urls


def fetch_with_retry(url, fetcher=None, sleep=time.sleep, tries=3):
    """Fetch with three attempts and a short backoff; raises the last error."""
    fetcher = fetcher or fetch
    last = None
    for attempt in range(tries):
        try:
            return fetcher(url, timeout=30)
        except Exception as e:  # URLError, HTTPError, timeouts, bad XML upstream
            last = e
            log(f'fetch failed ({attempt + 1}/{tries}): {url.split("?")[0]} - {e}')
            if attempt + 1 < tries:
                sleep(5 * (attempt + 1))
    raise last


def fetch_shelf(url, max_pages=1, fetcher=None, sleep=time.sleep):
    """A shelf feed, page by page (200 items each) until a short page; the to-read shelf needs every page."""
    items = []
    for page in range(1, max_pages + 1):
        chunk = rss_items(fetch_with_retry(url.format(page=page), fetcher=fetcher, sleep=sleep))
        items.extend(chunk)
        if len(chunk) < 200:
            break
    return items


def gather(urls, fixtures, fetcher=None, sleep=time.sleep, fetch_missing=True):
    """The updates feed and the shelves, from fixtures where given, else fetched. Returns
    (items or None when the updates feed failed, {shelf: items} for the shelves obtained, [what failed])."""
    failed, shelves = [], {}
    if fixtures.get('updates'):
        items = rss_items(Path(fixtures['updates']).read_bytes())
    else:
        try:
            items = rss_items(fetch_with_retry(urls['updates'], fetcher=fetcher, sleep=sleep))
        except Exception as e:
            log('CHECK updates feed unavailable:', e)
            items, failed = None, ['updates']
    for shelf in SHELVES:
        if fixtures.get(shelf):
            shelves[shelf] = rss_items(Path(fixtures[shelf]).read_bytes())
        elif fetch_missing and urls.get(shelf):
            try:
                shelves[shelf] = fetch_shelf(urls[shelf], max_pages=10 if shelf == 'to-read' else 1, fetcher=fetcher, sleep=sleep)
            except Exception as e:
                log(f'CHECK {shelf} shelf unavailable:', e)
                failed.append(shelf)
    return items, shelves, failed


def exit_code(report):
    """0 when the run did its work (even partially), 2 when nothing could be fetched, 1 when a Notion write failed."""
    if report.get('errors'):
        return 1
    if report.get('nothing_fetched'):
        return 2
    return 0


SHELVES = ('currently-reading', 'read', 'did-not-finish', 'to-read')
FENCE_MARGIN_HOURS = 48
ARCHIVE_DIR = HERE / 'feed_archive'


def archive_items(archive_dir, items, today):
    """Append the raw feed items to <archive_dir>/<today>.jsonl, once per guid across the whole archive."""
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    for f in archive_dir.glob('*.jsonl'):
        for line in f.read_text(encoding='utf-8').splitlines():
            try:
                seen.add(json.loads(line).get('guid'))
            except ValueError:
                pass
    n = 0
    with archive_dir.joinpath(f'{today}.jsonl').open('a', encoding='utf-8') as out:
        for it in items:
            if it['guid'] in seen:
                continue
            seen.add(it['guid'])
            out.write(json.dumps({**it, 'archived': today}, ensure_ascii=False) + '\n')
            n += 1
    return n


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
                    'user_date_created': t('user_date_created'), 'user_shelves': t('user_shelves'), 'book_published': t('book_published')})
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

    def create_database(self, parent_page_id, title, properties):
        """Create a database under a page; returns the id of its data source."""
        res = self._call('POST', '/databases', {'parent': {'type': 'page_id', 'page_id': parent_page_id},
                                                'title': [{'type': 'text', 'text': {'content': title}}],
                                                'initial_data_source': {'properties': properties}})
        sources = res.get('data_sources') or ([res['initial_data_source']] if res.get('initial_data_source') else [])
        return sources[0]['id'] if sources else res.get('id')


def is_page_id(s):
    return bool(s) and bool(re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', str(s)))


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
GR_IN_NOTES = re.compile(r'\bGR (\d+)')
WTR = 'Goodreads WTR'


def sort_name(name):
    """'Michael D. Coogan' -> 'Coogan, Michael D.' (the Authors convention); one-word names stay as they are."""
    parts = squeeze(name).split(' ')
    suffixes = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii'}
    tail = []
    while len(parts) > 2 and parts[-1].lower() in suffixes:
        tail.insert(0, parts.pop())
    if len(parts) < 2:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}" + (f" {' '.join(tail)}" if tail else '')
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
        self.raw_editions = []

    def load_rows(self, rows):
        """rows: [{'id', 'title', 'authors': [names], 'lists'?, 'wishlisted'?, 'gr_id'?}]"""
        for r in rows:
            self.add_book(r)

    def add_book(self, r):
        names = [x for x in r.get('authors') or [] if x]
        book = {'id': r['id'], 'title': r['title'], 'norm': norm(r['title']), 'main': main_title(r['title']), 'authors': names,
                'surnames': {surname(x) for x in names}, 'lists': list(r.get('lists') or []), 'wishlisted': r.get('wishlisted'), 'gr_id': r.get('gr_id'),
                'source': r.get('source'), 'year': r.get('year'), 'pages': r.get('pages')}
        self.books.append(book)
        return book

    def load(self):
        for a in self.n.query_all(cfg('NOTION_AUTHORS_DS')):
            self.authors[a['id']] = prop_text(a['properties'].get('Name'))
        rows = []
        for b in self.n.query_all(cfg('NOTION_BOOKS_DS')):
            p = b['properties']
            m = GR_IN_NOTES.search(prop_text(p.get('Notes')))
            rows.append({'id': b['id'], 'title': prop_text(p.get('Title')), 'authors': [self.authors.get(i, '') for i in prop_rel(p.get('Author'))],
                         'lists': [o['name'] for o in (p.get('Lists') or {}).get('multi_select', [])], 'wishlisted': prop_date(p.get('Wishlisted')),
                         'gr_id': m.group(1) if m else None, 'source': prop_select(p.get('Source')), 'year': prop_number(p.get('Original year')),
                         'pages': prop_number(p.get('Pages'))})
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
            rows.append({'id': pid(b['url']), 'title': b.get('Title') or '', 'authors': [self.authors.get(pid(u), '') for u in json.loads(b.get('Author') or '[]')],
                         'lists': json.loads(b.get('Lists') or '[]'), 'wishlisted': b.get('date:Wishlisted:start') or None,
                         'source': b.get('Source') or None, 'year': b.get('Original year'), 'pages': b.get('Pages')})
        self.load_rows(rows)
        self.raw_editions = []
        for e in json.loads(rd.joinpath('editions.json').read_text(encoding='utf-8')):
            self.raw_editions.append({'id': pid(e['url']), 'title': e.get('Edition') or '', 'books': [pid(u) for u in json.loads(e.get('Book') or '[]')],
                                      'format': e.get('Format') or None, 'publisher': e.get('Publisher') or '', 'year': e.get('Year'),
                                      'owned': e.get('Owned') == '__YES__' and not e.get('date:Disposed:start')})
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


def edition_norm(page):
    p = page['properties']
    return {'id': page['id'], 'title': prop_text(p.get('Edition')), 'books': prop_rel(p.get('Book')), 'format': prop_select(p.get('Format')),
            'publisher': prop_text(p.get('Publisher')), 'year': prop_number(p.get('Year')),
            'owned': prop_checkbox(p.get('Owned')) and not prop_date(p.get('Disposed'))}


def copy_label(ed):
    """How a copy is named in questions: 'Kindle · Simon & Schuster', or the Edition title."""
    if not ed:
        return 'none'
    return ' · '.join(x for x in (ed.get('format'), ed.get('publisher')) if x) or ed.get('title') or ed['id']


KIND_NAME = {'started': 'Started', 'progress': 'Progress', 'finished': 'Finished', 'dnf': 'DNF'}


def log_key(read_id, kind_name, date, page):
    return (read_id, kind_name, date, page)


class Store:
    """Where Reads are looked up and events written: Notion over the API, or the raw pull (read-only).

    Every write goes through here so dry runs, offline runs and tests see the same plan. Dedupe is
    by Goodreads id and by (Read, Kind, local date, Page), checked in Notion and within the run."""

    def __init__(self, notion=None, lib=None, dry_run=False, today=None):
        self.notion, self.lib, self.dry_run = notion, lib, dry_run
        self.now = now_iso()
        self.today = today or local_date(self.now)
        self.seen_guids, self.seen_keys, self.plan = set(), set(), []
        self.seen_rows = {}   # read id -> log rows written this run
        self.reads = {}       # read id -> the Read as this run last left it (created, closed, reopened)

    # ---- reads
    def _overlay(self, reads):
        return [self.reads.get(r['id'], r) for r in reads]

    def _reads(self, flt):
        return self._overlay([read_norm(r) for r in self.notion.query_all(cfg('NOTION_READS_DS'), flt)])

    def reads_for_book(self, book_id):
        if self.notion:
            rs = self._reads({'property': 'Book', 'relation': {'contains': book_id}})
        else:
            rs = self._overlay([dict(r) for r in self.lib.raw_reads if r['book'] == book_id])
        known = {r['id'] for r in rs}
        rs += [r for r in self.reads.values() if r.get('book') == book_id and r['id'] not in known]  # created this run (dry runs)
        return rs

    def read(self, read_id):
        if read_id in self.reads:
            return self.reads[read_id]
        if self.notion:
            return read_norm(self.notion.retrieve_page(read_id))
        return next((dict(r) for r in self.lib.raw_reads if r['id'] == read_id), None)

    def open_read(self, book_id):
        rs = [r for r in self.reads_for_book(book_id) if not r['outcome']]
        rs.sort(key=lambda r: r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def recent_read(self, book_id):
        rs = sorted(self.reads_for_book(book_id), key=lambda r: r['ended'] or r['started'] or '', reverse=True)
        return rs[0] if rs else None

    def all_open(self):
        if self.notion:
            rs = self._reads({'property': 'Outcome', 'select': {'is_empty': True}})
        else:
            rs = self._overlay([dict(r) for r in self.lib.raw_reads if not r['outcome']])
        return [r for r in rs if not r['outcome']]

    def create_read(self, book, props):
        """Create a Read (invariant: only ever an open one, from a Goodreads start); returns it in read_norm shape."""
        res = self._create(cfg('NOTION_READS_DS'), props)
        read = {'id': res.get('id') or 'planned', 'title': prop_text(props['Read']), 'outcome': None, 'started': prop_date(props.get('Started')),
                'ended': None, 'book': book['id'], 'dates_estimated': prop_checkbox(props.get('Dates estimated')), 'edition': prop_rel(props.get('Edition')),
                'edition_inferred': prop_checkbox(props.get('Edition inferred')), 'pages_read': None, 'format_read': prop_select(props.get('Format read')),
                'notes': prop_text(props.get('Notes'))}
        self.reads[read['id']] = read
        return read

    # ---- books and authors (wishlist only)
    def update_book(self, book_id, props):
        return self._update(book_id, props)

    def create_book(self, props):
        res = self._create(cfg('NOTION_BOOKS_DS'), props)
        return res.get('id') or 'planned'

    def find_author(self, name):
        """An Author by Name or Sort name (the runbook: check both before creating one)."""
        if not self.notion:
            return next((i for i, n in self.lib.authors.items() if n.lower() == name.lower()), None)
        rows = self.notion.query_all(cfg('NOTION_AUTHORS_DS'), {'or': [
            {'property': 'Name', 'title': {'equals': name}}, {'property': 'Sort name', 'rich_text': {'equals': sort_name(name)}},
            {'property': 'Name', 'title': {'equals': sort_name(name)}}]})
        return rows[0]['id'] if rows else None

    def create_author(self, name):
        res = self._create(cfg('NOTION_AUTHORS_DS'), {'Name': {'title': [{'text': {'content': name[:200]}}]}, 'Sort name': rt(sort_name(name)),
                                                      'Roles': {'multi_select': [{'name': 'Author'}]}})
        aid = res.get('id') or 'planned'
        self.lib.authors[aid] = name
        return aid

    # ---- editions
    def editions_for_book(self, book_id):
        if self.notion:
            return [edition_norm(e) for e in self.notion.query_all(cfg('NOTION_EDITIONS_DS'), {'property': 'Book', 'relation': {'contains': book_id}})]
        return [dict(e) for e in self.lib.raw_editions if book_id in e['books']]

    # ---- reading log
    def already_logged(self, guid):
        if guid in self.seen_guids:
            return True
        if not self.notion:
            return False
        return bool(self.notion.query_all(cfg('NOTION_LOG_DS'), {'property': 'Goodreads id', 'rich_text': {'equals': guid}}, page_size=1))

    def log_rows(self, read_id, kind_name):
        """Rows already in the Reading log for this Read and Kind (Notion plus this run): [{date, page, total}], oldest first."""
        rows = [dict(r) for r in self.seen_rows.get(read_id, []) if r['kind'] == kind_name]
        if self.notion and is_page_id(read_id):  # a Read planned this run (dry run) has no rows in Notion yet
            for r in self.notion.query_all(cfg('NOTION_LOG_DS'), {'and': [
                    {'property': 'Read', 'relation': {'contains': read_id}}, {'property': 'Kind', 'select': {'equals': kind_name}}]}):
                p = r['properties']
                rows.append({'kind': kind_name, 'date': local_date(prop_date(p.get('When'))), 'page': prop_number(p.get('Page')), 'total': prop_number(p.get('Of pages'))})
        rows.sort(key=lambda r: r['date'] or '')
        return rows

    def log_exists(self, read_id, kind_name, date, page, any_date=False):
        """The secondary dedupe key: a row on the same Read with the same Kind, local date and Page
        (or, with any_date, any row of that Kind on the Read)."""
        if log_key(read_id, kind_name, date, page) in self.seen_keys:
            return True
        rows = self.log_rows(read_id, kind_name)
        return bool(rows) if any_date else any((r['date'], r['page']) == (date, page) for r in rows)

    def last_progress_page(self, read_id):
        pages = [r['page'] for r in self.log_rows(read_id, 'Progress') if r['page'] is not None]
        return pages[-1] if pages else None

    def create_log(self, ev, book, read):
        props = log_props(ev, book, read)
        self.seen_guids.add(ev['guid'])
        self.seen_keys.add(log_key(read['id'], KIND_NAME[ev['kind']], local_date(ev['at']), ev.get('page')))
        self.seen_rows.setdefault(read['id'], []).append({'kind': KIND_NAME[ev['kind']], 'date': local_date(ev['at']), 'page': ev.get('page'), 'total': ev.get('total')})
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

    def stamp(self, text):
        return f'{text} from Goodreads {self.today}'

    def update_read(self, read, props, line):
        """Change a Read and append the reason (one line, dated) to its Notes (invariant 3)."""
        notes = (read.get('notes') or '').rstrip()
        props = dict(props, Notes=rt((notes + '\n' + line) if notes else line))
        read['notes'] = (notes + '\n' + line) if notes else line
        self.reads[read['id']] = read
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


# ---------------------------------------------------------------- the Sync queue
QUEUE_KINDS = ['Edition', 'Pages read', 'Reread or reopen', 'Edition change', 'Finish date', 'Unmatched book', 'No open Read',
               'Still reading', 'Book created', 'Wishlist removed', 'Gap', 'Sync run']
QUEUE_STATUSES = ['Open', 'Answered', 'Dismissed', 'Done']
DROP_ANSWERS = {'drop', 'skip', 'ignore', 'dismiss', 'no'}


def queue_properties():
    """The Sync queue schema (docs/SYNC_REVIEW.md, "Questions for Dan" plus Default applied / Proposed / Confirm)."""
    def sel(names):
        return {'select': {'options': [{'name': n} for n in names]}}
    return {
        'Question': {'title': {}},
        'Kind': sel(QUEUE_KINDS),
        'Status': sel(QUEUE_STATUSES),
        'Book': {'relation': {'data_source_id': cfg('NOTION_BOOKS_DS'), 'type': 'single_property', 'single_property': {}}},
        'Read': {'relation': {'data_source_id': cfg('NOTION_READS_DS'), 'type': 'single_property', 'single_property': {}}},
        'Options': {'rich_text': {}},          # what Dan can answer with
        'Proposed': {'rich_text': {}},         # the default as applied, or why an answer could not be applied
        'Default applied': {'checkbox': {}},   # Notion already holds the default; confirming keeps it
        'Answer': {'rich_text': {}},           # Dan's answer, by chat or in Notion
        'Confirm': {'checkbox': {}},           # tick = keep the default
        'Asked': {'date': {}},
        'Resolved': {'date': {}},
        'Key': {'rich_text': {}},              # one open row per key
        'Event': {'rich_text': {}},            # the feed event (JSON) for pending events, the run summary for Sync run rows
        'Goodreads id': {'rich_text': {}},
    }


def init_queue(notion, parent_page_id):
    """Create the Sync queue database under the Library page; returns its data source id."""
    ds = notion.create_database(parent_page_id, 'Sync queue', queue_properties())
    log(f'Sync queue created: data source {ds}')
    return ds


def queue_norm(page):
    p = page['properties']
    ev = prop_text(p.get('Event'))
    try:
        event = json.loads(ev) if ev else None
    except ValueError:
        event = None
    return {'id': page['id'], 'question': prop_text(p.get('Question')), 'kind': prop_select(p.get('Kind')), 'status': prop_select(p.get('Status')),
            'key': prop_text(p.get('Key')), 'answer': prop_text(p.get('Answer')), 'confirm': prop_checkbox(p.get('Confirm')),
            'default_applied': prop_checkbox(p.get('Default applied')), 'proposed': prop_text(p.get('Proposed')), 'options': prop_text(p.get('Options')),
            'book': (prop_rel(p.get('Book')) or [None])[0], 'read': (prop_rel(p.get('Read')) or [None])[0], 'event': event,
            'guid': prop_text(p.get('Goodreads id')), 'asked': prop_date(p.get('Asked')), 'resolved': prop_date(p.get('Resolved'))}


class SyncQueue:
    """Questions for Dan and the run watermark, kept in the Sync queue database (survives a throw-away checkout)."""

    def __init__(self, store, ds=None):
        self.store = store
        self.ds = ds if ds is not None else cfg('NOTION_QUEUE_DS')
        self.asked = []       # every question raised this run, written or not (dry runs, offline)
        self._open = None

    @property
    def enabled(self):
        return bool(self.store.notion and self.ds)

    def open_rows(self, refresh=False):
        if not self.enabled:
            return []
        if self._open is None or refresh:
            self._open = [queue_norm(r) for r in self.store.notion.query_all(self.ds, {'property': 'Status', 'select': {'equals': 'Open'}})]
        return self._open

    def recently_asked(self, key, days):
        if not self.enabled:
            return False
        rows = [queue_norm(r) for r in self.store.notion.query_all(self.ds, {'property': 'Key', 'rich_text': {'equals': key}})]
        return any(r['asked'] and days_between(r['asked'], self.store.today) <= days for r in rows)

    def event_settled(self, kind, key, book, event):
        """True when a question about this same Goodreads event already exists in any status (2026-10-27 fix).

        An event is one fact, so once its question is answered or dropped it is never asked again. The read shelf and
        the updates feed report the same finish under different ids (Shelf…:finished:<date> vs ReadStatus…), so a
        question of the same kind on the same Book about the same event kind and day counts as the same question."""
        if not self.enabled:
            return False
        rows = [queue_norm(r) for r in self.store.notion.query_all(self.ds, {'property': 'Key', 'rich_text': {'equals': key}})]
        if rows:
            return True
        if not book:
            return False
        day = local_date(event.get('at'))
        rows = [queue_norm(r) for r in self.store.notion.query_all(self.ds, {'and': [
            {'property': 'Kind', 'select': {'equals': kind}}, {'property': 'Book', 'relation': {'contains': book}}]})]
        return any(r['event'] and r['event'].get('kind') == event.get('kind') and local_date(r['event'].get('at')) == day for r in rows)

    def ask(self, kind, question, key, *, book=None, read=None, options='', proposed='', default_applied=False, event=None, guid='', reask_days=None):
        q = {'kind': kind, 'question': question, 'key': key, 'book': book, 'read': read, 'options': options, 'proposed': proposed,
             'default_applied': default_applied, 'event': event, 'guid': guid or (event or {}).get('guid') or '', 'asked': self.store.now}
        if any(r['key'] == key for r in self.open_rows()) or any(a['key'] == key for a in self.asked):
            return None  # still open from an earlier run (or already asked this run)
        if reask_days and self.recently_asked(key, reask_days):
            return None  # answered recently; ask again only after the interval
        if event and not reask_days and self.event_settled(kind, key, book, event):
            return None  # this Goodreads event was asked about before and Dan answered or dropped it
        self.asked.append(q)
        log('QUESTION', kind, '-', question)
        if not self.enabled:
            return None
        props = {'Question': {'title': [{'text': {'content': question[:200]}}]}, 'Kind': {'select': {'name': kind}}, 'Status': {'select': {'name': 'Open'}},
                 'Key': rt(key), 'Options': rt(options), 'Proposed': rt(proposed), 'Default applied': {'checkbox': default_applied},
                 'Asked': {'date': {'start': self.store.now}}, 'Goodreads id': rt(q['guid']), 'Event': rt(json.dumps(event, ensure_ascii=False) if event else '')}
        if book:
            props['Book'] = {'relation': [{'id': book}]}
        if read:
            props['Read'] = {'relation': [{'id': read}]}
        res = self.store._create(self.ds, props)
        q['id'] = res.get('id')
        if self._open is not None:
            self._open.append(dict(q, status='Open', answer='', confirm=False))
        return q

    def resolve(self, row, status, answer=None, proposed=None):
        props = {'Status': {'select': {'name': status}}, 'Resolved': {'date': {'start': self.store.today}}}
        if answer is not None:
            props['Answer'] = rt(answer)
        if proposed is not None:
            props['Proposed'] = rt(proposed)
        row['status'] = status
        log('resolved', status, '-', row['question'], ('(' + (answer or proposed or '') + ')') if (answer or proposed) else '')
        return self.store._update(row['id'], props)

    def note(self, row, proposed):
        row['proposed'] = proposed
        return self.store._update(row['id'], {'Proposed': rt(proposed)})

    def last_run(self):
        if not self.enabled:
            return None
        rows = [queue_norm(r) for r in self.store.notion.query_all(self.ds, {'and': [
            {'property': 'Kind', 'select': {'equals': 'Sync run'}}, {'property': 'Status', 'select': {'equals': 'Done'}}]})]
        rows.sort(key=lambda r: r['asked'] or '', reverse=True)
        return rows[0] if rows else None

    def record_run(self, summary):
        if not self.enabled or self.store.dry_run:
            return None
        name = f"Sync run {self.store.now[:16].replace('T', ' ')} · {summary.get('written', 0)} logged, {summary.get('asked', 0)} asked"
        return self.store._create(self.ds, {'Question': {'title': [{'text': {'content': name}}]}, 'Kind': {'select': {'name': 'Sync run'}},
                                            'Status': {'select': {'name': 'Done'}}, 'Key': rt('run:' + self.store.now),
                                            'Asked': {'date': {'start': self.store.now}}, 'Event': rt(json.dumps(summary, ensure_ascii=False))})


# ---------------------------------------------------------------- the run
HEX32 = re.compile(r'^[0-9a-f]{32}$')
REOPEN_DAYS = 14   # a Read closed by the sync is reopened by progress within this many days; later progress is a reread
AGREE_DAYS = 2     # a Finished/DNF attaches quietly to a closed Read whose Ended is this close
SYNC_CLOSE_NOTE = 'Closed from Goodreads'


class Run:
    """One sync: match events to Books and Reads, write log rows and Read changes, ask questions.

    Reads are created on a Started (or a currently-reading shelf item), closed on Finished / DNF, reopened
    by progress within REOPEN_DAYS of a close the sync made; everything else is a question with the
    default already applied (docs/SYNC_REVIEW.md, "Question policy")."""

    def __init__(self, store, lib, queue, state):
        self.store, self.lib, self.queue, self.state = store, lib, queue, state
        self.books_by_id = {b['id']: b for b in lib.books}
        self.written = self.already = self.strays = 0
        self.unmatched, self.errors = [], []
        self.to_read_by_id = {}

    def guarded(self, label, fn, *a, **kw):
        """Run one step; a Notion error is recorded and the rest of the run goes on (the watermark then stays put)."""
        try:
            return fn(*a, **kw)
        except RuntimeError as e:
            log('ERROR', label, '-', e)
            self.errors.append(f'{label}: {e}')
            return {'status': 'error', 'why': str(e)}

    # ---- lookups
    def find_book(self, ev):
        cached = self.state['book_cache'].get(ev['gr_book_id'] or '')
        if cached:
            book = self.books_by_id.get(cached)
            if book:
                return book, 'ok'
            log('book cache: dropping stale entry', ev['gr_book_id'], '->', cached)
            del self.state['book_cache'][ev['gr_book_id']]
        return self.lib.match(ev['title'], ev['author'])

    def book_from_answer(self, answer):
        a = answer.strip()
        if HEX32.match(a.replace('-', '')):
            return self.books_by_id.get(a.replace('-', ''))
        book, _ = self.lib.match(a, None)
        if book:
            return book
        exact = [b for b in self.lib.books if norm(b['title']) == norm(a)]
        return exact[0] if len(exact) == 1 else None

    def read_from_answer(self, answer, book_id):
        a = answer.strip()
        reads = self.store.reads_for_book(book_id) if book_id else []
        if HEX32.match(a.replace('-', '')):
            return next((r for r in reads if r['id'] == a.replace('-', '')), None)
        hits = [r for r in reads if r['title'].lower() == a.lower()] or [r for r in reads if r['title'].lower().startswith(a.lower())]
        return hits[0] if len(hits) == 1 else None

    def edition_from_answer(self, book_id, answer):
        a = answer.strip().lower()
        eds = self.store.editions_for_book(book_id)
        if HEX32.match(a.replace('-', '')):
            return next((e for e in eds if e['id'] == a.replace('-', '')), None)
        hits = [e for e in eds if a == (e['format'] or '').lower() or a == copy_label(e).lower() or a in e['title'].lower()
                or (e['publisher'] and a in e['publisher'].lower())]
        owned = [e for e in hits if e['owned']]
        hits = owned or hits
        return hits[0] if len(hits) == 1 else None

    def edition_default(self, book_id, reads):
        """The copy a new Read gets: the only owned copy, else the copy of the most recent Read, else none."""
        eds = self.store.editions_for_book(book_id)
        owned = [e for e in eds if e['owned']]
        if len(owned) == 1:
            return owned[0], eds, 'the only owned copy'
        by_id = {e['id']: e for e in eds}
        for r in sorted(reads, key=lambda r: r['ended'] or r['started'] or '', reverse=True):
            for eid in r.get('edition') or []:
                if eid in by_id:
                    return by_id[eid], eds, f"the copy of {r['title']}"
        return None, eds, 'no owned copy' if not owned else f'{len(owned)} owned copies'

    # ---- Read changes (invariant 2: only these)
    def create_read(self, book, day, estimated, why):
        reads = self.store.reads_for_book(book['id'])
        title = f"{book['title']} · {day[:4]}"
        taken = {r['title'] for r in reads}
        if title in taken:
            title += ' (reread)'
            n = 2
            while title in taken:
                title = f"{book['title']} · {day[:4]} (reread {n})"
                n += 1
        ed, eds, how = self.edition_default(book['id'], reads)
        note = f'Created from Goodreads {self.store.today}' + (f' ({why})' if why else '')
        props = {'Read': {'title': [{'text': {'content': title[:200]}}]}, 'Book': {'relation': [{'id': book['id']}]},
                 'Started': {'date': {'start': day}}, 'Dates estimated': {'checkbox': bool(estimated)}, 'Notes': rt(note),
                 'Edition': {'relation': [{'id': ed['id']}] if ed else []}, 'Edition inferred': {'checkbox': bool(ed)}}
        fmt = None
        if not ed:
            by_id = {e['id']: e for e in eds}
            for r in sorted(reads, key=lambda r: r['ended'] or r['started'] or '', reverse=True):
                fmt = r.get('format_read') or next((by_id[e]['format'] for e in (r.get('edition') or []) if e in by_id and by_id[e]['format']), None)
                if fmt:
                    break
            if fmt:
                props['Format read'] = {'select': {'name': fmt}}
        read = self.store.create_read(book, props)
        log('created Read', title, '-', f'Started {day}' + (' (estimated)' if estimated else ''), '-', 'copy:', copy_label(ed), f'({how})')
        owned = [copy_label(e) for e in eds if e['owned']]
        self.queue.ask('Edition', f'Which copy are you reading “{book["title"]}” in? [default: {copy_label(ed)}]', f'edition:{read["id"]}',
                       book=book['id'], read=read['id'], default_applied=bool(ed),
                       proposed=(copy_label(ed) + f' ({how})') if ed else ('blank' + (f'; Format read {fmt}' if fmt else '') + f' ({how})'),
                       options=' / '.join(owned + ['other (name it)']) + '; Confirm keeps the default')
        return read

    def close_read(self, read, kind, day, book, ev=None, pages=None, by_dan=False, force=False):
        """Close an open Read (Ended, Outcome; Pages read on DNF from the log). Dan's Pages read or Ended are never changed.

        A Pages read Dan already set never blocks the close (runbook decision 2026-10-27): his workflow is to mark the book
        read on Goodreads and give the page count in chat, in either order. The close keeps his figure as it is, and no
        Pages read question is asked, since he has already answered it."""
        if read['outcome']:
            return False
        kind_name = KIND_NAME.get(kind, kind)
        dans_pages = read.get('pages_read') if pages is None else None
        props = {'Outcome': {'select': {'name': kind_name}}}
        ended = read.get('ended')
        if not ended:
            props['Ended'] = {'date': {'start': day}}
            ended = day
        last = None
        if kind == 'dnf' and pages is None and read.get('pages_read') is None:
            last = self.store.last_progress_page(read['id'])
            if last is not None:
                props['Pages read'] = {'number': last}
        if pages is not None:
            props['Pages read'] = {'number': pages}
        line = f"Closed by Dan's answer {self.store.today}" if by_dan else f'Closed from Goodreads {self.store.today}' + (f' (DNF at p.{last})' if last is not None else '')
        if dans_pages is not None and not by_dan:
            line += f' (your Pages read {dans_pages:g} kept)'
        self.store.update_read(read, props, line)
        read['outcome'], read['ended'] = kind_name, ended
        if 'Pages read' in props:
            read['pages_read'] = props['Pages read']['number']
        log(f"closed {read['title']}: {kind_name}, Ended {ended}" + (f', Pages read {read["pages_read"]:g}' if read.get('pages_read') is not None else ''))
        if not by_dan and dans_pages is None:
            shown = f'{read["pages_read"]:g}' if read.get('pages_read') is not None else 'blank'
            self.queue.ask('Pages read', f'{kind_name} “{book["title"]}” on {ended}; pages read = {shown}?', f'pages:{read["id"]}',
                           book=book['id'], read=read['id'], default_applied=True, proposed=f'Ended {ended}, Pages read {shown}',
                           options="Answer a page count, 'ended YYYY-MM-DD' to correct the date, or both; Confirm keeps it")
        return True

    def sync_closed(self, read):
        """Closed by the sync and not since taken over by Dan (Pages read is blank, or is the DNF page the sync wrote)."""
        if SYNC_CLOSE_NOTE not in (read.get('notes') or ''):
            return False
        if read.get('pages_read') is None:
            return True
        return read['outcome'] == 'DNF' and read['pages_read'] == self.store.last_progress_page(read['id'])

    def reopen(self, read, line):
        props = {'Outcome': {'select': None}, 'Ended': {'date': None}}
        if read['outcome'] == 'DNF' and read.get('pages_read') is not None and read['pages_read'] == self.store.last_progress_page(read['id']):
            props['Pages read'] = {'number': None}  # the sync's own DNF figure
            read['pages_read'] = None
        self.store.update_read(read, props, line)
        read['outcome'], read['ended'] = None, None
        log('reopened', read['title'], '-', line)
        return read

    # ---- the wishlist
    def wishlist(self, ev):
        """'Wants to read': tag the Book (Goodreads WTR + Wishlisted), creating Author and Book per the runbook when missing."""
        book, why = self.find_book(ev)
        day = local_date(ev['at']) or self.store.today
        if not book and why.startswith('several'):
            log('UNMATCHED', 'wishlisted', ev['title'], '-', why)
            return {'status': 'unmatched', 'why': why}
        if book:
            if ev['gr_book_id']:
                self.state['book_cache'][ev['gr_book_id']] = book['id']
            props = {}
            if WTR not in book['lists']:
                props['Lists'] = {'multi_select': [{'name': x} for x in book['lists'] + [WTR]]}
            if not book.get('wishlisted'):
                props['Wishlisted'] = {'date': {'start': day}}
            # a Book that came from Goodreads (this sync or the import) gets its blank year and pages from the shelf; Dan's own Books are left alone
            extra = self.to_read_by_id.get(ev['gr_book_id'] or '', {})
            year, pages = ev.get('published') or extra.get('book_published'), ev.get('pages') or extra.get('num_pages')
            if book.get('source') == 'Goodreads':
                if book.get('year') is None and str(year or '').lstrip('-').isdigit():
                    props['Original year'] = {'number': int(year)}
                if book.get('pages') is None and str(pages or '').isdigit() and int(pages) > 0:
                    props['Pages'] = {'number': int(pages)}
            if not props:
                return {'status': 'already', 'book': book}
            self.store.update_book(book['id'], props)
            book['lists'] = book['lists'] + ([WTR] if WTR not in book['lists'] else [])
            book['wishlisted'] = book.get('wishlisted') or day
            book['year'] = props['Original year']['number'] if 'Original year' in props else book.get('year')
            book['pages'] = props['Pages']['number'] if 'Pages' in props else book.get('pages')
            log('wishlisted', book['title'], '-', ', '.join(props))
            return {'status': 'tagged', 'book': book}
        # no Book: create the Author(s) and the Book (Source Goodreads, no Edition, genres left for chat)
        extra = self.to_read_by_id.get(ev['gr_book_id'] or '', {})
        author = ev.get('author') or extra.get('author_name') or ''
        created = []
        aid = self.store.find_author(author) if author else None
        if author and not aid:
            aid = self.store.create_author(author)
            created.append(f'Author created: \u201c{author}\u201d')
        notes = [f'GR {ev["gr_book_id"]}' if ev.get('gr_book_id') else None, f'ISBN {ev.get("isbn") or extra.get("isbn")}' if (ev.get('isbn') or extra.get('isbn')) else None,
                 f'created from Goodreads {self.store.today}']
        props = {'Title': {'title': [{'text': {'content': ev['title'][:200]}}]}, 'Author': {'relation': [{'id': aid}] if aid else []},
                 'Source': {'select': {'name': 'Goodreads'}}, 'Lists': {'multi_select': [{'name': WTR}]}, 'Wishlisted': {'date': {'start': day}},
                 'Notes': rt('; '.join(n for n in notes if n))}
        year = ev.get('published') or extra.get('book_published')
        pages = ev.get('pages') or extra.get('num_pages')
        if str(year or '').lstrip('-').isdigit():
            props['Original year'] = {'number': int(year)}
        if str(pages or '').isdigit() and int(pages) > 0:
            props['Pages'] = {'number': int(pages)}
        bid = self.store.create_book(props)
        book = self.lib.add_book({'id': bid, 'title': ev['title'], 'authors': [author] if author else [], 'lists': [WTR], 'wishlisted': day, 'gr_id': ev.get('gr_book_id'),
                                  'source': 'Goodreads', 'year': props.get('Original year', {}).get('number'), 'pages': props.get('Pages', {}).get('number')})
        self.books_by_id[bid] = book
        if ev['gr_book_id']:
            self.state['book_cache'][ev['gr_book_id']] = bid
        created.append(f'Book created: \u201c{ev["title"]}\u201d')
        log('created', ', '.join(created))
        self.queue.ask('Book created', f'Wants to read \u201c{ev["title"]}\u201d{(" by " + author) if author else ""}: created in Notion from Goodreads ({day})',
                       f'book:{bid}', book=bid, default_applied=True,
                       proposed=', '.join(created) + f'; Source Goodreads, Lists {WTR}' + (f', {year} · {pages} pp' if year or pages else '') + '; Genre, Form and Fiction blank',
                       options='Confirm, or fill the genres by chat; answer a Notion title if it duplicates an existing Book')
        return {'status': 'created', 'book': book}

    def wishlist_removed(self, items):
        """Books tagged Goodreads WTR that are not on the to-read shelf any more: reported, never untagged."""
        if not items:
            return []
        on_shelf, gr_ids = set(), {it['book_id'] for it in items if it.get('book_id')}
        for it in items:
            book, _ = self.lib.match(it['title'], it['author_name'])
            if book:
                on_shelf.add(book['id'])
        out = []
        for b in self.lib.books:
            if WTR in b['lists'] and b['id'] not in on_shelf and not (b.get('gr_id') and b['gr_id'] in gr_ids):
                out.append({'title': b['title'], 'authors': b['authors'], 'wishlisted': b.get('wishlisted'), 'book_id': b['id']})
        for r in out:
            log('WISHLIST REMOVED?', r['title'], '- tagged Goodreads WTR but not on the to-read shelf (tag kept)')
        return out

    # ---- one event
    def handle(self, ev, book=None, read=None, force_new=False, force_reopen=False, stray=False):
        """Write one event. Returns {'status': logged | already | stray | unmatched | no_read | finish_mismatch | reread_or_reopen | skipped, ...}."""
        if ev['kind'] == 'wishlisted':
            return self.wishlist(ev)
        if ev['kind'] not in KIND_NAME:
            log('skipped', ev['kind'], ev['title'], '(not handled yet)')
            return {'status': 'skipped'}
        kind = ev['kind']
        if not book:
            book, why = self.find_book(ev)
            if not book:
                log('UNMATCHED', kind, ev['title'], '-', why)
                return {'status': 'unmatched', 'why': why}
        if ev['gr_book_id']:
            self.state['book_cache'][ev['gr_book_id']] = book['id']
        day = local_date(ev['at'])
        kind_name = KIND_NAME[kind]
        if not read:
            read = self.store.open_read(book['id'])
        if stray:
            target = read or self.store.recent_read(book['id'])
            marker = f'Stray {kind_name} from Goodreads {day} skipped'
            if target and marker not in (target.get('notes') or ''):
                self.store.update_read(target, {}, f'{marker}: progress continued ({self.store.today})')
            log('STRAY', kind_name, book['title'], day, '- later progress in the same feed; skipped')
            return {'status': 'stray', 'book': book, 'read': target}
        if not read and day:
            recent = self.store.recent_read(book['id'])
            if recent and recent['outcome'] and recent['ended']:
                gap = days_between(recent['ended'], day)
                if day <= recent['ended']:
                    # the event lies inside (or before) a closed Read: recovered history, never a new Read
                    if kind in ('finished', 'dnf') and gap <= AGREE_DAYS:
                        read = recent
                    elif kind in ('finished', 'dnf'):
                        log('FINISH DATE DIFFERS', book['title'], day, 'vs', recent['title'], recent['ended'])
                        return {'status': 'finish_mismatch', 'book': book, 'read': recent, 'why': f"Notion's {recent['title']} ended {recent['ended']}, Goodreads says {day}"}
                    elif recent['started'] and day >= recent['started']:
                        read = recent
                elif kind in ('finished', 'dnf'):
                    if gap <= AGREE_DAYS:
                        read = recent  # Dan's own entry; the event attaches quietly
                    elif gap <= REOPEN_DAYS and not force_new:
                        log('FINISH DATE DIFFERS', book['title'], day, 'vs', recent['title'], recent['ended'])
                        return {'status': 'finish_mismatch', 'book': book, 'read': recent, 'why': f"Notion's {recent['title']} ended {recent['ended']}, Goodreads says {day}"}
                    else:
                        read = self.create_read(book, day, True, f'{kind_name} on a reread with no Started event; start date estimated')
                elif gap <= REOPEN_DAYS and not force_new:
                    if force_reopen or self.sync_closed(recent):
                        read = self.reopen(recent, f"Reopened by Dan's answer {self.store.today}" if force_reopen else f'Reopened from Goodreads {self.store.today}: progress continued')
                    else:
                        log('PROGRESS AFTER A CLOSE', book['title'], day, 'vs', recent['title'], recent['ended'])
                        return {'status': 'reread_or_reopen', 'book': book, 'read': recent, 'why': f"{recent['title']} was finished on {recent['ended']}"}
                else:
                    read = self.create_read(book, day, kind != 'started' or bool(ev.get('estimated')),
                                            'start date estimated from the currently-reading shelf' if ev.get('estimated') else
                                            ('progress on a read with no Started event; start date estimated' if kind != 'started' else ''))
            elif not recent:
                if kind in ('finished', 'dnf'):
                    log('NO READ AT ALL', kind, book['title'])
                    return {'status': 'no_read', 'book': book, 'why': f"no Read of {book['title']} in Notion"}
                read = self.create_read(book, day, kind != 'started' or bool(ev.get('estimated')),
                                        'start date estimated from the currently-reading shelf' if ev.get('estimated') else
                                        ('progress on a read with no Started event; start date estimated' if kind != 'started' else ''))
        if not read:
            log('NO OPEN READ', kind, book['title'])
            return {'status': 'no_read', 'book': book, 'why': f"no open Read in Notion for {book['title']}"}
        # a changed Goodreads page total on an open Read is an edition signal: question only (never an edit)
        if kind == 'progress' and ev.get('total') and not read['outcome']:
            prior = [r['total'] for r in self.store.log_rows(read['id'], 'Progress') if r['total']]
            if prior and prior[-1] != ev['total']:
                eds = self.store.editions_for_book(book['id'])
                current = next((e for e in eds if e['id'] in (read.get('edition') or [])), None)
                self.queue.ask('Edition change', f'Goodreads now shows “{book["title"]}” with {ev["total"]} pages (was {prior[-1]:g}); the Read keeps its copy ({copy_label(current)}). Change it?',
                               f'edchange:{read["id"]}:{ev["total"]}', book=book['id'], read=read['id'], default_applied=True, proposed='unchanged',
                               options=' / '.join(copy_label(e) for e in eds if e['owned']) + '; Confirm keeps it')
        if self.store.already_logged(ev['guid']):
            status = 'already'
            log('already in the log', ev['guid'])
        elif self.store.log_exists(read['id'], kind_name, day, ev.get('page')):
            status = 'already'
            log('already in the log (same Read, Kind, date and Page)', ev['guid'])
        elif ev.get('estimated') and self.store.log_exists(read['id'], kind_name, day, None, any_date=True):
            status = 'already'  # a shelf date only recovers a start the feed never gave; the Read already has one
            log('already in the log (a Started row exists; the shelf date is only a guess)', ev['guid'])
        else:
            self.store.create_log(ev, book, read)
            status = 'logged'
            log('logged', kind, book['title'], ev.get('page') or ev.get('percent') or '', '->', read['title'])
        if kind == 'started' and day:
            old = read.get('started')
            if ev.get('estimated'):
                # a shelf date is when the book was shelved, not when the reading began: fill a blank only, keep the guess flagged
                if not old:
                    self.store.update_read(read, {'Started': {'date': {'start': day}}, 'Dates estimated': {'checkbox': True}},
                                           self.store.stamp(f'Started {day} (estimated, from the currently-reading shelf)'))
                    read['started'], read['dates_estimated'] = day, True
                    log(f"set estimated Started {day} on {read['title']} from the shelf")
            elif (not old or read.get('dates_estimated')) and (old != day or read.get('dates_estimated')):
                self.store.update_read(read, {'Started': {'date': {'start': day}}, 'Dates estimated': {'checkbox': False}},
                                       self.store.stamp(f'Started {day}' + (f' (was estimated {old})' if old and old != day else '')))
                read['started'], read['dates_estimated'] = day, False
                log(f"replaced estimated Started {old} with {day} on {read['title']}" if old else f"set Started {day} on {read['title']}")
        if kind in ('finished', 'dnf') and day and not read['outcome']:
            self.close_read(read, kind, day, book, ev=ev)
        return {'status': status, 'book': book, 'read': read}

    def count(self, res):
        if res['status'] in ('logged', 'tagged', 'created'):
            self.written += 1
        elif res['status'] == 'already':
            self.already += 1
        elif res['status'] == 'stray':
            self.strays += 1

    def question_for(self, ev, res):
        """Queue the question an event that could not be written needs (nothing is guessed)."""
        kind_name, day = KIND_NAME.get(ev['kind'], ev['kind']), local_date(ev['at']) or '?'
        if res['status'] == 'unmatched':
            who = f" by {ev['author']}" if ev.get('author') else ''
            options = "Answer with the Notion Book's title, or 'drop'"
            if res['why'].startswith('several'):
                options += '. Candidates: ' + res['why'].split(': ', 1)[1]
            self.unmatched.append({**ev, 'reason': res['why']})
            return self.queue.ask('Unmatched book', f'Which Book is “{ev["title"]}”{who}? Goodreads {kind_name} on {day}: {res["why"]}',
                                  'event:' + ev['guid'], options=options, event=ev)
        if res['status'] == 'finish_mismatch':
            book, read = res['book'], res['read']
            self.unmatched.append({**ev, 'reason': res['why'], 'book_id': book['id']})
            return self.queue.ask('Finish date', f'Goodreads says {kind_name} “{book["title"]}” on {day}; Notion’s {read["title"]} ended {read["ended"]}',
                                  'event:' + ev['guid'], book=book['id'], read=read['id'], default_applied=True,
                                  proposed=f"left as is: Ended {read['ended']} kept, the Goodreads {kind_name} not logged",
                                  options="Answer 'attach' to log it to that Read anyway, a Read title, or 'drop'", event=ev)
        if res['status'] == 'reread_or_reopen':
            book, read = res['book'], res['read']
            self.unmatched.append({**ev, 'reason': res['why'], 'book_id': book['id']})
            return self.queue.ask('Reread or reopen', f'Goodreads shows you back on “{book["title"]}” ({kind_name} {day}) after you finished it on {read["ended"]}: reread, or reopen?',
                                  'event:' + ev['guid'], book=book['id'], read=read['id'], proposed='nothing done until answered',
                                  options="Answer 'reopen' (clears Ended and Outcome on that Read) or 'reread' (a new Read), or 'drop'", event=ev)
        if res['status'] == 'no_read':
            book = res['book']
            last = self.store.recent_read(book['id'])
            proposed = (f"last Read in Notion: {last['title']} ({last['outcome'] or 'open'}" + (f", ended {last['ended']}" if last['ended'] else '') + ')') if last else 'no Read of this Book in Notion'
            self.unmatched.append({**ev, 'reason': res['why'], 'book_id': book['id']})
            if ev['kind'] in ('finished', 'dnf'):
                return self.queue.ask('Finish date', f'Goodreads {kind_name} “{book["title"]}” on {day}, but Notion has no Read of it to close',
                                      'event:' + ev['guid'], book=book['id'], options="Answer with the Read to attach it to (its title), 'new' for a new Read closed on that date, or 'drop'",
                                      proposed=proposed, event=ev)
            return self.queue.ask('No open Read', f'Goodreads {kind_name} on “{book["title"]}” {day}, but Notion has no open Read',
                                  'event:' + ev['guid'], book=book['id'], options="Answer with the Read to attach it to (its title), or 'drop'", proposed=proposed, event=ev)
        return None

    # ---- the queue
    def apply_answers(self):
        """Rows Dan answered or confirmed since the last run are applied first."""
        for row in list(self.queue.open_rows()):
            answer, confirm = (row['answer'] or '').strip(), row['confirm']
            if not answer and not confirm:
                continue
            if answer.lower() in DROP_ANSWERS:
                self.queue.resolve(row, 'Dismissed')
                continue
            kind, ev = row['kind'], row['event']
            read = self.store.read(row['read']) if row.get('read') else None
            book = self.books_by_id.get(row.get('book'))
            try:
                if ev and kind in ('Unmatched book', 'No open Read', 'Finish date', 'Reread or reopen'):
                    self.apply_event_answer(row, answer, confirm, kind, ev, book, read)
                elif kind in ('Edition', 'Edition change'):
                    self.apply_edition_answer(row, answer, confirm, kind, book, read)
                elif kind == 'Pages read':
                    self.apply_pages_answer(row, answer, confirm, read)
                elif kind == 'Still reading':
                    self.apply_still_reading_answer(row, answer, confirm, book, read)
                else:
                    self.queue.resolve(row, 'Answered')  # Gap and the like: confirmed or answered, nothing to apply
            except RuntimeError as e:
                log('ERROR applying answer', row['key'], e)
                self.errors.append(f"answer {row['key']}: {e}")

    def apply_event_answer(self, row, answer, confirm, kind, ev, book, read):
        if not answer:
            self.queue.resolve(row, 'Answered', answer='confirmed: event left unlogged')
            return
        a = answer.lower()
        day = local_date(ev['at'])
        if kind == 'Unmatched book':
            book = self.book_from_answer(answer)
            if not book:
                self.queue.note(row, f'could not apply “{answer}”: no single Book with that title')
                return
            res = self.handle(ev, book=book)
        elif kind == 'Reread or reopen':
            if a == 'reopen' and read:
                res = self.handle(ev, book=book, force_reopen=True)
            elif a == 'reread':
                res = self.handle(ev, book=book, force_new=True)
            else:
                self.queue.note(row, f'could not apply “{answer}”: answer reopen, reread or drop')
                return
        elif kind == 'Finish date' and a == 'close' and read:
            self.close_read(read, ev['kind'], day, book, ev=ev, force=True)
            res = self.handle(ev, book=book, read=read)
        elif kind == 'Finish date' and a == 'new':
            res = self.handle(ev, book=book, force_new=True)
        else:
            target = read if (a == 'attach' and read) else self.read_from_answer(answer, row['book'])
            if not book or not target:
                self.queue.note(row, f'could not apply “{answer}”: no Read of that title on the Book')
                return
            res = self.handle(ev, book=book, read=target)
        self.count(res)
        if res['status'] in ('logged', 'already'):
            self.queue.resolve(row, 'Answered', proposed=f"applied: logged to {res['read']['title']}")
        else:
            self.queue.note(row, f'could not apply “{answer}”: {res.get("why", res["status"])}')

    def apply_edition_answer(self, row, answer, confirm, kind, book, read):
        if not read or not book:
            self.queue.note(row, 'could not apply: the Read or Book no longer exists')
            return
        if answer:
            if read.get('edition') and not read.get('edition_inferred'):
                self.queue.note(row, f'could not apply “{answer}”: the copy on {read["title"]} was set by you; change it by chat')
                return
            if kind == 'Edition change' and read['outcome']:
                self.queue.note(row, f'could not apply “{answer}”: {read["title"]} is closed; a closed Read’s copy changes only by chat')
                return
            ed = self.edition_from_answer(book['id'], answer)
            if not ed:
                self.queue.note(row, f'could not apply “{answer}”: no single copy of {book["title"]} matches')
                return
            self.store.update_read(read, {'Edition': {'relation': [{'id': ed['id']}]}, 'Edition inferred': {'checkbox': False}},
                                   f'Edition set to {copy_label(ed)} by Dan {self.store.today}')
            read['edition'], read['edition_inferred'] = [ed['id']], False
            self.queue.resolve(row, 'Answered', proposed=f'applied: {copy_label(ed)}')
            return
        if read.get('edition') and read.get('edition_inferred'):
            self.store.update_read(read, {'Edition inferred': {'checkbox': False}}, f'Confirmed by Dan {self.store.today}')
            read['edition_inferred'] = False
        self.queue.resolve(row, 'Answered', answer='confirmed')

    def apply_pages_answer(self, row, answer, confirm, read):
        if not read:
            self.queue.note(row, 'could not apply: the Read no longer exists')
            return
        if not answer:
            self.store.update_read(read, {}, f'Confirmed by Dan {self.store.today}')
            self.queue.resolve(row, 'Answered', answer='confirmed')
            return
        m_end = re.search(r'ended\s+(\d{4}-\d\d-\d\d)', answer, re.I)
        m_pages = re.search(r'(?<![\d-])(\d{1,5})(?![\d-])', re.sub(r'\d{4}-\d\d-\d\d', '', answer))
        if not m_end and not m_pages:
            self.queue.note(row, f'could not apply “{answer}”: answer a page count and/or ended YYYY-MM-DD')
            return
        props, lines = {}, []
        if m_pages:
            props['Pages read'] = {'number': int(m_pages.group(1))}
            lines.append(f'Pages read {int(m_pages.group(1))}')
        if m_end:
            props['Ended'] = {'date': {'start': m_end.group(1)}}
            lines.append(f'Ended {m_end.group(1)}')
        self.store.update_read(read, props, ', '.join(lines) + f' by Dan {self.store.today}')
        self.queue.resolve(row, 'Answered', proposed='applied: ' + ', '.join(lines))

    def apply_still_reading_answer(self, row, answer, confirm, book, read):
        if not read:
            self.queue.note(row, 'could not apply: the Read no longer exists')
            return
        if not answer or answer.lower() in ('still', 'still reading', 'yes', 'reading'):
            self.queue.resolve(row, 'Answered', answer=answer or 'confirmed: still reading')
            return
        m = re.match(r'(finished|dnf)\s+(\d{4}-\d\d-\d\d)(?:[\s,]*pages?\s*(\d+))?', answer, re.I)
        if not m or not book:
            self.queue.note(row, f'could not apply “{answer}”: answer still, finished YYYY-MM-DD [pages N] or dnf YYYY-MM-DD [pages N]')
            return
        kind = m.group(1).lower()
        self.close_read(read, kind, m.group(2), book, pages=int(m.group(3)) if m.group(3) else None, by_dan=True)
        self.queue.resolve(row, 'Answered', proposed=f'applied: {KIND_NAME[kind]} {m.group(2)}')

    def retry_pending(self):
        """Events that could not be written on an earlier run are tried again; the fix (a Read, a Book) usually came by chat."""
        for row in list(self.queue.open_rows()):
            if row['kind'] not in ('Unmatched book', 'No open Read', 'Finish date', 'Reread or reopen') or not row['event'] or row['status'] != 'Open':
                continue
            res = self.guarded(f"pending {row['key']}", self.handle, row['event'])
            self.count(res)
            if res['status'] in ('logged', 'already'):
                self.queue.resolve(row, 'Answered', answer=f"auto: logged to {res['read']['title']}")

    def feed(self, events):
        events = sorted(events, key=lambda e: e['at'] or '')
        strays = set()
        for i, ev in enumerate(events):
            if ev['kind'] not in ('finished', 'dnf'):
                continue
            key = ev['gr_book_id'] or norm(ev['title'])
            later = [e for e in events[i + 1:] if e['kind'] in ('progress', 'started') and (e['gr_book_id'] or norm(e['title'])) == key and (e['at'] or '') > (ev['at'] or '')]
            if later:
                strays.add(ev['guid'])
        for ev in events:
            res = self.guarded(ev['guid'], self.handle, ev, stray=ev['guid'] in strays)
            self.count(res)
            if res['status'] in ('unmatched', 'no_read', 'finish_mismatch', 'reread_or_reopen'):
                self.guarded(f"question {ev['guid']}", self.question_for, ev, res)

    # ---- shelves (state), consulted only inside the fence
    def shelf_events(self, shelf, items, watermark, open_books):
        """Shelf items as events the feed may have aged out. The currently-reading shelf is always inside the fence
        (it describes now, not history); read and did-not-finish items only when dated after the watermark or when
        their Book has an open Read in Notion. Without a watermark (first run) the fence is 48 hours."""
        out, fenced_out = [], 0
        fence = to_dt(watermark) if watermark else to_dt(self.store.now) - timedelta(hours=FENCE_MARGIN_HOURS)
        for it in items:
            added, read_at = rfc_iso(it['user_date_added']), rfc_date(it['user_read_at'])
            book, _ = self.lib.match(it['title'], it['author_name'])
            times = [to_dt(added)] + ([to_dt(read_at, end_of_day=True)] if read_at else [])
            latest = max(x for x in times if x) if any(times) else None
            inside = shelf == 'currently-reading' or (latest is not None and latest > fence) or (shelf != 'to-read' and book is not None and book['id'] in open_books)
            if not inside:
                fenced_out += 1
                continue
            m = re.search(r'/review/show/(\d+)', it['guid'] or it['link'] or '')
            review = m.group(1) if m else (it['book_id'] or 'x')
            if shelf == 'currently-reading':
                kind, day = 'started', local_date(added)
            elif shelf == 'read':
                kind, day = 'finished', read_at or local_date(added)
            elif shelf == 'to-read':
                kind, day = 'wishlisted', local_date(added)
            else:
                kind, day = 'dnf', read_at or local_date(added)
            if not day:
                continue
            ev = {'kind': kind, 'title': it['title'], 'author': it['author_name'] or None, 'gr_book_id': it['book_id'] or None,
                  'guid': f'Shelf{review}:{kind}:{day}', 'at': day, 'raw': f'{shelf} shelf: {it["title"]}, added {it["user_date_added"]}' + (f', read at {read_at}' if read_at else ''),
                  'note': None, 'review_url': it['link'] if '/review/show/' in (it['link'] or '') else None, 'source': 'shelf'}
            if kind == 'started':
                ev['estimated'] = True
            if it.get('num_pages', '').isdigit():
                ev['total'] = int(it['num_pages'])
            if kind == 'wishlisted':
                ev.update({'pages': it.get('num_pages'), 'published': it.get('book_published'), 'isbn': it.get('isbn')})
            out.append(ev)
        return out, fenced_out

    def still_reading(self, items):
        """Open Reads in Notion whose Book is not on the currently-reading shelf: still reading, or finished?"""
        on_shelf = set()
        for it in items:
            book, _ = self.lib.match(it['title'], it['author_name'])
            if book:
                on_shelf.add(book['id'])
        for rd in self.store.all_open():
            if rd.get('book') in on_shelf or not rd.get('started') or days_between(rd['started'], self.store.today) <= 2:
                continue
            self.queue.ask('Still reading', f'Notion has \u201c{rd["title"]}\u201d open since {rd["started"]}, but it is not on the Goodreads currently-reading shelf: still reading, or did you finish?',
                           f'still:{rd["id"]}', book=rd.get('book'), read=rd['id'], default_applied=True, proposed='left open',
                           options="Answer 'still' to keep it open, or 'finished YYYY-MM-DD [pages N]' / 'dnf YYYY-MM-DD [pages N]' to close it", reask_days=14)


def export_queue(store, lib):
    """The open questions as the dashboard's sync_queue dataset: one row per Open question, newest first."""
    queue = SyncQueue(store)
    titles = {b['id']: b['title'] for b in lib.books}
    rows = []
    for q in sorted(queue.open_rows(), key=lambda q: q['asked'] or '', reverse=True):
        if q['kind'] == 'Sync run':
            continue
        read = store.read(q['read']) if q.get('read') else None
        rows.append({'kind': q['kind'], 'question': q['question'], 'proposed': q['proposed'], 'options': q['options'], 'default_applied': q['default_applied'],
                     'book': titles.get(q.get('book'), ''), 'read': read['title'] if read else '', 'asked': (q['asked'] or '')[:10], 'key': q['key']})
    return rows


def feed_window(items):
    times = sorted(x for x in (rfc_iso(it['pubDate']) for it in items) if x)
    return {'oldest': times[0] if times else None, 'newest': times[-1] if times else None, 'n': len(items)}


def run_sync(notion, items, shelves, state, *, dry_run=False, offline_lib=None, now=None, since=None, partial=False):
    """Match and write one batch of feed items and fenced shelf items. `notion` is a Notion (or a test
    double); None with `offline_lib` for an offline plan. `shelves` maps shelf name -> items (only the
    shelves fetched). Returns the report; the writes are in report['plan']."""
    now = now or now_iso()
    events = [e for e in (parse_update(it) for it in items) if e]
    log(f'feed: {len(items)} items, {len(events)} tracked events; shelves: ' + ', '.join(f'{k} {len(v)}' for k, v in shelves.items()))
    lib = offline_lib or Library(notion)
    if not offline_lib:
        lib.load()
    store = Store(notion, lib, dry_run=dry_run, today=local_date(now))
    store.now = now
    queue = SyncQueue(store)
    run = Run(store, lib, queue, state)
    run.to_read_by_id = {it['book_id']: it for it in shelves.get('to-read', []) if it.get('book_id')}
    # the fence: only what happened after the last successful run (less a margin) is an event
    last = queue.last_run()
    if since:
        watermark = to_dt(since).isoformat()
    elif last and last['asked']:
        watermark = (to_dt(last['asked']) - timedelta(hours=FENCE_MARGIN_HOURS)).isoformat()
    else:
        watermark = None
    log(f'watermark: {watermark or "none (first run: whole feed)"}' + (f' (last run {last["asked"]})' if last else ''))
    if last and last['asked'] and days_between(last['asked'], local_date(now)) > 2:
        log(f'CHECK no successful run since {last["asked"]}')
        queue.ask('Gap', f'No successful sync since {local_date(last["asked"])}: events between then and now may have aged out of the updates feed',
                  'norun:' + last['asked'], default_applied=True, proposed='noted; the shelves recover starts and finishes, page updates may be missing (docs/GAP_FILL.md)',
                  options='Tick Confirm once checked')
    window = feed_window(items)
    last_window = ((last or {}).get('event') or {}).get('window') or {}
    if window['oldest'] and last_window.get('newest') and to_dt(window['oldest']) > to_dt(last_window['newest']):
        queue.ask('Gap', f'Page updates between {local_date(last_window["newest"])} {last_window["newest"][11:16]} and {local_date(window["oldest"])} {window["oldest"][11:16]} may be missing: '
                         'the updates feed had already dropped them', 'gap:' + last_window['newest'], default_applied=True,
                  proposed='noted; fill by hand from the Goodreads review pages if it matters (docs/GAP_FILL.md)', options="Tick Confirm once filled or if it doesn't matter")
    fenced_out = 0
    if watermark:
        kept = [e for e in events if e['at'] and to_dt(e['at']) > to_dt(watermark)]
        fenced_out = len(events) - len(kept)
        events = kept
    run.apply_answers()
    run.retry_pending()
    run.feed(events)
    if not events:
        log('no new events')
    open_books = {r.get('book') for r in store.all_open()} if shelves else set()
    for shelf in SHELVES:
        if shelf not in shelves:
            continue
        shelf_evs, out = run.shelf_events(shelf, shelves[shelf], watermark, open_books)
        fenced_out += out
        log(f'{shelf} shelf: {len(shelf_evs)} item(s) inside the fence, {out} outside')
        run.feed(shelf_evs)
    if 'currently-reading' in shelves:
        run.still_reading(shelves['currently-reading'])
    removed = run.wishlist_removed(shelves['to-read']) if 'to-read' in shelves else []
    summary = {'at': now, 'items': len(items), 'new': len(events), 'fenced_out': fenced_out, 'written': run.written, 'already': run.already, 'strays': run.strays,
               'unmatched': len(run.unmatched), 'asked': len(queue.asked), 'window': window, 'watermark': watermark,
               'shelves': {k: len(v) for k, v in shelves.items()}, 'partial': partial, 'errors': run.errors}
    if partial or run.errors:
        log('watermark not advanced:', 'partial run' if partial else f'{len(run.errors)} error(s)')
    else:
        queue.record_run(summary)
    open_questions = [q for q in queue.open_rows(refresh=True) if q['status'] == 'Open'] if (queue.enabled and not dry_run) else list(queue.open_rows())
    return {**summary, 'unmatched': run.unmatched, 'questions': queue.asked, 'open_questions': open_questions, 'wishlist_removed': removed, 'plan': store.plan}


def run(args):
    load_env()
    if args.init_queue:
        if cfg('NOTION_QUEUE_DS'):
            sys.exit(f"NOTION_QUEUE_DS is already set ({cfg('NOTION_QUEUE_DS')}); unset it first if you really want a second queue")
        ds = init_queue(Notion(cfg('NOTION_TOKEN', required=True)), cfg('NOTION_LIBRARY_PAGE'))
        print(f'NOTION_QUEUE_DS={ds}')
        print('Add that line to sync/.env and to the workflow env (and as the default in goodreads_sync.py).')
        return 0
    if args.export_queue:
        notion = Notion(cfg('NOTION_TOKEN', required=True))
        cfg('NOTION_QUEUE_DS', required=True)
        lib = Library(notion)
        lib.load()
        rows = export_queue(Store(notion, lib), lib)
        Path(args.export_queue).write_text(json.dumps(rows, ensure_ascii=False) + '\n', encoding='utf-8')
        log(f'{len(rows)} open question(s) written to {args.export_queue}')
        return 0
    state = load_state()
    fixtures = {'updates': args.fixture, 'currently-reading': args.shelf_fixture, 'read': args.read_fixture, 'did-not-finish': args.dnf_fixture, 'to-read': args.to_read_fixture}
    urls = feed_urls() if not (args.fixture and (args.parse_only or all(fixtures[s] for s in SHELVES) or not args.fetch_shelves)) else {}
    if args.parse_only:
        items = rss_items(Path(args.fixture).read_bytes()) if args.fixture else rss_items(fetch_with_retry(urls['updates']))
        for e in (parse_update(it) for it in items):
            if e:
                print(json.dumps(e, ensure_ascii=False))
        return 0
    items, shelves, failed = gather(urls, fixtures, fetch_missing=bool(urls))
    nothing_fetched = items is None and not shelves
    if items is not None and not args.fixture and not args.no_archive:
        n = archive_items(ARCHIVE_DIR, items, local_date(now_iso()))
        log(f'feed archive: {n} new item(s) in {ARCHIVE_DIR.name}/')
    if failed:
        log('CHECK partial fetch; failed:', ', '.join(failed), '- answers, pending events and the other feeds still run; the watermark stays')
    if args.parse_only:
        for e in (parse_update(it) for it in items):
            if e:
                print(json.dumps(e, ensure_ascii=False))
        return 0
    offline = bool(args.offline_library)
    notion = None if offline else Notion(cfg('NOTION_TOKEN', required=True), dry_run=args.dry_run)
    if not offline:
        cfg('NOTION_QUEUE_DS', required=True)
    lib = None
    if offline:
        lib = Library(None)
        lib.load_raw(args.offline_library)
    report = run_sync(notion, items or [], shelves, state, dry_run=args.dry_run, offline_lib=lib, since=args.since, partial=bool(failed))
    report['nothing_fetched'] = nothing_fetched
    if args.plan:
        Path(args.plan).write_text(json.dumps(report['plan'], indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        log(f"{len(report['plan'])} planned write(s) saved to {args.plan}")
    view = {'at': report['at'], 'open_questions': [{k: v for k, v in q.items() if k != 'event'} for q in report['open_questions']],
            'asked_this_run': [{k: v for k, v in q.items() if k != 'event'} for q in report['questions']],
            'unmatched_events': report['unmatched'], 'wishlist_removed': report['wishlist_removed']}
    if view['open_questions'] or view['asked_this_run'] or report['unmatched'] or report['wishlist_removed']:
        UNMATCHED_FILE.write_text(json.dumps(view, indent=1, ensure_ascii=False), encoding='utf-8')
        log(f"{len(view['open_questions'])} open question(s), {len(report['unmatched'])} unmatched event(s) written to {UNMATCHED_FILE.name}")
    elif UNMATCHED_FILE.exists():
        UNMATCHED_FILE.unlink()
    if not args.dry_run and not offline:
        state['runs'] = (state.get('runs') or [])[-30:] + [{k: report[k] for k in ('at', 'new', 'written', 'asked', 'fenced_out')} | {'unmatched': len(report['unmatched'])}]
        save_state(state)
    log(f"done: {report['written']} logged, {report['already']} already there, {len(report['unmatched'])} unmatched, {len(report['questions'])} question(s) asked, "
        f"{len(report['open_questions'])} open, {report['fenced_out']} outside the fence" + (' (dry run, nothing saved)' if args.dry_run or offline else '')
        + (f"; {len(report['errors'])} ERROR(S)" if report['errors'] else '') + ('; NOTHING FETCHED' if nothing_fetched else ''))
    return exit_code(report)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='read Notion, print the writes, save nothing')
    ap.add_argument('--fixture', help='parse this saved RSS file instead of fetching')
    ap.add_argument('--parse-only', action='store_true', help='print parsed events and stop; no Notion')
    ap.add_argument('--shelf-fixture', help='parse this saved currently-reading shelf RSS instead of fetching')
    ap.add_argument('--read-fixture', help='parse this saved read shelf RSS instead of fetching')
    ap.add_argument('--dnf-fixture', help='parse this saved did-not-finish shelf RSS instead of fetching')
    ap.add_argument('--to-read-fixture', help='parse this saved to-read shelf RSS instead of fetching')
    ap.add_argument('--fetch-shelves', action='store_true', help='with --fixture: still fetch the shelves that have no fixture')
    ap.add_argument('--since', metavar='ISO', help='override the watermark: take events and shelf items after this date/time')
    ap.add_argument('--no-archive', action='store_true', help='do not append the fetched feed to sync/feed_archive/')
    ap.add_argument('--offline-library', metavar='DIR', help='match against data/raw/*.json instead of Notion; implies dry run')
    ap.add_argument('--plan', metavar='FILE', help='save the planned writes as JSON (any mode)')
    ap.add_argument('--init-queue', action='store_true', help='create the Sync queue database under the Library page and print its id')
    ap.add_argument('--export-queue', metavar='FILE', help="write the open questions as the dashboard's sync_queue dataset (JSON) and stop")
    return ap.parse_args(argv)


if __name__ == '__main__':
    sys.exit(run(parse_args()))
