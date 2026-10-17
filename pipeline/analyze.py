#!/usr/bin/env python3
"""Compute Dan's reading-library stats from the local Notion pulls.
Writes tidy dataset files to /home/claude/out/ and prints sanity checks."""
import json, re, os, sys, math
from collections import Counter, defaultdict
from datetime import date, datetime

D = 'data/raw'
OUT = 'data/out'
os.makedirs(OUT, exist_ok=True)
TODAY = date(2026, 10, 16)

def load(n):
    return json.load(open(f'{D}/{n}.json'))

books = load('books'); editions = load('editions'); reads = load('reads'); authors = load('authors')

def pid(url):
    return url[23:] if url else None  # substr(url,24) in SQL is 1-based

def rel(v):
    if not v: return []
    if isinstance(v, list): return [pid(u) for u in v]
    try:
        arr = json.loads(v)
    except Exception:
        return []
    return [pid(u) for u in arr]

def ms(v):
    if not v: return []
    if isinstance(v, list): return v
    try: return json.loads(v)
    except Exception: return []

def yes(v): return v == '__YES__'
def num(v):
    if v is None or v == '': return None
    try: return float(v)
    except Exception: return None
def d(v):
    if not v: return None
    return date.fromisoformat(str(v)[:10])

B = {pid(b['url']): b for b in books}
E = {pid(e['url']): e for e in editions}
R = {pid(r['url']): r for r in reads}
A = {pid(a['url']): a for a in authors}

print('rows', len(B), len(E), len(R), len(A))

# ---- normalise editions
for eid, e in E.items():
    e['_books'] = rel(e.get('Book'))
    e['_translators'] = rel(e.get('Translator'))
    e['_owned_now'] = yes(e.get('Owned')) and not e.get('date:Disposed:start')
    e['_disposed'] = bool(e.get('date:Disposed:start'))
    e['_pages'] = num(e.get('Pages'))
    e['_acq'] = d(e.get('date:Acquired:start'))
    e['_year'] = num(e.get('Year'))

# ---- normalise books
for bid, b in B.items():
    b['_authors'] = rel(b.get('Author'))
    b['_genre'] = ms(b.get('Genre'))
    b['_lists'] = ms(b.get('Lists'))
    b['_pages'] = num(b.get('Pages'))
    b['_year'] = num(b.get('Original year'))
    b['_fiction'] = b.get('Fiction') or None
    b['_form'] = b.get('Form') or None
    b['_editions'] = []
    b['_reads'] = []
for eid, e in E.items():
    for bid in e['_books']:
        if bid in B: B[bid]['_editions'].append(eid)

# ---- normalise reads
for rid, r in R.items():
    r['_book'] = (rel(r.get('Book')) or [None])[0]
    r['_editions'] = rel(r.get('Edition'))
    r['_start'] = d(r.get('date:Started:start'))
    r['_end'] = d(r.get('date:Ended:start'))
    r['_outcome'] = r.get('Outcome') or None   # None = currently reading
    r['_pages_read'] = num(r.get('Pages read'))
    r['_est'] = yes(r.get('Dates estimated'))
    r['_inferred'] = yes(r.get('Edition inferred'))
    # format: edition's format else Format read
    fmt = None
    for eid in r['_editions']:
        if eid in E and E[eid].get('Format'): fmt = E[eid]['Format']; break
    r['_format'] = fmt or r.get('Format read') or None
    # pages counted
    pc = 0.0
    if r['_pages_read'] is not None:
        pc = r['_pages_read']
    elif r['_outcome'] == 'Finished':
        ep = [E[eid]['_pages'] for eid in r['_editions'] if eid in E and E[eid]['_pages']]
        if ep: pc = max(ep)
        elif r['_book'] and B.get(r['_book'], {}).get('_pages'): pc = B[r['_book']]['_pages']
    r['_pages_counted'] = pc if r['_outcome'] == 'Finished' else 0.0
    r['_dnf_pages'] = r['_pages_read'] if r['_outcome'] == 'DNF' else None
    if r['_book'] in B: B[r['_book']]['_reads'].append(rid)
    bk = B.get(r['_book'], {})
    r['_title'] = bk.get('Title') or r.get('Read')

