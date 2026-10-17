"""Unit tests for sync/goodreads_sync.py. Offline: a fake of the Notion API stands in for the workspace.

Run with: python3 -m unittest discover sync
"""
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault('NOTION_TOKEN', 'test-token')

import goodreads_sync as gs  # noqa: E402

gs.QUIET = True

FIX = HERE / 'fixtures'
os.environ.setdefault('NOTION_QUEUE_DS', 'queue-ds-for-tests')
BOOKS_DS, READS_DS, AUTHORS_DS, LOG_DS = gs.cfg('NOTION_BOOKS_DS'), gs.cfg('NOTION_READS_DS'), gs.cfg('NOTION_AUTHORS_DS'), gs.cfg('NOTION_LOG_DS')
QUEUE_DS = gs.cfg('NOTION_QUEUE_DS')


# ---------------------------------------------------------------- a fake Notion API
def _plain(arr):
    return ''.join(t.get('plain_text') or t.get('text', {}).get('content', '') for t in arr or [])


def _normalise(props):
    """Write-shape properties -> the read shape the API returns (plain_text on rich text)."""
    out = {}
    for k, v in props.items():
        v = json.loads(json.dumps(v))
        for key in ('title', 'rich_text'):
            if key in v:
                for t in v[key]:
                    t.setdefault('plain_text', t.get('text', {}).get('content', ''))
        out[k] = v
    return out


def _match(flt, page):
    if not flt:
        return True
    if 'and' in flt:
        return all(_match(f, page) for f in flt['and'])
    if 'or' in flt:
        return any(_match(f, page) for f in flt['or'])
    p = page['properties'].get(flt['property']) or {}
    if 'relation' in flt:
        ids, c = [r['id'] for r in p.get('relation', [])], flt['relation']
        if 'contains' in c:
            return c['contains'] in ids
        if 'is_empty' in c:
            return not ids
        if 'is_not_empty' in c:
            return bool(ids)
    if 'select' in flt:
        name, c = (p.get('select') or {}).get('name'), flt['select']
        if 'equals' in c:
            return name == c['equals']
        if 'does_not_equal' in c:
            return name != c['does_not_equal']
        if 'is_empty' in c:
            return name is None
        if 'is_not_empty' in c:
            return name is not None
    for key in ('rich_text', 'title'):
        if key in flt:
            text, c = _plain(p.get('rich_text') or p.get('title')), flt[key]
            if 'equals' in c:
                return text == c['equals']
            if 'starts_with' in c:
                return text.startswith(c['starts_with'])
            if 'contains' in c:
                return c['contains'] in text
            if 'is_empty' in c:
                return text == ''
            if 'is_not_empty' in c:
                return text != ''
    if 'checkbox' in flt:
        return bool(p.get('checkbox')) == flt['checkbox']['equals']
    if 'date' in flt:
        start, c = (p.get('date') or {}).get('start'), flt['date']
        if 'is_empty' in c:
            return start is None
        if 'is_not_empty' in c:
            return start is not None
        if start is None:
            return False
        if 'on_or_after' in c:
            return start >= c['on_or_after']
        if 'after' in c:
            return start > c['after']
        if 'before' in c:
            return start < c['before']
        if 'on_or_before' in c:
            return start <= c['on_or_before']
        if 'equals' in c:
            return start[:10] == c['equals'][:10]
    if 'number' in flt:
        n, c = p.get('number'), flt['number']
        if 'equals' in c:
            return n == c['equals']
        if 'is_empty' in c:
            return n is None
    raise AssertionError(f'FakeNotion: unsupported filter {flt}')


class FakeNotion:
    """Enough of the Notion API for the sync: pages per data source, the filters the script uses, writes recorded."""

    def __init__(self):
        self.pages, self.writes, self.dry_run, self._n = {}, [], False, 0
        self.databases = []

    def seed(self, ds, props, page_id=None):
        self._n += 1
        pid = page_id or f'page{self._n:04d}'
        self.pages[pid] = {'id': pid, 'ds': ds, 'properties': _normalise(props)}
        return pid

    def query_all(self, ds_id, flt=None, page_size=100):
        return [json.loads(json.dumps(p)) for p in self.pages.values() if p['ds'] == ds_id and _match(flt, p)]

    def create_page(self, ds_id, props):
        self.writes.append(('create', ds_id, props))
        return {'id': self.seed(ds_id, props)}

    def update_page(self, page_id, props):
        self.writes.append(('update', page_id, props))
        self.pages[page_id]['properties'].update(_normalise(props))
        return {'id': page_id}

    def retrieve_page(self, page_id):
        return json.loads(json.dumps(self.pages[page_id]))

    def create_database(self, parent_page_id, title, properties):
        self.databases.append({'parent': {'type': 'page_id', 'page_id': parent_page_id}, 'title': title, 'properties': properties})
        return f'ds-{len(self.databases)}'

    # test helpers
    def creates(self, ds=None):
        return [w[2] for w in self.writes if w[0] == 'create' and (ds is None or w[1] == ds)]

    def updates(self, page_id=None):
        return [w[2] for w in self.writes if w[0] == 'update' and (page_id is None or w[1] == page_id)]

    def prop(self, page_id, name):
        return self.pages[page_id]['properties'].get(name)


def text(s):
    return {'rich_text': [{'text': {'content': s}}]}


def title(s):
    return {'title': [{'text': {'content': s}}]}


def rel(*ids):
    return {'relation': [{'id': i} for i in ids if i]}


def date(s):
    return {'date': {'start': s}} if s else {'date': None}


def select(s):
    return {'select': {'name': s}} if s else {'select': None}


def rss_item(guid, pub, title_text, desc_html, link=''):
    return {'title': title_text, 'description': desc_html, 'link': link, 'guid': guid, 'pubDate': pub,
            'book_id': '', 'isbn': '', 'isbn13': '', 'author_name': '', 'num_pages': '', 'user_read_at': '', 'user_date_added': ''}


