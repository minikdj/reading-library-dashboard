#!/usr/bin/env python3
"""Match Steve's two lists against Books, and find possible duplicate works."""
import json, re, unicodedata, difflib, sys
from collections import defaultdict

B = json.load(open('data/raw/books.json'))
A = {a['url'][23:]: a for a in json.load(open('data/raw/authors.json'))}
R = json.load(open('data/raw/reads.json'))
E = json.load(open('data/raw/editions.json'))
read_ids = {json.loads(r['Book'])[0][23:] for r in R if r.get('Outcome') == 'Finished'}
owned_ids = {b[23:] for e in E if e.get('Owned') == '__YES__' and not e.get('date:Disposed:start') for b in json.loads(e['Book'] or '[]')}

STOP = {'the', 'a', 'an', 'of', 'and', 'in', 'to', 'on', 'de', 'la', 'le', 'les', 'der', 'die', 'das', 'other', 'poems', 'selected', 'complete', 'works', 'plays', 'edition', 'penguin', 'classics'}
def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = s.split(':')[0].split(' - ')[0]
    s = s.replace('&', ' and ').replace("'s", 's').replace("’s", 's')
    s = re.sub(r"[^a-z0-9 ]", ' ', s)
    toks = [t for t in s.split() if t not in STOP]
    return ' '.join(toks)
def authors_of(b):
    return [A[a]['Name'] for a in [u[23:] for u in json.loads(b.get('Author') or '[]')] if a in A]
def surname(name):
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    toks = re.sub(r'[^a-z ]', ' ', name).split()
    return toks[-1] if toks else ''

for b in B:
    b['_n'] = norm(b['Title']); b['_auth'] = authors_of(b); b['_sur'] = {surname(a) for a in b['_auth']}
    b['_id'] = b['url'][23:]
    b['_st'] = 'Read' if b['_id'] in read_ids else ('Owned' if b['_id'] in owned_ids else '')

steve100 = """Heart of Darkness - Joseph Conrad
The last of the just - Andre Schwartz-Bart
Eichmann in Jerusalem - Hannah Arendt
The scarlet letter - Nathaniel Hawthorne
The history of the conquest of Mexico - William H. Prescott
The water margin - shi naian
Germinal - Zola
North and south - Elizabeth Gaskell
The annals of imperial Rome - Tacitus
the elder Edda
The life of Samuel Johnson - James Boswell
Paradise lost - John Milton
The nature of things - Lucretius
The history of the Peloponnesian war - Thucydides
The once and future king - T.H. White
Leaves of Grass - Walt Whitman
Black Lamb and grey falcon - Rebecca West
The prince - niccolo machiavelli
On The origin of species - Charles Darwin
The demons - heimito von doderer
Decameron - Giovanni Boccaccio
Lyrical ballads - William Wordsworth
Shakespeare's first folio - William Shakespeare
The consolation of philosophy - boethius
Orlando furioso - Ariosto
The velveteen rabbit - Margery Williams
Confessions - saint augustine
Ring of bright water - Gavin Maxwell
The story of the stone - cao xueqin
Roughing it - mark twain
The way we live now - Anthony Trollope
Vindication of the Rights of Woman - Mary Wollstonecraft
Candide - Voltaire
The history of the kings of Britain - Geoffrey of Monmouth
Republic - Plato
Animal Liberation - Peter singer
The secret history - procopius
The demon-haunted world - Carl sagan
Oedipus Rex - Sophocles
Huckleberry Finn - mark twain
The Metamorphosis - Franz Kafka
Don Quixote - Miguel de Cervantes
The Poems of Catullus - Catullus
Faust - Goethe
The Scholars - Wu Jingzi
Selected Poems - Tu Fu
Antigone - Sophocles
The Tale of the Heike
Krieg - Ludwig Renn
Duino Elegies - Rainer Maria Rilke
The history of the Franks - Gregory of tours
The anatomy of melancholy - Robert Burton
The conference of the birds - farid attar
The narrow road to the deep north - Basho
The Heptameron - Marguerite de Navarre
The outermost house - Henry beston
Njal's saga
The Arabian nights
Eugene onegin - Alexander Pushkin
The pillow book - sei shonagon
The natural history of selborne - Gilbert white
Vanity fair - William makepeace Thackeray
The golden ass - Apuleius
The autobiography of benvenuto Cellini - Cellini
The satires of juvenal - Juvenal
Poems of wang wei - Wang Wei
Chronicles - froissart
The dirty dust - mairtin o cadhain
Middlemarch - George Eliot
Wuthering heights - Emily Bronte
Pride and prejudice - Jane Austen
Our town - thornton wilder
Gulag archipelago - aleksandr solzhenitsyn
Brideshead revisited - Evelyn Waugh
The age of reason - Thomas Paine
Crime and punishment - fyodor Dostoevsky
Walden - Henry David thoreau
The education of Henry adams - Henry Adams
Hiroshima - John Hersey
The Ramayana - valmiki
Shahnameh - abolqasem ferdowsi
Seven pillars of wisdom - t.e. Lawrence
A room of one's own - Virginia Woolf
The oresteia - Aeschylus
The tale of genji - murasaki shikibu
The odyssey - Homer
The Canterbury tales - Geoffrey Chaucer
War and peace - Leo Tolstoy
The divine comedy - Dante
The Iliad - Homer"""