# ---- book status
for bid, b in B.items():
    rs = [R[rid] for rid in b['_reads']]
    owned = any(E[eid]['_owned_now'] for eid in b['_editions'] if eid in E)
    disposed = any(E[eid]['_disposed'] for eid in b['_editions'] if eid in E)
    if any(r['_outcome'] is None for r in rs): st = 'Reading'
    elif any(r['_outcome'] == 'Finished' for r in rs): st = 'Read'
    elif any(r['_outcome'] == 'DNF' for r in rs): st = 'Abandoned'
    elif owned: st = 'Unread'
    elif disposed: st = 'Previously owned'
    else: st = 'Wishlist'
    b['_status'] = st
    b['_owned_now'] = owned
    b['_owned_formats'] = sorted({E[eid]['Format'] for eid in b['_editions'] if eid in E and E[eid]['_owned_now'] and E[eid].get('Format')})
    b['_wishlist'] = 'Goodreads WTR' in b['_lists']
    b['_times_read'] = sum(1 for r in rs if r['_outcome'] == 'Finished')
    b['_canon'] = 'Classic' if 'Classic' in b['_genre'] else ('Canonical work' if 'Canonical work' in b['_genre'] else None)
    b['_content_genres'] = [g for g in b['_genre'] if g not in ('Classic', 'Canonical work')]

# ======== SANITY CHECKS ========
fin = [r for r in R.values() if r['_outcome'] == 'Finished']
dnf = [r for r in R.values() if r['_outcome'] == 'DNF']
paused = [r for r in R.values() if r['_outcome'] == 'Paused']
openr = [r for r in R.values() if r['_outcome'] is None]
print('finished', len(fin), 'dnf', len(dnf), 'paused', len(paused), 'open', len(openr))
print('finished date range', min(r['_end'] for r in fin), max(r['_end'] for r in fin))
py = Counter(); by = Counter()
for r in fin:
    py[r['_end'].year] += r['_pages_counted']; by[r['_end'].year] += 1
print('pages by year', dict(sorted(py.items())))
print('reads by year', dict(sorted(by.items())))
print('2026 finished', by[2026])
own = [e for e in E.values() if e['_owned_now']]
print('owned now by format', Counter(e.get('Format') for e in own))
print('classic', sum(1 for b in B.values() if 'Classic' in b['_genre']), 'canonical', sum(1 for b in B.values() if 'Canonical work' in b['_genre']), 'no genre', sum(1 for b in B.values() if not b['_genre']))
print('books no pages', sum(1 for b in B.values() if not b['_pages']))
print('status', Counter(b['_status'] for b in B.values()))
print('fin reads with 0 pages counted', [(r['_title'], r['_end']) for r in fin if not r['_pages_counted']])
print('reads missing book', sum(1 for r in R.values() if r['_book'] not in B))
print('editions no book', sum(1 for e in E.values() if not e['_books']))
print('editions no format', sum(1 for e in E.values() if not e.get('Format')))

# ======== DATASETS ========
def write(name, rows):
    json.dump(rows, open(f'{OUT}/{name}.json', 'w'), indent=0, ensure_ascii=False)
    print('wrote', name, len(rows))

books_read_ids = {r['_book'] for r in fin}
# 1. per year
rows = []
for y in range(2021, 2027):
    fy = [r for r in fin if r['_end'].year == y]
    dy = [r for r in dnf if r['_end'] and r['_end'].year == y]
    distinct = len({r['_book'] for r in fy})
    rereads = sum(1 for r in fy if r['_book'] in {x['_book'] for x in fin if x['_end'] < r['_end']})
    days_in_scope = (date(y,12,31) - date(y,8,1)).days+1 if y == 2021 else ((TODAY - date(y,1,1)).days+1 if y == 2026 else (366 if y % 4 == 0 else 365))
    rows.append(dict(year=y, reads=len(fy), books=distinct, pages=int(sum(r['_pages_counted'] for r in fy)), rereads=rereads, dnf=len(dy),
                     pages_per_day=round(sum(r['_pages_counted'] for r in fy)/days_in_scope, 1), days=days_in_scope,
                     note='Aug–Dec only' if y == 2021 else ('to Oct 16' if y == 2026 else ''),
                     fiction=sum(1 for r in fy if B.get(r['_book'],{}).get('_fiction') == 'Fiction'),
                     nonfiction=sum(1 for r in fy if B.get(r['_book'],{}).get('_fiction') == 'Non-fiction'),
                     fiction_unknown=sum(1 for r in fy if not B.get(r['_book'],{}).get('_fiction')),
                     paper=sum(1 for r in fy if r['_format']=='Paper'), kindle=sum(1 for r in fy if r['_format']=='Kindle'),
                     audio=sum(1 for r in fy if r['_format']=='Audio'), format_unknown=sum(1 for r in fy if not r['_format']),
                     classic=sum(1 for r in fy if B.get(r['_book'],{}).get('_canon')=='Classic'),
                     canonical=sum(1 for r in fy if B.get(r['_book'],{}).get('_canon')=='Canonical work'),
                     other=sum(1 for r in fy if not B.get(r['_book'],{}).get('_canon'))))