class Workspace:
    """A small library in a FakeNotion: authors, books (with page ids), reads."""

    def __init__(self):
        self.n = FakeNotion()
        self.books = {}

    def author(self, name):
        return self.n.seed(AUTHORS_DS, {'Name': title(name)})

    def book(self, t, author=None):
        aid = self.author(author) if author else None
        pid = self.n.seed(BOOKS_DS, {'Title': title(t), 'Author': rel(aid)})
        self.books[t] = pid
        return pid

    def read(self, t, book, started=None, ended=None, outcome=None, estimated=False, pages_read=None, notes='', edition=None, inferred=False):
        return self.n.seed(READS_DS, {'Read': title(t), 'Book': rel(book), 'Started': date(started), 'Ended': date(ended), 'Outcome': select(outcome),
                                      'Dates estimated': {'checkbox': estimated}, 'Pages read': {'number': pages_read}, 'Notes': text(notes),
                                      'Edition': rel(edition), 'Edition inferred': {'checkbox': inferred}})

    def log(self, guid, read, book, kind, when, page=None):
        props = {'Name': title(f'x · {kind} · {when[:10]}'), 'Read': rel(read), 'Book': rel(book), 'Kind': select(kind), 'When': date(when),
                 'Goodreads id': text(guid), 'Source': select('Goodreads')}
        if page is not None:
            props['Page'] = {'number': page}
        return self.n.seed(LOG_DS, props)

    def question(self, kind, question, key, status='Open', answer='', confirm=False, event=None, guid='', book=None, read=None, asked='2026-10-16T23:45:00-04:00', proposed='', default_applied=False):
        props = {'Question': title(question), 'Kind': select(kind), 'Status': select(status), 'Key': text(key), 'Answer': text(answer),
                 'Confirm': {'checkbox': confirm}, 'Event': text(json.dumps(event) if event else ''), 'Goodreads id': text(guid), 'Book': rel(book), 'Read': rel(read),
                 'Asked': date(asked), 'Proposed': text(proposed), 'Default applied': {'checkbox': default_applied}}
        return self.n.seed(QUEUE_DS, props)

    def queue_rows(self, **flt):
        rows = [p for p in self.n.pages.values() if p['ds'] == QUEUE_DS]
        out = [gs.queue_norm(r) for r in rows]
        return [r for r in out if all(r.get(k) == v for k, v in flt.items())]

    def sync(self, items, shelf=None, state=None, now='2026-10-17T09:00:00-04:00', read_shelf=None, dnf_shelf=None, **kw):
        state = state if state is not None else {'book_cache': {}, 'runs': []}
        shelves = {k: list(v) for k, v in (('currently-reading', shelf), ('read', read_shelf), ('did-not-finish', dnf_shelf)) if v is not None}
        return gs.run_sync(self.n, list(items), shelves, state, now=now, **kw), state


PAGE_ITEM = rss_item('UserStatus1', 'Thu, 15 Oct 2026 21:12:03 -0400', 'Dan is on page 120 of 400 of The Brothers Karamazov',
                     'Dan is on page 120 of 400 of <a href="https://www.goodreads.com/book/show/4934.The_Brothers_Karamazov">The Brothers Karamazov</a> by '
                     '<a href="https://www.goodreads.com/author/show/3137322.Fyodor_Dostoevsky">Fyodor Dostoevsky</a>')
PCT_ITEM = rss_item('UserStatus2', 'Wed, 14 Oct 2026 22:40:00 -0400', 'Dan is 35% done with Middlemarch',
                    'Dan is 35% done with <a href="https://www.goodreads.com/book/show/19089.Middlemarch">Middlemarch</a> by George Eliot')
NOTE_ITEM = rss_item('UserStatus3', 'Sun, 04 Oct 2026 22:10:00 -0400', 'Dan is on page 53 of 964 of Lonesome Dove',
                     '<a href="/user/show/1-dan">Dan</a> is on page 53 of 964 of <a href="/book/show/56346987-lonesome-dove">Lonesome Dove</a>.<br/>'
                     '“Quite the bedtime story.”')
BY_ITEM = rss_item('ReadStatus4', 'Mon, 12 Oct 2026 08:05:00 -0400', 'Dan started reading Release It! by Michael Nygard',
                   'Dan started reading <a href="https://www.goodreads.com/book/show/1069827.Release_It_">Release It!: Design and Deploy Production-Ready Software</a>',
                   link='https://www.goodreads.com/review/show/2003')
SERIES_ITEM = rss_item('UserStatus5', 'Fri, 09 Oct 2026 20:00:00 -0400', 'Dan is on page 85 of 964 of Lonesome Dove (Lonesome Dove, #1)',
                       'Dan is on page 85 of 964 of <a href="/book/show/56346987-lonesome-dove">Lonesome Dove (Lonesome Dove, #1)</a>')
LIKED_ITEM = rss_item('Rating6', 'Thu, 15 Oct 2026 06:59:22 -0700', 'Dan liked a readstatus',
                      "<div class='updateAction'><strong><a href=\"/user/show/1-dan\">Dan</a></strong> liked <a href=\"/read_statuses/1\">Taylor’s status update</a></div>"
                      '<a href="/user/show/2-taylor">Taylor Lance</a> is currently reading <a href="/book/show/228974086-murder">Murder at the Grand Alpine Hotel</a>')
WTR_ITEM = rss_item('ReadStatus7', 'Fri, 09 Oct 2026 12:00:00 -0400', "Dan wants to read 'Oroonoko'",
                    'Dan wants to read <a class="bookTitle" href="/book/show/77.Oroonoko">Oroonoko</a> <span class="by">by</span> <a class="authorName" href="/author/show/9.Aphra_Behn">Aphra Behn</a>')
DNF_ITEM = rss_item('ReadStatus8', 'Fri, 16 Oct 2026 19:18:50 -0700', "Dan did not finish 'The Cauldron: The Making of the Modern Middle East'",
                    '<a href="/book/show/241861256-the-cauldron"><img src="x"/></a> Dan did not finish <a class="bookTitle" href="/book/show/241861256-the-cauldron">'
                    'The Cauldron: The Making of the Modern Middle East</a> <span class="by">by</span> <a class="authorName" href="/author/show/371093.Simon_Sebag_Montefiore">Simon Sebag Montefiore</a>',
                    link='https://www.goodreads.com/review/show/8529215161')
LATE_ITEM = rss_item('UserStatus9', 'Thu, 15 Oct 2026 23:30:00 -0700', 'Dan is on page 63 of 869 of Fundamentals of Software Architecture',
                     'Dan is on page 63 of 869 of <a href="/book/show/229076154-fundamentals-of-software-architecture">Fundamentals of Software Architecture</a>.')
FINISHED_ITEM = rss_item('ReadStatus10', 'Thu, 15 Oct 2026 05:29:00 -0700', "Dan finished reading 'Release It!: Design and Deploy Production-Ready Software'",
                         'Dan finished reading <a class="bookTitle" href="/book/show/38733031-release-it">Release It!: Design and Deploy Production-Ready Software</a> '
                         '<span class="by">by</span> <a class="authorName" href="/author/show/6089.Michael_T_Nygard">Michael T. Nygard</a>',
                         link='https://www.goodreads.com/review/show/8984202464')


