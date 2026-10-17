#!/usr/bin/env python3
"""Merge the Notion connector's page files into data/raw/*.json and write data/raw/meta.json.

The connector's SQL mode returns at most 100 rows per call and no shell can call it, so a
pull is: Claude runs the SELECT in docs/REFRESH.md for each database with OFFSET 0, 100,
200, ... until a page comes back short, saves each reply verbatim to
data/raw/pages/<db>_<offset>.json, then runs this script. It checks the pages, concatenates
them into data/raw/<db>.json (same serialisation as the original pull: json.dump defaults)
and records the pull date and row counts in data/raw/meta.json, which analyze.py reads as
its as-of date.

Usage: python3 pipeline/pull_merge.py [--pages DIR] [--pulled YYYY-MM-DD] [--keep-pages]
                                      [--expect reads=233,books=1064,...]

Each page file may be the connector's reply object ({"results": [...], "has_more": ...}) or
the bare row list. Checks, any of which fails the run (exit 1) without touching data/raw:
  - every database has at least one page and the offsets are 0, 100, 200, ... with no gap;
  - every page but the last has exactly 100 rows and the last has at least one;
  - every row has exactly the raw columns analyze.py reads (listed in COLUMNS below);
  - url values are unique and ascending across pages (the SELECT orders by url);
  - --expect counts, when given (from a SELECT COUNT(*) per database), match.
"""
import json, os, sys, glob, re, shutil
from datetime import date

RAW = 'data/raw'
PAGE = 100
DATABASES = {
    'authors':  'collection://f96682e9-8772-4493-abff-ec079148f1b2',
    'books':    'collection://21e3da73-9608-41f8-a857-bd226043a391',
    'editions': 'collection://d6b4c6a6-4099-4b89-b135-0fa50a918c57',
    'reads':    'collection://05439130-377c-4e0b-948e-7c1d7ffb065e',
}
# The raw columns analyze.py (and lists.py) read, in the order of the original pull.
COLUMNS = {
    'authors':  ['url', 'Name', 'Sort name', 'Roles', 'Born', 'Died', 'Nationality', 'userDefined:ID'],
    'books':    ['url', 'Title', 'Author', 'Original year', 'Pages', 'Genre', 'Form', 'Fiction', 'Lists',
                 'date:Wishlisted:start', 'Source', 'Genre auto', 'userDefined:ID'],
    'editions': ['url', 'Edition', 'Book', 'Translator', 'Publisher', 'Year', 'Format', 'Binding', 'Pages', 'Owned',
                 'date:Acquired:start', 'date:Disposed:start', 'Disposition', 'Edition statement', 'Source', 'userDefined:ID'],
    'reads':    ['url', 'Read', 'Book', 'Edition', 'date:Started:start', 'date:Ended:start', 'Outcome', 'Pages read',
                 'Format read', 'Dates estimated', 'Edition inferred', 'userDefined:ID'],
}

def sql(db):
    """The exact SELECT for one page of a database (OFFSET is the only thing that changes)."""
    cols = ', '.join('url' if c == 'url' else f'"{c}"' for c in COLUMNS[db])
    return f'SELECT {cols} FROM "{DATABASES[db]}" ORDER BY url LIMIT {PAGE} OFFSET {{offset}}'

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def rows_of(obj, path):
    if isinstance(obj, list): return obj
    if isinstance(obj, dict):
        for k in ('results', 'rows', 'data'):
            if isinstance(obj.get(k), list): return obj[k]
    sys.exit(f'{path}: expected a row list or an object with a "results" list')

def main():
    if '--print-sql' in sys.argv:
        for db in DATABASES: print(f'-- {db}\n{sql(db)}\n')
        return
    pages_dir = arg('--pages', f'{RAW}/pages')
    pulled = arg('--pulled', date.today().isoformat())
    date.fromisoformat(pulled)
    expect = {}
    for part in (arg('--expect', '') or '').split(','):
        if part: k, v = part.split('='); expect[k.strip()] = int(v)
    errors, merged, counts = [], {}, {}
    for db in DATABASES:
        files = glob.glob(f'{pages_dir}/{db}_*.json')
        offs = {}
        for f in files:
            m = re.search(rf'{db}_(\d+)\.json$', f)
            if m: offs[int(m.group(1))] = f
        if not offs:
            errors.append(f'{db}: no page files {pages_dir}/{db}_<offset>.json'); continue
        want = list(range(0, max(offs) + 1, PAGE))
        if sorted(offs) != want:
            errors.append(f'{db}: offsets {sorted(offs)} should be {want}'); continue
        rows = []
        for i, off in enumerate(want):
            page = rows_of(json.load(open(offs[off])), offs[off])
            last = i == len(want) - 1
            if not last and len(page) != PAGE:
                errors.append(f'{db}: page at offset {off} has {len(page)} rows, expected {PAGE} (only the last page may be short)')
            if last and not page:
                errors.append(f'{db}: last page at offset {off} is empty; drop it')
            if last and len(page) == PAGE:
                print(f'note: {db}: last page is full; make sure OFFSET {off + PAGE} returned no rows', file=sys.stderr)
            rows.extend(page)
        cols = COLUMNS[db]
        for r in rows:
            if list(r.keys()) != cols:
                missing = [c for c in cols if c not in r]; extra = [c for c in r if c not in cols]
                errors.append(f'{db}: row {r.get("url")} columns differ: missing {missing}, extra {extra}'); break
        urls = [r.get('url') for r in rows]
        if len(set(urls)) != len(urls):
            errors.append(f'{db}: {len(urls) - len(set(urls))} duplicate url(s); pages overlap or the data changed mid-pull')
        if urls != sorted(urls):
            errors.append(f'{db}: urls not ascending; every page must ORDER BY url')
        if db in expect and expect[db] != len(rows):
            errors.append(f'{db}: {len(rows)} rows merged but --expect says {expect[db]}')
        merged[db] = rows; counts[db] = len(rows)
    if errors:
        print('\n'.join('ERROR ' + e for e in errors), file=sys.stderr)
        sys.exit(1)
    for db, rows in merged.items():
        json.dump(rows, open(f'{RAW}/{db}.json', 'w'))  # same serialisation as the original pull
    meta = {'pulled': pulled,
            'source': f'Notion connector, SQL mode, {PAGE} rows per call, ORDER BY url; raw columns only, rows verbatim',
            'rows': counts, 'columns': COLUMNS, 'data_sources': DATABASES}
    json.dump(meta, open(f'{RAW}/meta.json', 'w'), indent=1)
    print('merged', counts, 'pulled', pulled)
    if '--keep-pages' not in sys.argv and os.path.isdir(pages_dir):
        shutil.rmtree(pages_dir); print('removed', pages_dir)

if __name__ == '__main__':
    main()
