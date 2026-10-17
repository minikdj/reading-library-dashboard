#!/usr/bin/env python3
"""Fold the analysis outputs into the dataset files the dashboard attaches."""
import json, os
O = 'data/out'; D = 'data/out/ds'
os.makedirs(D, exist_ok=True)
def L(n): return json.load(open(f'{O}/{n}.json'))
def W(n, rows): json.dump(rows, open(f'{D}/{n}.json', 'w'), ensure_ascii=False); print(n, len(rows))

W('headline', L('headline'))
W('by_year', L('by_year'))
W('by_month', L('by_month'))
W('genres_by_year', L('genres_by_year'))
W('genres_read', L('genres_read'))
W('eras_by_status', L('eras_by_status'))
W('centuries_read', L('centuries_read'))
W('top_authors', L('top_authors'))
W('top_translators', L('top_translators'))
W('reads_log', L('reads_log'))
W('forms_read', L('forms_read'))
other = [dict(kind='DNF', **r) for r in L('dnfs')] + [dict(kind='Reading now', title=r['title'], ended=None, pages_read=None, book_pages=None, started=r['started'], format=r['format']) for r in L('reading_now')]
W('other_reads', other)
copies = [dict(dimension='Format', key=r['format'], copies=r['copies']) for r in L('owned_by_format')] \
       + [dict(dimension='Binding', key=r['binding'], copies=r['copies']) for r in L('owned_by_binding')] \
       + [dict(dimension='Publisher', key=r['publisher'], copies=r['copies']) for r in L('owned_by_publisher')]
W('copies_by', copies)
W('shelf_status', L('shelf_status'))
W('kindle_by_quarter', L('kindle_by_quarter'))
W('kindle_read_by_year', L('kindle_read_by_year'))
backlog = [dict(dimension='Genre', pool=r['pool'], key=r['genre'], fiction=r['fiction'], books=r['books'], pages=r['pages']) for r in L('backlog_genres')] \
        + [dict(dimension='Era', pool=r['pool'], key=r['era'], books=r['books'], pages=r['pages']) for r in L('backlog_eras')] \
        + [dict(dimension='Length', pool=r['pool'], key=r['length'], books=r['books'], pages=r['pages']) for r in L('backlog_length')] \
        + [dict(dimension='Canon', pool=r['pool'], key=r['canon'], books=r['books'], pages=r['pages']) for r in L('backlog_canon')]
W('backlog', backlog)