def started_item(guid, pub, gr_id, title_text, author='Larry McMurtry'):
    return rss_item(guid, pub, f"Dan started reading '{title_text}'",
                    f'Dan started reading <a class="bookTitle" href="/book/show/{gr_id}-x">{title_text}</a> <span class="by">by</span> '
                    f'<a class="authorName" href="/author/show/1.x">{author}</a>', link='https://www.goodreads.com/review/show/1')


def progress_item(guid, pub, gr_id, title_text, page, total):
    return rss_item(guid, pub, f'Dan is on page {page} of {total} of {title_text}',
                    f'<a href="/user/show/1-dan">Dan</a> is on page {page} of {total} of <a href="/book/show/{gr_id}-x">{title_text}</a>.')


# ---------------------------------------------------------------- parser
class ParserTests(unittest.TestCase):
    def test_page_of_total(self):
        ev = gs.parse_update(PAGE_ITEM)
        self.assertEqual((ev['kind'], ev['page'], ev['total'], ev['percent']), ('progress', 120, 400, 30.0))
        self.assertEqual((ev['title'], ev['author'], ev['gr_book_id']), ('The Brothers Karamazov', 'Fyodor Dostoevsky', '4934'))
        self.assertEqual(ev['at'], '2026-10-15T21:12:03-04:00')

    def test_percent_only(self):
        ev = gs.parse_update(PCT_ITEM)
        self.assertEqual((ev['kind'], ev.get('page'), ev['percent'], ev['title']), ('progress', None, 35, 'Middlemarch'))

    def test_one_decimal_percent(self):
        ev = gs.parse_update(LATE_ITEM)
        self.assertEqual(ev['percent'], 7.2)  # 63/869 = 7.249…

    def test_note_with_curly_quotes(self):
        ev = gs.parse_update(NOTE_ITEM)
        self.assertEqual((ev['page'], ev['note']), (53, 'Quite the bedtime story.'))

    def test_by_author_suffix_in_title(self):
        ev = gs.parse_update(BY_ITEM)
        self.assertEqual((ev['kind'], ev['title'], ev['author']), ('started', 'Release It!: Design and Deploy Production-Ready Software', 'Michael Nygard'))

    def test_series_suffix_kept_for_the_matcher(self):
        ev = gs.parse_update(SERIES_ITEM)
        self.assertEqual((ev['kind'], ev['page'], ev['title']), ('progress', 85, 'Lonesome Dove (Lonesome Dove, #1)'))

    def test_liked_item_dropped(self):
        self.assertIsNone(gs.parse_update(LIKED_ITEM))

    def test_wants_to_read(self):
        ev = gs.parse_update(WTR_ITEM)
        self.assertEqual((ev['kind'], ev['title'], ev['author'], ev['gr_book_id']), ('wishlisted', 'Oroonoko', 'Aphra Behn', '77'))

    def test_did_not_finish(self):
        ev = gs.parse_update(DNF_ITEM)
        self.assertEqual((ev['kind'], ev['title'], ev['author'], ev['gr_book_id']),
                         ('dnf', 'The Cauldron: The Making of the Modern Middle East', 'Simon Sebag Montefiore', '241861256'))

    def test_finished_with_review_link(self):
        ev = gs.parse_update(FINISHED_ITEM)
        self.assertEqual((ev['kind'], ev['gr_book_id'], ev['review_url']), ('finished', '38733031', 'https://www.goodreads.com/review/show/8984202464'))

    def test_eastern_date_slicing(self):
        ev = gs.parse_update(LATE_ITEM)  # 23:30 Pacific = 02:30 Eastern the next day
        self.assertEqual(gs.local_date(ev['at']), '2026-10-16')
        props = gs.log_props(ev, {'id': 'b1', 'title': 'Fundamentals of Software Architecture'}, {'id': 'r1'})
        self.assertTrue(_plain(props['Name']['title']).endswith('· 2026-10-16'))
        self.assertEqual(props['When']['date']['start'], '2026-10-15T23:30:00-07:00')
        self.assertEqual(gs.local_date('2026-10-14'), '2026-10-14')

    @unittest.skipUnless((FIX / 'live_updates.xml').exists(), 'live fixture not present')
    def test_live_feed_items_have_goodreads_ids(self):
        items = gs.rss_items((FIX / 'live_updates.xml').read_bytes())
        self.assertEqual(len(items), 9)
        self.assertTrue(all(it['guid'].startswith(('ReadStatus', 'UserStatus', 'Rating')) for it in items))
        kinds = [e['kind'] for e in (gs.parse_update(it) for it in items) if e]
        self.assertEqual(kinds, ['dnf', 'progress', 'wishlisted', 'wishlisted', 'wishlisted', 'started', 'finished'])


# ---------------------------------------------------------------- matcher
def small_library():
    rows = [('Stalin: Waiting for Hitler', 'Stephen Kotkin'), ('Stalin: Paradoxes of Power', 'Stephen Kotkin'), ('Animal Liberation', 'Peter Singer'),
            ('Animal Liberation Now', 'Peter Singer'), ('Lonesome Dove', 'Larry McMurtry'), ('Release It!', 'Michael T. Nygard'),
            ('Fundamentals of Software Architecture', 'Mark Richards'), ('Foundation', 'Isaac Asimov'),
            ('Foundation: The History of England from Its Earliest Beginnings to the Tudors', 'Peter Ackroyd'), ('1984', 'George Orwell')]
    lib = gs.Library(None)
    lib.load_rows([{'id': f'b{i}', 'title': t, 'authors': [a]} for i, (t, a) in enumerate(rows)])
    return lib


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.lib = small_library()

    def m(self, t, a=None):
        book, why = self.lib.match(t, a)
        return book['title'] if book else why

    def test_subtitle_truncation(self):
        self.assertEqual(self.m('Fundamentals of Software Architecture: A Modern Engineering Approach', 'Mark Richards'), 'Fundamentals of Software Architecture')
        self.assertEqual(self.m('Release It!: Design and Deploy Production-Ready Software'), 'Release It!')

    def test_series_prefix(self):
        self.assertEqual(self.m('Lonesome Dove (Lonesome Dove, #1)', 'Larry McMurtry'), 'Lonesome Dove')

    def test_stalin_volumes(self):
        self.assertEqual(self.m('Stalin: Waiting for Hitler, 1929-1941', 'Stephen Kotkin'), 'Stalin: Waiting for Hitler')
        self.assertEqual(self.m('Stalin, Vol. 1: Paradoxes of Power, 1878-1928', 'Stephen Kotkin'), 'Stalin: Paradoxes of Power')
        self.assertIn('several books match', self.m('Stalin', 'Stephen Kotkin'))

    def test_animal_liberation(self):
        self.assertEqual(self.m('Animal Liberation Now', 'Peter Singer'), 'Animal Liberation Now')
        self.assertEqual(self.m('Animal Liberation: The Definitive Classic of the Animal Movement', 'Peter Singer'), 'Animal Liberation')

    def test_foundation_subtitle_mismatch(self):
        self.assertEqual(self.m('Foundation (Foundation, #1)', 'Isaac Asimov'), 'Foundation')
        self.assertEqual(self.m('Foundation: The History of England from Its Earliest Beginnings to the Tudors', 'Peter Ackroyd'),
                         'Foundation: The History of England from Its Earliest Beginnings to the Tudors')

    def test_alias(self):
        self.assertEqual(self.m('Nineteen Eighty-Four', 'George Orwell'), '1984')

    def test_no_match(self):
        self.assertEqual(self.m('A Book Nobody Owns'), 'no book with that title')