canon = """The Bible
The Iliad - Homer
The odyssey - Homer
Theogony - Hesiod
Works and Days - Hesiod
The Histories - Herodotus
History of the Peloponnesian war - Thucydides
The Oresteia - Aeschylus
Theban Plays - Sophocles
Medea and other plays - Euripides
Lysistrata - Aristophanes
Crito - Plato
Euthyphro - Plato
Republic - Plato
Symposium - Plato
Apology - Plato
Poetics - Aristotle
Nicomachean ethics - Aristotle
Plautus - Plautus
Terence - Terence
Xenophon - Xenophon
The Rise of the Roman Empire - Polybius
The early history of Rome - Livy
Rome and Italy - Livy
The war with Hannibal - Livy
Rome and the Mediterranean - Livy
The Annals - Tacitus
The histories - Tacitus
The Parallel Lives - Plutarch
The Nature of Things - Lucretius
The twelve Caesars - Suetonius
Cicero - Cicero
The Landmark Julius Caesar - Caesar
The Sixteen Satires - Juvenal
The Poems - Catullus
The Complete Odes and Epodes - Horace
The Aeneid - Virgil
Metamorphoses - Ovid
The history of the decline and fall of the Roman Empire - Edward gibbon
Confessions - St. Augustine
City of God - St. Augustine
Beowulf
The divine comedy - Dante
Vita Nuova - Dante
The Decameron - Boccaccio
Petrarch in English - Petrarch
Canterbury tales - Chaucer
Le Morte d'Arthur - Thomas Malory
Orlando furioso - Ariosto
Don Quixote - Cervantes
Essays - Michel de Montaigne
The Prince - Machiavelli
The Praise of Folly - Erasmus
The complete plays and complete poems - Christopher Marlowe
Faerie Queene - Edmund Spenser
Shakespeare - William Shakespeare
Poetry of John Donne - John Donne
Paradise lost - John Milton
Gulliver's travels - Jonathan Swift
Robinson Crusoe - Daniel Defoe
Moll Flanders - Daniel Defoe
Wuthering heights - Emily Bronte
Jane Eyre - Charlotte Bronte
Tenant of wildfell hall - Anne Bronte
David copperfield - Charles Dickens
Bleak House - Charles Dickens
Vanity Fair - William Makepeace Thackeray
Middlemarch - George Eliot
Uncle Tom's Cabin - Harriet Beecher Stowe
Huckleberry Finn - Mark Twain
Moby Dick - Herman Melville
Sense and Sensibility - Jane Austen
Pride and Prejudice - Jane Austen
Mansfield Park - Jane Austen
Emma - Jane Austen
Persuasion - Jane Austen
Northanger Abbey - Jane Austen
Sanditon - Jane Austen
Mrs. Dalloway - Virginia Woolf
The Great Gatsby - F. Scott Fitzgerald
The Sun also Rises - Hemingway
For Whom the Bell Tolls - Hemingway
The Old man and the sea - Hemingway
The House of Mirth - Edith Wharton
Death in Venice - Thomas Mann
The Lord of the Rings - J.R.R. Tolkien"""