write('by_year', rows)

# 2. per month
rows = []
mstart = date(2021, 8, 1)
y, m = mstart.year, mstart.month
while (y, m) <= (TODAY.year, TODAY.month):
    fm = [r for r in fin if (r['_end'].year, r['_end'].month) == (y, m)]
    rows.append(dict(month=f'{y}-{m:02d}', reads=len(fm), pages=int(sum(r['_pages_counted'] for r in fm))))
    m += 1
    if m == 13: y, m = y+1, 1
write('by_month', rows)

# 3. all finished reads (event log) + reading speed
rows = []
for r in sorted(fin, key=lambda r: r['_end']):
    bk = B.get(r['_book'], {})
    days = (r['_end'] - r['_start']).days + 1 if r['_start'] else None
    rows.append(dict(title=r['_title'], author=', '.join(A[a]['Name'] for a in bk.get('_authors', []) if a in A),
                     started=str(r['_start']) if r['_start'] else None, ended=str(r['_end']), year=r['_end'].year,
                     pages=int(r['_pages_counted']), days=days, dates_estimated=r['_est'],
                     pages_per_day=round(r['_pages_counted']/days, 1) if days and not r['_est'] and r['_pages_counted'] else None,
                     format=r['_format'], fiction=bk.get('_fiction'), form=bk.get('_form'), canon=bk.get('_canon'),
                     genres=', '.join(bk.get('_content_genres', [])), original_year=bk.get('_year'),
                     translator=', '.join(sorted({A[t]['Name'] for eid in r['_editions'] if eid in E for t in E[eid]['_translators'] if t in A})),
                     reread=sum(1 for x in fin if x['_book'] == r['_book'] and x['_end'] < r['_end']) > 0))
write('reads_log', rows)
print('speed-eligible reads', sum(1 for x in rows if x['pages_per_day']), 'estimated', sum(1 for x in rows if x['dates_estimated']))

# 4. genres over time (content genres, per finished read; a read with 2 genres counts in both), split by Fiction
gc = Counter(); gy = defaultdict(Counter)
def fic(b): return b.get('_fiction') or 'Unknown'
for r in fin:
    bk = B.get(r['_book'], {})
    for g in bk.get('_content_genres', []):
        gc[(g, fic(bk))] += 1; gy[r['_end'].year][(g, fic(bk))] += 1
rows = [dict(genre=g, fiction=f, year=y, reads=gy[y][(g, f)]) for y in sorted(gy) for (g, f) in gc if gy[y][(g, f)]]
write('genres_by_year', rows)
rows = [dict(genre=g, fiction=f, reads=n,
             books=len({r['_book'] for r in fin if g in B.get(r['_book'],{}).get('_content_genres',[]) and fic(B[r['_book']]) == f}),
             pages=int(sum(r['_pages_counted'] for r in fin if g in B.get(r['_book'],{}).get('_content_genres',[]) and fic(B[r['_book']]) == f)))
        for (g, f), n in gc.most_common()]
write('genres_read', rows)
print('finished reads with no content genre', sum(1 for r in fin if not B.get(r['_book'],{}).get('_content_genres')))
print('genres appearing under both fiction and non-fiction', sorted({g for (g,f) in gc if (g,'Fiction') in gc and (g,'Non-fiction') in gc}))