# ---------------------------------------------------------------- read_norm
class ReadNormTests(unittest.TestCase):
    def test_carries_book_and_flags(self):
        ws = Workspace()
        b = ws.book('Lonesome Dove', 'Larry McMurtry')
        r = ws.read('Lonesome Dove · 2026', b, started='2026-10-04', estimated=True, edition='e1', inferred=True, pages_read=None, notes='hi')
        rn = gs.read_norm(ws.n.retrieve_page(r))
        self.assertEqual(rn['book'], b)
        self.assertTrue(rn['dates_estimated'])
        self.assertEqual(rn['edition'], ['e1'])
        self.assertTrue(rn['edition_inferred'])
        self.assertIsNone(rn['pages_read'])
        self.assertEqual(rn['notes'], 'hi')
        self.assertIsNone(rn['outcome'])


# ---------------------------------------------------------------- rules (step 1: log rows and Started)
class StartedRuleTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.book = self.ws.book('Lonesome Dove', 'Larry McMurtry')
        self.item = started_item('ReadStatus100', 'Sun, 04 Oct 2026 21:00:00 -0400', '56346987', 'Lonesome Dove')

    def test_started_fills_blank(self):
        r = self.ws.read('Lonesome Dove · 2026', self.book)
        self.ws.sync([self.item])
        up = self.ws.n.updates(r)
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0]['Started']['date']['start'], '2026-10-04')
        self.assertFalse(up[0]['Dates estimated']['checkbox'])
        self.assertIn('from Goodreads 2026-10-17', _plain(up[0]['Notes']['rich_text']))
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)

    def test_started_replaces_estimated(self):
        r = self.ws.read('Lonesome Dove · 2026', self.book, started='2026-10-01', estimated=True, notes='Reread.')
        self.ws.sync([self.item])
        up = self.ws.n.updates(r)
        self.assertEqual(up[0]['Started']['date']['start'], '2026-10-04')
        notes = _plain(up[0]['Notes']['rich_text'])
        self.assertTrue(notes.startswith('Reread.'))
        self.assertIn('estimated 2026-10-01', notes)
        self.assertIn('from Goodreads', notes)

    def test_started_confirmed_left_alone(self):
        r = self.ws.read('Lonesome Dove · 2026', self.book, started='2026-10-01', estimated=False)
        self.ws.sync([self.item])
        self.assertEqual(self.ws.n.updates(r), [])
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)  # the Started event is still logged


class LogRowTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.book = self.ws.book('Fundamentals of Software Architecture', 'Mark Richards')
        self.read = self.ws.read('Fundamentals of Software Architecture · 2026', self.book, started='2026-10-15')

    def test_progress_row(self):
        self.ws.sync([LATE_ITEM])
        rows = self.ws.n.creates(LOG_DS)
        self.assertEqual(len(rows), 1)
        p = rows[0]
        self.assertEqual(p['Read']['relation'][0]['id'], self.read)
        self.assertEqual((p['Page']['number'], p['Of pages']['number'], p['Percent']['number']), (63, 869, 7.2))
        self.assertEqual(p['Kind']['select']['name'], 'Progress')
        self.assertEqual(_plain(p['Goodreads id']['rich_text']), 'UserStatus9')
        self.assertTrue(_plain(p['Name']['title']).endswith('· 2026-10-16'))

    def test_rerun_is_idempotent(self):
        self.ws.sync([LATE_ITEM])
        self.ws.sync([LATE_ITEM])  # fresh state: dedupe must come from Notion, not state.json
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)

    def test_secondary_key_blocks_a_backfill_duplicate(self):
        # the same event is already in the log under a Backfill id: same Read, Kind, Eastern date and Page
        self.ws.log('Backfill:FSA:2026-10-16:p63', self.read, self.book, 'Progress', '2026-10-16', page=63)
        self.ws.sync([LATE_ITEM])
        self.assertEqual(self.ws.n.creates(LOG_DS), [])

    def test_secondary_key_needs_the_same_page(self):
        self.ws.log('Backfill:FSA:2026-10-16:p60', self.read, self.book, 'Progress', '2026-10-16', page=60)
        self.ws.sync([LATE_ITEM])
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)

    def test_dry_run_writes_nothing_but_checks_the_log(self):
        self.ws.log('UserStatus9', self.read, self.book, 'Progress', '2026-10-16T02:30:00-04:00', page=63)
        report, _ = self.ws.sync([LATE_ITEM], dry_run=True)
        self.assertEqual(self.ws.n.writes, [])  # no log row, no Sync run row
        self.assertEqual(report['written'], 0)
        self.assertEqual(report['already'], 1)

    def test_wishlisted_event_is_not_a_log_row(self):
        self.ws.book('Oroonoko', 'Aphra Behn')
        self.ws.sync([WTR_ITEM])
        self.assertEqual(self.ws.n.creates(LOG_DS), [])


class CacheTests(unittest.TestCase):
    def test_stale_book_cache_entry_dropped(self):
        ws = Workspace()
        b = ws.book('Lonesome Dove', 'Larry McMurtry')
        ws.read('Lonesome Dove · 2026', b)
        state = {'book_cache': {'56346987': 'trashed-after-a-merge'}, 'runs': []}
        ws.sync([SERIES_ITEM], state=state)
        self.assertEqual(state['book_cache'].get('56346987'), b)
        self.assertEqual(len(ws.n.creates(LOG_DS)), 1)


def shelf_item(t, author, added='Fri, 09 Oct 2026 19:04:19 -0700', read_at='', book_id='1', review='5001', pages='300'):
    it = rss_item(f'https://www.goodreads.com/review/show/{review}?utm_medium=api', added, t, '', link=f'https://www.goodreads.com/review/show/{review}')
    it.update({'author_name': author, 'user_date_added': added, 'user_read_at': read_at, 'book_id': book_id, 'num_pages': pages})
    return it