# hand aliases: list title -> normalized title fragments to also try
ALIAS = {
 'arabian nights': ['thousand one nights', '1001 nights', 'arabian nights'],
 'metamorphosis': ['metamorphosis'],
 'annals imperial rome': ['annals'],
 'history conquest mexico': ['conquest mexico', 'history conquest mexico peru'],
 'shakespeares first folio': ['first folio', 'shakespeare'],
 'shakespeare': ['shakespeare'],
 'bible': ['bible'],
 'republic': ['republic'],
 'poems catullus': ['catullus'],
 'poems': ['catullus'],
 'satires juvenal': ['sixteen satires', 'satires'],
 'sixteen satires': ['sixteen satires', 'satires'],
 'autobiography benvenuto cellini': ['autobiography', 'cellini', 'my life'],
 'chronicles': ['chronicles'],
 'confessions': ['confessions'],
 'lyrical ballads': ['lyrical ballads'],
 'nature things': ['nature things', 'way things are'],
 'huckleberry finn': ['huckleberry finn', 'adventures huckleberry finn'],
 'oedipus rex': ['oedipus rex', 'oedipus king', 'theban', 'three theban'],
 'antigone': ['antigone', 'theban', 'three theban'],
 'theban': ['theban', 'three theban'],
 'oresteia': ['oresteia'],
 'elder edda': ['elder edda', 'poetic edda'],
 'njals saga': ['njals saga', 'njal'],
 'gulag archipelago': ['gulag archipelago'],
 'water margin': ['water margin', 'outlaws marsh'],
 'story stone': ['story stone', 'dream red chamber'],
 'dirty dust': ['dirty dust', 'cre na cille', 'graveyard clay'],
 'landmark julius caesar': ['landmark julius caesar', 'julius caesar', 'gallic war', 'civil war'],
 'twelve caesars': ['twelve caesars'],
 'history decline fall roman empire': ['decline fall', 'decline fall roman empire'],
 'le morte darthur': ['morte darthur', 'morte d arthur'],
 'tenant wildfell hall': ['tenant wildfell hall'],
 'medea plays': ['medea'],
 'early history rome': ['early history rome'],
 'parallel lives': ['parallel lives', 'lives', 'rise fall athens', 'fall roman republic', 'makers rome', 'age alexander'],
 'poetry john donne': ['donne'],
 'complete plays complete poems': ['marlowe'],
 'faerie queene': ['faerie queene'],
 'essays': ['essays', 'complete essays'],
 'theogony': ['theogony'],
 'cicero': ['cicero'],
 'plautus': ['plautus'], 'terence': ['terence'], 'xenophon': ['xenophon'],
 'vindication rights woman': ['vindication rights woman', 'vindication'],
 'history kings britain': ['history kings britain'],
 'life samuel johnson': ['life samuel johnson', 'life johnson'],
 'lord rings': ['lord rings', 'fellowship ring', 'two towers', 'return king'],
 'eichmann jerusalem': ['eichmann jerusalem'],
 'secret history': ['secret history'],
 'velveteen rabbit': ['velveteen rabbit'],
 'age reason': ['age reason'],
 'education henry adams': ['education henry adams'],
}

def parse(lst):
    out = []
    for line in lst.strip().split('\n'):
        t, _, a = line.partition(' - ')
        out.append((t.strip(), a.strip()))
    return out

def match(title, author):
    n = norm(title); sur = surname(author) if author else ''
    cands = []
    keys = ALIAS.get(n, []) + [n]
    for b in B:
        bn = b['_n']
        score = 0
        if bn in keys or any(k == bn for k in keys): score = 3
        elif any(bn.startswith(k + ' ') or bn.endswith(' ' + k) or (' ' + k + ' ') in (' ' + bn + ' ') for k in keys if len(k) > 3): score = 2
        else:
            r = difflib.SequenceMatcher(None, n, bn).ratio()
            if r >= 0.86: score = 1
        if not score: continue
        asc = (sur in b['_sur']) if sur else None
        if sur and asc is False and score < 3: continue
        cands.append((score, 1 if asc else 0, b))
    cands.sort(key=lambda c: (-c[0], -c[1]))
    return cands

if __name__ == '__main__':
    for name, lst in [('Steve 100', steve100), ('Western Canon', canon)]:
        print(f'\n===== {name}')
        matched = {}; unmatched = []
        for t, a in parse(lst):
            c = match(t, a)
            good = [x for x in c if x[0] >= 2 or (x[0] == 1 and x[1])]
            if not good: unmatched.append((t, a)); continue
            for s, asc, b in good:
                matched.setdefault(b['_id'], (b, [])); matched[b['_id']][1].append(f'{t} [{s}{"✓" if asc else "?"}]')
        for bid, (b, why) in sorted(matched.items(), key=lambda kv: kv[1][0]['Title']):
            print(f"  {b['_st'] or '-':5} | {b['Title']} — {', '.join(b['_auth'])}  <= {'; '.join(why)}")
        print(f'  -- matched {len(matched)} books; unmatched {len(unmatched)}:')
        for t, a in unmatched: print(f'     ? {t} - {a}')
        json.dump({bid: b['Title'] for bid, (b, _) in matched.items()}, open(f'data/out/list_{name.replace(" ", "_")}.json', 'w'), ensure_ascii=False, indent=0)