# 5. eras / centuries of what was read (distinct books) — and of the whole library by status
def era(yr):
    if yr is None: return None
    if yr < 500: return 'Antiquity (to 500)'
    if yr < 1500: return 'Medieval (500–1499)'
    if yr < 1800: return '1500–1799'
    if yr < 1900: return '1800s'
    if yr < 1950: return '1900–1949'
    if yr < 2000: return '1950–1999'
    return '2000s'
ERAS = ['Antiquity (to 500)','Medieval (500–1499)','1500–1799','1800s','1900–1949','1950–1999','2000s']
def century(yr):
    """Return (sort key, label) for the century of a year."""
    if yr is None: return None
    if yr < 0:
        c = int(math.ceil(-yr / 100))  # 1..n centuries BCE
        return (-c, f'{c}00s BCE' if c > 1 else '1–100 BCE')
    c = int(yr // 100)
    return (c, f'{c}00s' if c > 0 else '1–99 CE')
rows = []
for st in ['Read', 'Unread', 'Wishlist']:
    pool = [b for b in B.values() if (b['_status'] == st) or (st == 'Wishlist' and b['_wishlist'] and b['_status'] in ('Wishlist',))]
    pool = [b for b in B.values() if b['_status'] == st]
    cnt = Counter(era(b['_year']) for b in pool)
    for e in ERAS + [None]:
        rows.append(dict(status=st, era=e or 'No year', books=cnt.get(e, 0)))
write('eras_by_status', rows)
rows = []
cc = Counter(century(b['_year']) for b in B.values() if b['_status'] == 'Read' and b['_year'] is not None)
for (k, label) in sorted(cc):
    rows.append(dict(century=label, books=cc[(k, label)], order=k))
write('centuries_read', rows)
print('read books no year', sum(1 for b in B.values() if b['_status']=='Read' and b['_year'] is None))

# 6. most-read authors (by finished reads and distinct books)
ac = Counter(); ab = defaultdict(set); ap = Counter()
for r in fin:
    for a in B.get(r['_book'], {}).get('_authors', []):
        ac[a] += 1; ab[a].add(r['_book']); ap[a] += r['_pages_counted']
rows = [dict(author=A[a]['Name'] if a in A else a, reads=n, books=len(ab[a]), pages=int(ap[a])) for a, n in ac.most_common(25)]
write('top_authors', rows)

# 7. translators (finished reads whose linked edition has a translator)
tc = Counter(); tb = defaultdict(set)
for r in fin:
    ts = {t for eid in r['_editions'] if eid in E for t in E[eid]['_translators']}
    for t in sorted(ts): tc[t] += 1; tb[t].add(r['_book'])  # sorted: set order varies per process
rows = [dict(translator=A[t]['Name'] if t in A else t, reads=n, books=len(tb[t])) for t, n in tc.most_common(20)]
write('top_translators', rows)
print('finished reads with translator', sum(1 for r in fin if any(E[eid]['_translators'] for eid in r['_editions'] if eid in E)), 'with any edition', sum(1 for r in fin if r['_editions']))

# 8. longest reads
rows = sorted([dict(title=r['_title'], author=', '.join(A[a]['Name'] for a in B.get(r['_book'],{}).get('_authors',[]) if a in A), pages=int(r['_pages_counted']), year=r['_end'].year) for r in fin], key=lambda x: -x['pages'])[:15]
write('longest_reads', rows)

# 9. rereads list
rr = defaultdict(list)
for r in fin: rr[r['_book']].append(r['_end'].year)
rows = [dict(title=B[b]['Title'], times=len(ys), years=', '.join(str(y) for y in sorted(ys))) for b, ys in rr.items() if len(ys) > 1]
rows.sort(key=lambda x: (-x['times'], x['title']))
write('rereads', rows)

# 10. DNFs
rows = [dict(title=r['_title'], ended=str(r['_end']) if r['_end'] else None, pages_read=int(r['_pages_read'] or 0), book_pages=int(B.get(r['_book'],{}).get('_pages') or 0)) for r in dnf]
write('dnfs', rows)
rows = [dict(title=r['_title'], started=str(r['_start']) if r['_start'] else None, format=r['_format']) for r in openr]
write('reading_now', rows)

# 11. collection: owned copies by format / binding / publisher
rows = [dict(format=f, copies=n) for f, n in Counter(e.get('Format') or 'Unknown' for e in own).most_common()]
write('owned_by_format', rows)
paper = [e for e in own if e.get('Format') == 'Paper']
rows = [dict(binding=b or 'Not recorded', copies=n) for b, n in Counter(e.get('Binding') for e in paper).most_common()]
write('owned_by_binding', rows)
pubc = Counter((e.get('Publisher') or '').strip() or 'Not recorded' for e in own)
rows = [dict(publisher=p, copies=n) for p, n in pubc.most_common(20)]
write('owned_by_publisher', rows)
print('owned copies no publisher', pubc.get('Not recorded', 0), 'distinct publishers', len(pubc))

# 12. shelf status: books with an owned copy, by status and format
owned_books = [b for b in B.values() if b['_owned_now']]
rows = []
for fmt in ['Paper', 'Kindle', 'Both']:
    pool = [b for b in owned_books if (fmt == 'Both' and len(b['_owned_formats']) > 1) or (fmt != 'Both' and b['_owned_formats'] == [fmt])]
    for st in ['Read', 'Reading', 'Abandoned', 'Unread']:
        ps = [b for b in pool if b['_status'] == st]
        rows.append(dict(format=fmt, status=st, books=len(ps), pages=int(sum(b['_pages'] or 0 for b in ps)), books_no_pages=sum(1 for b in ps if not b['_pages'])))
write('shelf_status', rows)
print('owned books', len(owned_books), Counter(b['_status'] for b in owned_books))

# 13. kindle acquisitions over time
kin = [e for e in E.values() if e.get('Format') == 'Kindle' and e['_acq']]
print('kindle editions', sum(1 for e in E.values() if e.get('Format')=='Kindle'), 'with acquired', len(kin), 'paper with acquired', sum(1 for e in E.values() if e.get('Format')=='Paper' and e['_acq']))
kc = Counter((e['_acq'].year, (e['_acq'].month-1)//3+1) for e in kin)
ky = Counter(e['_acq'].year for e in kin)
rows = [dict(year=y, copies=ky[y]) for y in sorted(ky)]
write('kindle_by_year', rows)
rows = [dict(quarter=f'{y} Q{q}', copies=kc[(y,q)]) for (y,q) in sorted(kc)]
write('kindle_by_quarter', rows)
# were the kindles read?
rows = []
for y in sorted(ky):
    es = [e for e in kin if e['_acq'].year == y]
    readn = sum(1 for e in es if any(B[b]['_status'] in ('Read','Reading') for b in e['_books'] if b in B))
    rows.append(dict(year=y, copies=len(es), read=readn, unread=len(es)-readn))
write('kindle_read_by_year', rows)

# 14. unread shelf & wishlist breakdowns
unread = [b for b in B.values() if b['_status'] == 'Unread']
wish = [b for b in B.values() if b['_status'] == 'Wishlist']
def breakdown(pool, key, order=None):
    c = Counter(); p = Counter()
    for b in pool:
        for k in key(b): c[k] += 1; p[k] += (b['_pages'] or 0)
    ks = order or [k for k, _ in c.most_common()]
    return [(k, c[k], int(p[k])) for k in ks if c.get(k)]
rows = []
for name, pool in [('Unread shelf', unread), ('Wishlist', wish)]:
    for f in ['Fiction', 'Non-fiction', 'Unknown']:
        for g, n, pg in breakdown([b for b in pool if fic(b) == f], lambda b: b['_content_genres'] or ['No genre']):
            rows.append(dict(pool=name, fiction=f, genre=g, books=n, pages=pg))
write('backlog_genres', rows)
rows = []
for name, pool in [('Unread shelf', unread), ('Wishlist', wish)]:
    for e, n, pg in breakdown(pool, lambda b: [era(b['_year']) or 'No year'], ERAS + ['No year']):
        rows.append(dict(pool=name, era=e, books=n, pages=pg))
write('backlog_eras', rows)
def lenbin(p):
    if p is None: return 'No page count'
    if p < 200: return 'Under 200'
    if p < 400: return '200–399'
    if p < 600: return '400–599'
    if p < 900: return '600–899'
    return '900+'
LB = ['Under 200','200–399','400–599','600–899','900+','No page count']
rows = []
for name, pool in [('Unread shelf', unread), ('Wishlist', wish)]:
    for l, n, pg in breakdown(pool, lambda b: [lenbin(b['_pages'])], LB):
        rows.append(dict(pool=name, length=l, books=n, pages=pg))
write('backlog_length', rows)
rows = []
for name, pool in [('Unread shelf', unread), ('Wishlist', wish)]:
    for c, n, pg in breakdown(pool, lambda b: [b['_canon'] or 'Not canon'], ['Classic','Canonical work','Not canon']):
        rows.append(dict(pool=name, canon=c, books=n, pages=pg))
write('backlog_canon', rows)

# 15. headline numbers
days_tracked = (TODAY - date(2021, 8, 1)).days + 1
pages_total = int(sum(r['_pages_counted'] for r in fin))
last365 = [r for r in fin if (TODAY - r['_end']).days < 365]
pace_365_pages = sum(r['_pages_counted'] for r in last365) / 365
pace_365_books = len(last365) / 365
unread_pages = int(sum(b['_pages'] or 0 for b in unread)); unread_np = sum(1 for b in unread if not b['_pages'])
wish_pages = int(sum(b['_pages'] or 0 for b in wish)); wish_np = sum(1 for b in wish if not b['_pages'])
med_unread = sorted(b['_pages'] for b in unread if b['_pages'])
med_unread = med_unread[len(med_unread)//2] if med_unread else 0
med_wish = sorted(b['_pages'] for b in wish if b['_pages']); med_wish = med_wish[len(med_wish)//2] if med_wish else 0
unread_est_pages = unread_pages + unread_np * med_unread
acq_top = Counter(e['_acq'] for e in kin).most_common(1); acq_top = acq_top[0] if acq_top else None
unread_np_by = {f: sum(1 for b in unread if not b['_pages'] and ((f == 'Both' and len(b['_owned_formats']) > 1) or (f != 'Both' and b['_owned_formats'] == [f]))) for f in ('Paper', 'Kindle', 'Both')}
wish_est_pages = wish_pages + wish_np * med_wish
head = dict(
    books_read=len(books_read_ids), reads_finished=len(fin), pages_read=pages_total, dnf=len(dnf), reading_now=len(openr),
    rereads=len(fin) - len(books_read_ids), first_read=str(min(r['_end'] for r in fin)), last_read=str(max(r['_end'] for r in fin)),
    days_tracked=days_tracked, pages_per_day_all=round(pages_total / days_tracked, 1), books_per_year_all=round(len(fin) / days_tracked * 365.25, 1),
    pages_per_day_365=round(pace_365_pages, 1), books_per_year_365=round(pace_365_books * 365, 1), reads_last_365=len(last365), pages_last_365=int(sum(r['_pages_counted'] for r in last365)),
    books_total=len(B), owned_copies=len(own), owned_paper=sum(1 for e in own if e.get('Format')=='Paper'), owned_kindle=sum(1 for e in own if e.get('Format')=='Kindle'), owned_audio=sum(1 for e in own if e.get('Format')=='Audio'),
    owned_books=len(owned_books), owned_read=sum(1 for b in owned_books if b['_status'] in ('Read',)), owned_reading=sum(1 for b in owned_books if b['_status']=='Reading'), owned_abandoned=sum(1 for b in owned_books if b['_status']=='Abandoned'), owned_unread=len(unread),
    shelf_read_share=round(sum(1 for b in owned_books if b['_status'] in ('Read','Reading')) / len(owned_books), 3),
    unread_pages=unread_pages, unread_no_pages=unread_np, unread_est_pages=unread_est_pages, unread_years_at_pace=round(unread_est_pages / (pace_365_pages*365), 1),
    wishlist_books=len(wish), wishlist_pages=wish_pages, wishlist_no_pages=wish_np, wishlist_est_pages=wish_est_pages, wishlist_years_at_pace=round(wish_est_pages / (pace_365_pages*365), 1),
    backlog_books=len(unread)+len(wish), backlog_years_at_pace=round((unread_est_pages+wish_est_pages) / (pace_365_pages*365), 1),
    previously_owned=sum(1 for b in B.values() if b['_status']=='Previously owned'), abandoned=sum(1 for b in B.values() if b['_status']=='Abandoned'),
    books_no_pages=sum(1 for b in B.values() if not b['_pages']), books_no_year=sum(1 for b in B.values() if b['_year'] is None), books_no_genre=sum(1 for b in B.values() if not b['_genre']),
    books_no_fiction=sum(1 for b in B.values() if not b['_fiction']), classic=sum(1 for b in B.values() if b['_canon']=='Classic'), canonical=sum(1 for b in B.values() if b['_canon']=='Canonical work'),
    read_classic=sum(1 for b in B.values() if b['_status']=='Read' and b['_canon']=='Classic'), read_canonical=sum(1 for b in B.values() if b['_status']=='Read' and b['_canon']=='Canonical work'),
    read_fiction=sum(1 for b in B.values() if b['_status']=='Read' and b['_fiction']=='Fiction'), read_nonfiction=sum(1 for b in B.values() if b['_status']=='Read' and b['_fiction']=='Non-fiction'),
    reads_dates_estimated=sum(1 for r in fin if r['_est']), reads_with_speed=sum(1 for r in fin if r['_start'] and not r['_est'] and r['_pages_counted']),
    paper_binding_recorded=sum(1 for e in paper if e.get('Binding')), paper_copies=len(paper),
    genre_auto=sum(1 for b in B.values() if yes(b.get('Genre auto'))),
    median_unread_pages=med_unread, median_wish_pages=med_wish,
    shelf_read_pct=round(100*sum(1 for b in owned_books if b['_status'] in ('Read','Reading')) / len(owned_books)),
    # caption facts (the page quotes these so no number lives in markup)
    reads_pages_read_filled=sum(1 for r in fin if r['_pages_read'] is not None),
    reads_no_edition=sum(1 for r in fin if not r['_editions']),
    reads_with_translator=sum(1 for r in fin if any(E[eid]['_translators'] for eid in r['_editions'] if eid in E)),
    reads_audio=sum(1 for r in fin if r['_format'] == 'Audio'),
    disposed_copies=sum(1 for e in E.values() if e['_disposed']),
    publishers_distinct=sum(1 for p in pubc if p != 'Not recorded'), copies_no_publisher=pubc.get('Not recorded', 0),
    kindle_copies=sum(1 for e in E.values() if e.get('Format') == 'Kindle'), kindle_with_acquired=len(kin),
    kindle_acquired_top_date=str(acq_top[0]) if acq_top else None, kindle_acquired_top_count=acq_top[1] if acq_top else 0,
    unread_no_pages_paper=unread_np_by['Paper'], unread_no_pages_kindle=unread_np_by['Kindle'], unread_no_pages_both=unread_np_by['Both'],
    backlog_no_fiction=sum(1 for b in unread + wish if not b['_fiction']),
    tracking_from=str(date(2021, 8, 1)),
    as_of=str(TODAY),
)
write('headline', [head])
for k, v in head.items(): print(f'  {k}: {v}')

# median speed
sp = sorted(x['pages_per_day'] for x in json.load(open(f'{OUT}/reads_log.json')) if x['pages_per_day'])
print('median pages/day per read (speed-eligible)', sp[len(sp)//2] if sp else None, 'n', len(sp))
# fiction / nonfiction totals among finished reads
print('fiction reads', sum(1 for r in fin if B.get(r['_book'],{}).get('_fiction')=='Fiction'), 'nonfiction', sum(1 for r in fin if B.get(r['_book'],{}).get('_fiction')=='Non-fiction'), 'unknown', sum(1 for r in fin if not B.get(r['_book'],{}).get('_fiction')))
print('format of reads', Counter(r['_format'] for r in fin))
# form of read books
rows = [dict(form=f or 'No form', books=n) for f, n in sorted(Counter(B[b]['_form'] for b in books_read_ids if b in B).items(), key=lambda kv: (-kv[1], kv[0]))]  # deterministic tie order
write('forms_read', rows)