def sync_run_row(ws, asked, newest=None, oldest=None):
    return ws.question('Sync run', f'Sync run {asked}', 'run:' + asked, status='Done', asked=asked,
                       event={'at': asked, 'window': {'oldest': oldest, 'newest': newest, 'n': 9}})


class ReadingShelfTests(unittest.TestCase):
    """The currently-reading shelf, inside the fence."""

    def test_open_read_not_on_the_shelf_is_questioned(self):
        ws = Workspace()
        b1, b2 = ws.book('Lonesome Dove', 'Larry McMurtry'), ws.book('Middlemarch', 'George Eliot')
        ws.read('Lonesome Dove · 2026', b1, started='2026-10-04')
        r2 = ws.read('Middlemarch · 2026', b2, started='2026-10-01')
        report, _ = ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])
        qs = [(q['kind'], q['read']) for q in report['questions']]
        self.assertEqual(qs, [('Still reading', r2)])
        self.assertIn('Middlemarch · 2026', report['questions'][0]['question'])

    def test_read_opened_in_the_last_two_days_is_not_questioned(self):
        ws = Workspace()
        b2 = ws.book('Middlemarch', 'George Eliot')
        ws.read('Middlemarch · 2026', b2, started='2026-10-16')
        report, _ = ws.sync([], shelf=[])
        self.assertEqual(report['questions'], [])

    def test_shelf_item_with_no_open_read_is_questioned(self):
        ws = Workspace()
        b1 = ws.book('Lonesome Dove', 'Larry McMurtry')
        ws.read('Lonesome Dove · 2026', b1, started='2026-06-01', ended='2026-06-30', outcome='Finished', pages_read=858)
        report, _ = ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])
        q = report['questions'][0]
        self.assertEqual((q['kind'], q['book']), ('No open Read', b1))
        self.assertEqual(q['event']['kind'], 'started')
        self.assertEqual(q['event']['guid'], 'Shelf5001:started:2026-10-09')
        self.assertIn('Finished', q['proposed'])
        self.assertEqual(ws.n.creates(LOG_DS), [])

    def test_shelf_item_with_an_open_read_recovers_a_started_row(self):
        ws = Workspace()
        b1 = ws.book('Lonesome Dove', 'Larry McMurtry')
        r = ws.read('Lonesome Dove · 2026', b1)  # opened by chat, no Started
        ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])
        rows = ws.n.creates(LOG_DS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(_plain(rows[0]['Goodreads id']['rich_text']), 'Shelf5001:started:2026-10-09')
        self.assertEqual(rows[0]['Kind']['select']['name'], 'Started')
        up = ws.n.updates(r)[0]  # a shelf date is a guess: Started filled, Dates estimated stays ✓
        self.assertEqual(up['Started']['date']['start'], '2026-10-09')
        self.assertTrue(up['Dates estimated']['checkbox'])
        ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])  # night after night: no second row
        self.assertEqual(len(ws.n.creates(LOG_DS)), 1)

    def test_shelf_start_is_not_logged_when_the_read_already_has_a_started_row(self):
        ws = Workspace()
        b1 = ws.book('Lonesome Dove', 'Larry McMurtry')
        r = ws.read('Lonesome Dove · 2026 (reread)', b1, started='2026-10-04')
        ws.log('Backfill:LonesomeDove:2026-10-04:started', r, b1, 'Started', '2026-10-04')
        report, _ = ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])  # shelved 2026-10-09, the stray re-shelve
        self.assertEqual(ws.n.creates(LOG_DS), [])
        self.assertEqual(ws.n.updates(r), [])
        self.assertEqual(report['already'], 1)

    def test_shelf_date_never_replaces_a_feed_started(self):
        ws = Workspace()
        b1 = ws.book('Lonesome Dove', 'Larry McMurtry')
        r = ws.read('Lonesome Dove · 2026', b1, started='2026-10-04', estimated=True)
        ws.sync([], shelf=[shelf_item('Lonesome Dove', 'Larry McMurtry', book_id='56346987')])
        self.assertEqual(ws.n.updates(r), [])

    def test_unmatched_shelf_item_is_questioned(self):
        ws = Workspace()
        report, _ = ws.sync([], shelf=[shelf_item('A Book Notion Lacks', 'Nobody', book_id='7')])
        self.assertEqual(report['questions'][0]['kind'], 'Unmatched book')


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.book = self.ws.book('Fundamentals of Software Architecture', 'Mark Richards')
        self.read = self.ws.read('Fundamentals of Software Architecture · 2026', self.book, started='2026-10-15')

    def test_first_run_takes_the_whole_feed(self):
        report, _ = self.ws.sync([LATE_ITEM])
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)
        self.assertIsNone(report['watermark'])

    def test_events_before_the_watermark_are_ignored(self):
        sync_run_row(self.ws, '2026-10-20T23:45:00-04:00')  # watermark = 2026-10-18T23:45 Eastern
        report, _ = self.ws.sync([LATE_ITEM], now='2026-10-21T23:45:00-04:00')  # LATE_ITEM is 2026-10-16
        self.assertEqual(self.ws.n.creates(LOG_DS), [])
        self.assertEqual(report['watermark'], '2026-10-18T23:45:00-04:00')
        self.assertEqual(report['fenced_out'], 1)

    def test_events_inside_the_48h_margin_are_taken(self):
        sync_run_row(self.ws, '2026-10-17T23:45:00-04:00')  # watermark = 2026-10-15T23:45 Eastern; LATE_ITEM is 02:30 on the 16th
        self.ws.sync([LATE_ITEM], now='2026-10-18T23:45:00-04:00')
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)

    def test_since_overrides_the_watermark(self):
        sync_run_row(self.ws, '2026-10-20T23:45:00-04:00')
        self.ws.sync([LATE_ITEM], now='2026-10-21T23:45:00-04:00', since='2026-10-01')
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)

    def test_run_row_records_window_and_watermark(self):
        sync_run_row(self.ws, '2026-10-15T23:45:00-04:00', newest='2026-10-15T20:00:00-04:00')
        self.ws.sync([LATE_ITEM, FINISHED_ITEM], now='2026-10-16T23:45:00-04:00')
        run = [r for r in self.ws.queue_rows(kind='Sync run') if r['asked'].startswith('2026-10-16')][0]
        self.assertEqual(run['event']['window'], {'oldest': '2026-10-15T05:29:00-07:00', 'newest': '2026-10-15T23:30:00-07:00', 'n': 2})
        self.assertEqual(run['event']['watermark'], '2026-10-13T23:45:00-04:00')


