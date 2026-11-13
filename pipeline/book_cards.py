#!/usr/bin/env python3
"""Build the book-card snapshot for the "Reading this week" dashboard.

One row per Book that has at least one Read: title, authors, year, pages, form,
genres, lists and the editions of it (format, publisher, year, translator,
owned). Read history is NOT in here: the dashboard takes it live from Notion.

Input: data/raw/{books,editions,authors,reads}.json (pull_merge.py).
Output: data/out/ds/book_cards.json, attached to the dashboard as the
`book_cards` dataset. Rebuild and re-attach after a fresh pull.
"""
import json, os

R = 'data/raw'
OUT = 'data/out/ds/book_cards.json'


def load(name):
    return json.load(open(f'{R}/{name}.json'))


def pid(url):
    """Notion page URL -> 32-hex id."""
    return (url or '').rstrip('/').split('/')[-1].split('?')[0][-32:]


def rel(v):
    """A relation column (JSON text of page URLs) -> list of ids."""
    try:
        return [pid(u) for u in json.loads(v or '[]')]
    except ValueError:
        return []


def arr(v):
    try:
        return json.loads(v or '[]')
    except ValueError:
        return []


def main():
    books = load('books')
    editions = load('editions')
    authors = {pid(a['url']): a['Name'] for a in load('authors')}
    reads = load('reads')
    meta = json.load(open(f'{R}/meta.json'))

    read_books = {b for r in reads for b in rel(r['Book'])}
    eds_by_book = {}
    for e in editions:
        for b in rel(e['Book']):
            eds_by_book.setdefault(b, []).append(e)

    rows = []
    for b in books:
        bid = pid(b['url'])
        if bid not in read_books:
            continue
        eds = []
        for e in sorted(eds_by_book.get(bid, []), key=lambda e: (e['Format'] or '', e['Publisher'] or '')):
            eds.append({
                'id': pid(e['url']),
                'format': e['Format'],
                'binding': e['Binding'],
                'publisher': e['Publisher'],
                'year': int(e['Year']) if e['Year'] else None,
                'statement': e['Edition statement'],
                'translators': [authors.get(t, '') for t in rel(e['Translator']) if authors.get(t)],
                'pages': int(e['Pages']) if e['Pages'] else None,
                'owned': 1 if e['Owned'] == '__YES__' and not e['date:Disposed:start'] else 0,
                'disposition': e['Disposition'],
            })
        rows.append({
            'book_id': bid,
            'title': b['Title'],
            'authors': ', '.join(authors[a] for a in rel(b['Author']) if a in authors),
            'year': int(b['Original year']) if b['Original year'] is not None else None,
            'pages': int(b['Pages']) if b['Pages'] else None,
            'form': b['Form'],
            'fiction': b['Fiction'],
            'genres': ', '.join(g for g in arr(b['Genre']) if g not in ('Classic', 'Canonical work')),
            'canon': ', '.join(g for g in arr(b['Genre']) if g in ('Classic', 'Canonical work')),
            'lists': ', '.join(arr(b['Lists'])),
            'editions': json.dumps(eds, ensure_ascii=False),
            'as_of': meta['pulled'],
        })
    rows.sort(key=lambda r: r['title'] or '')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, 'w'), ensure_ascii=False)
    print(OUT, len(rows), 'books,', os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