class ReadShelfTests(unittest.TestCase):
    """The read and did-not-finish shelves recover a Finished / DNF the feed aged out, inside the fence only."""

    def setUp(self):
        self.ws = Workspace()
        self.book = self.ws.book('Release It!', 'Michael T. Nygard')
        sync_run_row(self.ws, '2026-10-14T23:45:00-04:00')  # watermark = 2026-10-12T23:45 Eastern
        self.now = '2026-10-16T23:45:00-04:00'

    def item(self, added='Thu, 15 Oct 2026 05:29:00 -0700', read_at='Thu, 15 Oct 2026 00:00:00 +0000', review='8984202464'):
        return shelf_item('Release It!: Design and Deploy Production-Ready Software', 'Michael T. Nygard', added=added, read_at=read_at, book_id='38733031', review=review)

    def test_item_before_the_watermark_is_never_compared(self):
        self.ws.read('Release It! · 2023', self.book, started='2023-01-01', ended='2023-02-01', outcome='Finished', pages_read=300)
        report, _ = self.ws.sync([], read_shelf=[self.item(added='Sat, 03 Oct 2026 05:29:00 -0700', read_at='Sat, 03 Oct 2026 00:00:00 +0000')], now=self.now)
        self.assertEqual(report['questions'], [])
        self.assertEqual(self.ws.n.creates(LOG_DS), [])

    def test_item_after_the_watermark_with_no_read_is_questioned(self):
        report, _ = self.ws.sync([], read_shelf=[self.item()], now=self.now)
        q = report['questions'][0]
        self.assertEqual((q['kind'], q['book']), ('Finish date', self.book))
        self.assertEqual(q['event']['guid'], 'Shelf8984202464:finished:2026-10-15')
        self.assertEqual(self.ws.n.creates(LOG_DS), [])

    def test_item_whose_book_has_an_open_read_logs_the_finish(self):
        r = self.ws.read('Release It! · 2026', self.book, started='2026-09-29')
        self.ws.sync([], read_shelf=[self.item()], now=self.now)
        rows = self.ws.n.creates(LOG_DS)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['Kind']['select']['name'], rows[0]['Read']['relation'][0]['id'], rows[0]['When']['date']['start']), ('Finished', r, '2026-10-15'))

    def test_item_agreeing_with_a_closed_read_attaches_quietly(self):
        r = self.ws.read('Release It! · 2026', self.book, started='2026-09-29', ended='2026-10-15', outcome='Finished', pages_read=568)
        report, _ = self.ws.sync([], read_shelf=[self.item()], now=self.now)
        self.assertEqual(report['questions'], [])
        rows = self.ws.n.creates(LOG_DS)
        self.assertEqual((len(rows), rows[0]['Read']['relation'][0]['id']), (1, r))
        self.assertEqual(self.ws.n.updates(r), [])  # never edits a closed Read

    def test_item_disagreeing_with_a_closed_read_is_questioned(self):
        r = self.ws.read('Release It! · 2026', self.book, started='2026-09-29', ended='2026-10-10', outcome='Finished', pages_read=568)
        report, _ = self.ws.sync([], read_shelf=[self.item()], now=self.now)
        q = report['questions'][0]
        self.assertEqual((q['kind'], q['read']), ('Finish date', r))
        self.assertIn('2026-10-10', q['proposed'])
        self.assertEqual(self.ws.n.creates(LOG_DS), [])
        self.assertEqual(self.ws.n.updates(r), [])

    def test_feed_finish_and_shelf_finish_are_one_row(self):
        self.ws.read('Release It! · 2026', self.book, started='2026-09-29')
        self.ws.sync([FINISHED_ITEM], read_shelf=[self.item()], now=self.now)
        rows = self.ws.n.creates(LOG_DS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(_plain(rows[0]['Goodreads id']['rich_text']), 'ReadStatus10')  # the feed's own event wins

    def test_dnf_shelf_item_logs_a_dnf(self):
        b = self.ws.book('The Cauldron: The Making of the Modern Middle East', 'Simon Sebag Montefiore')
        r = self.ws.read('The Cauldron · 2026', b, started='2026-10-10')
        it = shelf_item('The Cauldron: The Making of the Modern Middle East', 'Simon Sebag Montefiore', added='Fri, 16 Oct 2026 19:18:50 -0700', read_at='', book_id='241861256', review='8529215161')
        self.ws.sync([], dnf_shelf=[it], now=self.now)
        rows = self.ws.n.creates(LOG_DS)
        self.assertEqual((rows[0]['Kind']['select']['name'], rows[0]['Read']['relation'][0]['id'], rows[0]['When']['date']['start']), ('DNF', r, '2026-10-16'))
        self.assertEqual(_plain(rows[0]['Goodreads id']['rich_text']), 'Shelf8529215161:dnf:2026-10-16')


class GapTests(unittest.TestCase):
    def test_gap_when_oldest_item_is_newer_than_last_newest(self):
        ws = Workspace()
        sync_run_row(ws, '2026-10-14T23:45:00-04:00', newest='2026-10-14T20:00:00-04:00')
        report, _ = ws.sync([FINISHED_ITEM, LATE_ITEM], now='2026-10-16T23:45:00-04:00')  # oldest 2026-10-15 05:29 PT > 2026-10-14 20:00 ET
        gaps = [q for q in report['questions'] if q['kind'] == 'Gap']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['key'], 'gap:2026-10-14T20:00:00-04:00')
        self.assertTrue(gaps[0]['default_applied'])
        self.assertIn('2026-10-15', gaps[0]['question'])

    def test_no_gap_when_the_window_overlaps(self):
        ws = Workspace()
        sync_run_row(ws, '2026-10-15T23:45:00-04:00', newest='2026-10-15T20:00:00-04:00')
        report, _ = ws.sync([FINISHED_ITEM, LATE_ITEM], now='2026-10-16T23:45:00-04:00')
        self.assertEqual([q for q in report['questions'] if q['kind'] == 'Gap'], [])

    def test_no_gap_on_the_first_run(self):
        ws = Workspace()
        report, _ = ws.sync([FINISHED_ITEM, LATE_ITEM])
        self.assertEqual([q for q in report['questions'] if q['kind'] == 'Gap'], [])


class ArchiveTests(unittest.TestCase):
    def test_archive_appends_by_guid(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            n1 = gs.archive_items(Path(d), [PAGE_ITEM, PCT_ITEM], '2026-10-16')
            n2 = gs.archive_items(Path(d), [PCT_ITEM, NOTE_ITEM], '2026-10-17')
            self.assertEqual((n1, n2), (2, 1))
            lines = [json.loads(l) for f in sorted(Path(d).glob('*.jsonl')) for l in f.read_text().splitlines()]
            self.assertEqual([l['guid'] for l in lines], ['UserStatus1', 'UserStatus2', 'UserStatus3'])
            self.assertEqual(sorted(p.name for p in Path(d).glob('*.jsonl')), ['2026-10-16.jsonl', '2026-10-17.jsonl'])
            self.assertEqual(lines[0]['description'], PAGE_ITEM['description'])


# ---------------------------------------------------------------- backfill --apply dedupe
class BackfillApplyTests(unittest.TestCase):
    def test_backfill_row_blocks_timeline_duplicate(self):
        import backfill
        ws = Workspace()
        b = ws.book('Lonesome Dove', 'Larry McMurtry')
        r = ws.read('Lonesome Dove · 2026 (reread)', b, started='2026-10-04')
        ws.log('Backfill:LonesomeDove:2026-10-14:p161', r, b, 'Progress', '2026-10-14', page=161)
        ev = {'kind': 'progress', 'title': 'Lonesome Dove', 'author': None, 'gr_book_id': '56346987', 'guid': 'Timeline7521001057:9', 'at': '2026-10-14',
              'raw': 'October 14, 2026 – page 161 16.7%', 'note': None, 'page': 161, 'total': None, 'percent': 16.7}
        ev2 = dict(ev, guid='Timeline7521001057:10', at='2026-10-15', page=180, raw='October 15, 2026 – page 180')
        plan = {'log_rows': [{'read': r, 'read_title': 'x', 'props': gs.log_props(ev, {'id': b, 'title': 'Lonesome Dove'}, {'id': r})},
                             {'read': r, 'read_title': 'x', 'props': gs.log_props(ev2, {'id': b, 'title': 'Lonesome Dove'}, {'id': r})}],
                'started_updates': []}
        n = backfill.apply_plan(plan, ws.n, LOG_DS, done={'rows': [], 'started': []}, dry_run=False)
        self.assertEqual(n, 1)
        self.assertEqual([_plain(p['Goodreads id']['rich_text']) for p in ws.n.creates(LOG_DS)], ['Timeline7521001057:10'])
        # a second apply of the same plan writes nothing: the new row is now known by its Goodreads id
        self.assertEqual(backfill.apply_plan(plan, ws.n, LOG_DS, done={'rows': [], 'started': []}, dry_run=False), 0)

    def test_backfill_percent_one_decimal(self):
        import backfill
        evs = backfill.parse_rows(['October 4, 2026 – page 45 4.67%', 'October 5, 2026 – 12.345%'])
        self.assertEqual([e['percent'] for e in evs], [4.7, 12.3])



# ---------------------------------------------------------------- offline replay of the live fixtures
class OfflineReplayTests(unittest.TestCase):
    @unittest.skipUnless((FIX / 'live_updates.xml').exists() and (FIX / 'live_shelf_currently-reading_1.xml').exists(), 'live fixtures not present')
    def test_live_fixtures_against_raw_pull_match_expected_plan(self):
        raw = HERE.parent / 'data' / 'raw'
        lib = gs.Library(None)
        lib.load_raw(raw)
        items = gs.rss_items((FIX / 'live_updates.xml').read_bytes())
        shelf = gs.rss_items((FIX / 'live_shelf_currently-reading_1.xml').read_bytes())
        read_shelf = gs.rss_items((FIX / 'live_shelf_read_1.xml').read_bytes()) if (FIX / 'live_shelf_read_1.xml').exists() else []
        dnf_shelf = gs.rss_items((FIX / 'live_shelf_did-not-finish_1.xml').read_bytes()) if (FIX / 'live_shelf_did-not-finish_1.xml').exists() else []
        shelves = {'currently-reading': shelf, 'read': read_shelf, 'did-not-finish': dnf_shelf}
        report = gs.run_sync(None, items, shelves, {'book_cache': {}, 'runs': []}, offline_lib=lib, now='2026-10-17T09:00:00-04:00')
        expected = json.loads((FIX / 'expected_live.json').read_text(encoding='utf-8'))
        self.assertEqual(report['plan'], expected['plan'])
        self.assertEqual([(q['kind'], q['key']) for q in report['questions']], [tuple(x) for x in expected['questions']])


# ---------------------------------------------------------------- the Sync queue (step 2)
class QueueSchemaTests(unittest.TestCase):
    def test_schema_has_the_agreed_properties(self):
        props = gs.queue_properties()
        for name in ('Question', 'Kind', 'Book', 'Read', 'Options', 'Proposed', 'Default applied', 'Answer', 'Confirm', 'Status', 'Asked', 'Resolved', 'Key', 'Event', 'Goodreads id'):
            self.assertIn(name, props)
        self.assertEqual(props['Book']['relation']['data_source_id'], BOOKS_DS)
        self.assertEqual(props['Read']['relation']['data_source_id'], READS_DS)
        kinds = {o['name'] for o in props['Kind']['select']['options']}
        self.assertTrue({'Edition', 'Pages read', 'Reread or reopen', 'Unmatched book', 'No open Read', 'Still reading', 'Finish date', 'Gap', 'Sync run'} <= kinds)
        self.assertEqual([o['name'] for o in props['Status']['select']['options']], ['Open', 'Answered', 'Dismissed', 'Done'])

    def test_init_queue_creates_the_database_and_reports_its_id(self):
        n = FakeNotion()
        ds = gs.init_queue(n, 'parent-page')
        self.assertEqual(n.databases[0]['parent'], {'type': 'page_id', 'page_id': 'parent-page'})
        self.assertEqual(n.databases[0]['title'], 'Sync queue')
        self.assertTrue(ds)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.book = self.ws.book('Lonesome Dove', 'Larry McMurtry')
        self.read = self.ws.read('Lonesome Dove · 2026', self.book, started='2026-10-04')

    def test_unmatched_event_becomes_an_open_question(self):
        item = progress_item('UserStatus200', 'Mon, 12 Oct 2026 21:00:00 -0400', '999', 'A Title Notion Lacks', 10, 200)
        report, _ = self.ws.sync([item])
        rows = self.ws.queue_rows(kind='Unmatched book')
        self.assertEqual(len(rows), 1)
        q = rows[0]
        self.assertEqual((q['status'], q['guid'], q['default_applied']), ('Open', 'UserStatus200', False))
        self.assertEqual(q['event']['title'], 'A Title Notion Lacks')
        self.assertIn('A Title Notion Lacks', q['question'])
        self.assertEqual(q['asked'][:10], '2026-10-17')
        self.assertEqual(self.ws.n.creates(LOG_DS), [])
        self.assertEqual(len(report['open_questions']), 1)
        self.ws.sync([item])  # the same event again: one row, not two
        self.assertEqual(len(self.ws.queue_rows(kind='Unmatched book')), 1)

    def test_ambiguous_match_lists_the_candidates(self):
        self.ws.book('Stalin: Waiting for Hitler', 'Stephen Kotkin')
        self.ws.book('Stalin: Paradoxes of Power', 'Stephen Kotkin')
        item = progress_item('UserStatus201', 'Mon, 12 Oct 2026 21:00:00 -0400', '998', 'Stalin', 10, 900)
        self.ws.sync([item])
        q = self.ws.queue_rows(kind='Unmatched book')[0]
        self.assertIn('Stalin: Waiting for Hitler', q['options'])
        self.assertIn('Stalin: Paradoxes of Power', q['options'])

    def test_event_with_no_open_read_is_queued(self):
        b2 = self.ws.book('Middlemarch', 'George Eliot')
        self.ws.read('Middlemarch · 2024', b2, started='2024-01-01', ended='2024-02-01', outcome='Finished', pages_read=800)
        item = progress_item('UserStatus202', 'Mon, 12 Oct 2026 21:00:00 -0400', '19089', 'Middlemarch', 10, 900)
        self.ws.sync([item])
        q = self.ws.queue_rows(kind='No open Read')[0]
        self.assertEqual((q['status'], q['book']), ('Open', b2))
        self.assertEqual(self.ws.n.creates(LOG_DS), [])

    def test_pending_event_is_retried_and_lands_once_the_read_exists(self):
        ev = gs.parse_update(progress_item('UserStatus203', 'Mon, 12 Oct 2026 21:00:00 -0400', '56346987', 'Lonesome Dove', 10, 964))
        row = self.ws.question('No open Read', 'Progress on Lonesome Dove, but no open Read', 'event:UserStatus203', event=ev, guid='UserStatus203', book=self.book)
        self.ws.sync([])  # no feed items at all: the queue alone drives the write
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)
        q = gs.queue_norm(self.ws.n.retrieve_page(row))
        self.assertEqual(q['status'], 'Answered')
        self.assertTrue(q['answer'].startswith('auto:'))
        self.assertEqual(q['resolved'], '2026-10-17')

    def test_answer_naming_the_book_is_applied(self):
        ev = gs.parse_update(progress_item('UserStatus204', 'Mon, 12 Oct 2026 21:00:00 -0400', '777', 'Lonesome Dove Unabridged Special', 20, 964))
        row = self.ws.question('Unmatched book', 'Which Book?', 'event:UserStatus204', event=ev, guid='UserStatus204', answer='Lonesome Dove')
        _, state = self.ws.sync([])
        self.assertEqual(len(self.ws.n.creates(LOG_DS)), 1)
        q = gs.queue_norm(self.ws.n.retrieve_page(row))
        self.assertEqual(q['status'], 'Answered')
        self.assertEqual(state['book_cache'].get('777'), self.book)

    def test_answer_that_cannot_be_applied_stays_open(self):
        ev = gs.parse_update(progress_item('UserStatus205', 'Mon, 12 Oct 2026 21:00:00 -0400', '777', 'Nothing Like It', 20, 964))
        row = self.ws.question('Unmatched book', 'Which Book?', 'event:UserStatus205', event=ev, guid='UserStatus205', answer='Still Nothing')
        self.ws.sync([])
        q = gs.queue_norm(self.ws.n.retrieve_page(row))
        self.assertEqual(q['status'], 'Open')
        self.assertIn('could not apply', q['proposed'])

    def test_drop_answer_dismisses(self):
        ev = gs.parse_update(progress_item('UserStatus206', 'Mon, 12 Oct 2026 21:00:00 -0400', '777', 'Nothing Like It', 20, 964))
        row = self.ws.question('Unmatched book', 'Which Book?', 'event:UserStatus206', event=ev, guid='UserStatus206', answer='drop')
        self.ws.sync([])
        self.assertEqual(gs.queue_norm(self.ws.n.retrieve_page(row))['status'], 'Dismissed')
        self.assertEqual(self.ws.n.creates(LOG_DS), [])

    def test_confirm_marks_a_question_answered_with_the_default_kept(self):
        row = self.ws.question('Gap', 'Page updates between a and b may be missing', 'gap:a', confirm=True, default_applied=True, proposed='noted')
        self.ws.sync([])
        q = gs.queue_norm(self.ws.n.retrieve_page(row))
        self.assertEqual((q['status'], q['resolved']), ('Answered', '2026-10-17'))

    def test_unanswered_question_stays_open(self):
        row = self.ws.question('Gap', 'Page updates between a and b may be missing', 'gap:b', default_applied=True)
        report, _ = self.ws.sync([])
        self.assertEqual(gs.queue_norm(self.ws.n.retrieve_page(row))['status'], 'Open')
        self.assertEqual([q['key'] for q in report['open_questions']], ['gap:b'])

    def test_sync_run_row_is_the_watermark(self):
        self.assertIsNone(gs.SyncQueue(gs.Store(self.ws.n)).last_run())
        self.ws.sync([LATE_ITEM], now='2026-10-16T23:45:00-04:00')
        self.ws.sync([LATE_ITEM], now='2026-10-17T23:45:00-04:00')
        runs = self.ws.queue_rows(kind='Sync run')
        self.assertEqual(len(runs), 2)
        last = gs.SyncQueue(gs.Store(self.ws.n)).last_run()
        self.assertEqual(last['asked'], '2026-10-17T23:45:00-04:00')
        self.assertEqual(last['status'], 'Done')
        self.assertEqual(last['event']['new'], 1)

    def test_dry_run_records_no_run_and_asks_nothing(self):
        item = progress_item('UserStatus207', 'Mon, 12 Oct 2026 21:00:00 -0400', '999', 'A Title Notion Lacks', 10, 200)
        report, _ = self.ws.sync([item], dry_run=True)
        self.assertEqual(self.ws.n.writes, [])
        self.assertEqual(len(report['questions']), 1)  # the question is in the plan, not in Notion

    def test_offline_run_reports_questions_without_a_queue(self):
        lib = small_library()
        item = progress_item('UserStatus208', 'Mon, 12 Oct 2026 21:00:00 -0400', '999', 'A Title Notion Lacks', 10, 200)
        report = gs.run_sync(None, [item], {}, {'book_cache': {}, 'runs': []}, offline_lib=lib, now='2026-10-17T09:00:00-04:00')
        self.assertEqual(report['questions'][0]['kind'], 'Unmatched book')
        self.assertEqual(report['open_questions'], [])


if __name__ == '__main__':
    unittest.main()
