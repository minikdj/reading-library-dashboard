---
name: "reading"
description: "Use when Dan logs reading (started, finished, DNF, paused), adds or buys a book, sells or gives one away, borrows from the library, wishlists a book, or asks about his library — all kept in the Notion Library databases."
---

# Dan's reading library (Notion)

Dan keeps his books in four linked Notion databases under **Media › Library**. Use this skill whenever he tells you about a book: he started or finished one, quit one, bought or was given one, sold or gave one away, borrowed one from the library, wants one on his wishlist, or asks a question about what he owns or has read.

## Before you write anything

1. **Read the runbook:** `notion-fetch` page `3fb1b15c8b7781409d37e348858b0b04` ("Library runbook (for Claude)"). It is the source of truth. If it disagrees with this skill, the runbook wins, because Dan's decisions are logged there as they are made.
2. **Use only the Notion connector tools** (`notion-query-data-sources`, `notion-create-pages`, `notion-update-page`, `notion-fetch`).
   - Never use the local `notion-reading-list` tools (`add_book`, `update_book`, `query_books`). They write to the archived old Reading List.
   - Never write to the Old Reading List (`collection://bae2599f-4319-42a8-9f23-89919c21e23e`).
3. **Get today's date** from the current-time tool. Don't guess it.

## Databases

| DB | Data source | One row per |
|---|---|---|
| Authors | `collection://f96682e9-8772-4493-abff-ec079148f1b2` | person (author, translator, editor) |
| Books | `collection://21e3da73-9608-41f8-a857-bd226043a391` | **work** |
| Editions | `collection://d6b4c6a6-4099-4b89-b135-0fa50a918c57` | **copy/object** (paper volume, Kindle file, audiobook), owned or not |
| Reads | `collection://05439130-377c-4e0b-948e-7c1d7ffb065e` | **reading event** |
| Reading log | `collection://ced88101-f6c5-4e4e-9c1f-8305afeb2e7e` | **progress event** from Goodreads (Started / Progress / Finished / DNF); written by the nightly sync, read-only for you |
| Sync queue | `collection://4e8f45b7-8f9c-4018-8108-7eff25029227` | **question** from the nightly sync, with its default already applied |

When creating pages, use `parent: {"data_source_id": "<uuid without collection://>", "type": "data_source_id"}`.

## Core rules

- **Owning ≠ reading.** Never mark a copy read because he owns it, and never mark a copy owned because he read it. He sometimes owns a different translation from the one he read; for example, Don Quixote: he read Grossman and owns Rutherford.
- **Never delete** a Book, Edition or Read to record a change in the real world. A sold copy keeps its row and gets a Disposed date. Moving pages to Trash needs Dan's explicit OK on the exact list, and is only for data errors such as duplicates.
- **Derived fields are never set by hand:**
  - Books: Status, Owned n, Disposed n, Times read, Open reads, Attempts, Formats owned, Translators, Missing, First read, Last read, Read this year, Read when.
  - Editions: Owned n, Disposed n, Owned format, Format text, Translator names, Missing, Title check.
  - Reads: Status, Year, Days, Format, Edition format, Book pages, Edition pages, Pages counted, Finished this year, When, Finished n, Open n.
  - Book Status works out to: Reading › Read › Abandoned › Unread (owns a copy) › Previously owned › Wishlist.
- **Uncertain matches:** if you can't tell which Book or Edition he means, ask him. If he isn't around, write `UNSURE: <why>` in Notes and tell him.
- **Collections:** one Edition linked to the Book of **every** work inside it, titled with the volume's own title. Reads point at the individual work's Book.
- **"Read" means finished.** A DNF never counts as read. Stats count only `Outcome` = Finished.
  - Count distinct works on **Books**: `Read when` is "This year", "Before this year" or blank, so each work counts once even if he reread it or read two translations.
  - Count reading events on **Reads**: `When` is "This year", "Earlier" or blank.
- **Genres (system v2, Dan 2026-10-16):**
  - **Classic** = Greek and Roman antiquity only, from Homer up to and including St Augustine (d. 430). Non-Greco-Roman ancient works (Gilgamesh, the Mahabharata, Confucius) are never Classic; they get Canonical work. Neither are modern books *about* antiquity. Scripture (the Bibles, the Qur'an, the Talmud, the Avesta) gets Religion only, with no canon tag.
  - **Canonical work** = any other work widely regarded as canonical, fiction or non-fiction, whose original publication is **before 1970**. Never use it for genre fiction that isn't canonical, popular non-fiction, textbooks or reference books.
  - A book never has both tags, and never has a canon tag alone. Give it 1–3 content genres first, then the canon tag last (at most 4 in total). Poems and plays get content genres too (Paradise Lost → Religion, Canonical work).
  - **Language** = grammar, usage, style, writing craft, linguistics, dictionaries.
  - Dan's own tags are a non-empty Genre with `Genre auto` unchecked. Never remove or replace his content genres or touch his Form or Fiction. The only changes allowed are applying the Classic / Canonical work rule; set `Genre auto` ✓ when you do, so it shows up in his review views.
  - When you fill blank ones, use only the existing options and set `Genre auto` ✓.
- **Multi-volume sets:** one Edition per physical volume, on the same Book, with `vol. N` in the title and the Notes.
- **Translators come from the copy itself.** Get them from the ISBN record or the copyright page, never from what Dan remembers reading. He sometimes owns a different translation from the one he read; on 2026-10-16, *Crime & Punishment* and *Vita Nuova* went wrong this way.
  - The `Translator` relation is the source of truth. The title repeats the surnames only for readability.
  - Whenever you set or change one, change the other to match, then check that `Title check` is blank.

## Property reference (exact names and options)

**Books**
- `Title`, `Author` (relation → Authors), `Original year` (a number; negative means BCE), `Pages`, `Notes`, `Subjects`
- `Fiction`: Fiction / Non-fiction
- `Form`: Novel, Novella, Short stories, Epic, Play, Poetry, Essay, Essay collection, Letters, Narrative nonfiction, Treatise, Scripture, Reference, Textbook, Technical, Graphic novel, Comic, Picture book, Pamphlet, Anthology
- `Genre` (multi-select): History, Philosophy, Religion, Science, Natural history, Biography, Memoir, Economics, Politics, Current events, Psychology, Self help, Medicine, Math, Computer science, Art history, Travel, Literary fiction, Historical fiction, Classic, Canonical work, Fantasy, Sci-fi, Horror, Mystery, Thriller, Romance, Western, Satire, Mythology, Children’s (typographic ’), YA, Books about books, True crime, Literary criticism, Journalism, Animals, Gardening, Cooking, Business, Language
  - New options can't be written until they exist in the schema. Adding one via DDL means re-sending the full option list; never rename an option through the API, because it strips that option from every row.
- `Lists`: Up next, Goodreads WTR (Steve 100 is unused). `Wishlisted` = the date it was added to a list.
- `Genre auto` (checkbox): ✓ = genre data filled by Claude and not yet confirmed by Dan.
- `Source`: Manual (use this for new Books), Shelf photo, Kindle library, Goodreads, Migrated

**Editions**
- `Edition` (title): `<Book> — <Publisher> · <Edition statement> · tr. <Surnames> · vol. N · <Format>`; leave out the parts that don't apply. Use `ed. <Surnames>` instead of `tr.` for editors.
- `Book` (relation, can hold several), `Translator` (relation → Authors; translators and editors)
- `Publisher` (publisher, imprint or series, e.g. "Penguin Classics Deluxe"), `Year`, `Pages`, `ISBN-13` (13 digits, no hyphens), `ASIN`
- `Edition statement` (text): which edition of the text this is, e.g. "2nd ed.", "3rd ed.", "Rev. ed.", "50th anniversary ed.".
  - Leave it blank for a first or unnumbered edition.
  - Never use it for printings or imprint reissues ("14th printing", "1st Vintage ed.").
- `Format`: Paper / Kindle / Audio
- `Binding` (Paper only): Hardcover / Paperback / Mass market paperback / Other
- `Owned` and `Verified` (checkboxes `"__YES__"` / `"__NO__"`)
- `Source`: Manual, Shelf scan, Kindle library, Library loan, Goodreads, Translator pass, Migrated
- Dates: `date:Acquired:start`, `date:Disposed:start`
- `Disposition`: Sold / Given away / Donated / Traded / Lost
- `Notes`, `Shelf`

**Reads**
- `Read` (title): `<Book title> · <year finished>`. While a read is open, use the start year, and update the title if he finishes in a different year.
- `Book` (relation), `Edition` (relation, can hold two if he switched copies mid-read)
- Dates: `date:Started:start`, `date:Ended:start`. Ended is the day he finished OR stopped; Outcome says which.
- `Outcome`: Finished / DNF / Paused. Leave it blank while he's reading.
- `Pages read` (number): the pages he actually read; he skips indexes and appendices. Record it whenever he gives a number, and always record how far he got on a DNF. Leave it blank to count the copy's page count.
- `Format read`: Paper / Kindle / Audio. Set it only when no Edition is linked.
- `Dates estimated` ✓ when the Started date is a guess. `Edition inferred` ✓ when you linked the copy by rule rather than because he told you.
- `Notes`

## Finding things

- **Book:** search by title in SQL, then confirm the author: `SELECT Title, Author, url FROM "<Books>" WHERE Title LIKE ?` with params `["%<word>%"]`.
  - Look for subtitle variants and alternate titles. Duplicates have come from "Foundation" vs "Foundation: The History of England…", and from "Arabian Nights" vs "The Thousand and One Nights".
- **Author:** check both `Name` and `Sort name` ("Last, First") before creating one. The Author's `Roles` should include Author, Translator and/or Editor as appropriate.
- **His copies of a Book:** `SELECT Edition, Format, Owned, "date:Disposed:start", url FROM "<Editions>" WHERE Book LIKE '%'||?||'%'` with the Book's 32-hex id.
- **Page id** = `substr(url, 24)` in SQL. Relations take bare 32-hex ids.
- SQL can't see formulas or rollups. To filter on Status and similar fields, use rows mode with a formula `resultFilter`. SQL returns at most 100 rows; page through with LIMIT/OFFSET.

## Workflows

### "I started X"
0. If he also marks books on Goodreads, the nightly sync will create the Read itself from the "started reading" event (with the copy as the only owned one or the last Read's, and an Edition question). Logging it here first is fine too: the sync then only adds the Started row and fills the date. Never create a second Read for the same start.
1. Find the Book; if there isn't one, create it (see *"I bought / got / was given X"*, step 1).
2. Pick the Edition:
   - **One owned copy:** link it.
   - **Several owned copies:** ask which one.
   - **Not his** (library, borrowed): see *Library or borrowed copy*.
3. Create the Read with `Read`, `Book`, `Edition` (or `Format read`), and `date:Started:start`. Leave `Outcome` blank.

### "I finished X" / "I gave up on X" / "pausing X"
1. Find the open Read (`Outcome` empty) on that Book.
   - If there is none, he never logged the start. Create the Read now; for Started, use his date, or the end date with `Dates estimated` ✓.
2. Set `date:Ended:start`. Set `Outcome` to Finished, DNF or Paused. Set `Pages read` if he gives a number; for a DNF, set it to how far he got.
   - When he resumes a paused read, clear `Outcome` on the same Read. Don't create a new one.
3. Make sure the title year matches the finish year.
4. **Rereads** always get a new Read on the same Book. Never overwrite an old one.
5. If he switched copies mid-read, link both Editions.

### "I bought / got / was given X"
1. Find or create the Book.
2. Before creating an Edition, check whether one already exists for that copy:
   - An unowned Goodreads or Library-loan Edition with the same ISBN: update it to `Owned` ✓, `Verified` ✓, `Source` Manual, and set Acquired.
3. Otherwise create the Edition with:
   - `Format` and, for paper, `Binding`
   - `Edition statement`, if it isn't a first edition (tech books, textbooks, field guides, Norton Criticals and anniversary editions often aren't)
   - `Publisher`, `Year`, `ISBN-13` or `ASIN`, `Pages`, `Translator` (taken from this copy; see *Core rules*)
   - `Owned` ✓, `Verified` ✓, `date:Acquired:start`
   - `Source`: Manual for a single add, Kindle library or Shelf scan for batch imports
   - In `Notes`, "Gift from …" if it was a gift
4. If it's a collection, link every contained work's Book, creating any that are missing.
5. **Looking up an ISBN:** run curl in the shell. WebFetch is blocked by robots.txt and Google Books is blocked.
   - **Never send Dan's email or any other personal detail** to Open Library or any other outside service: not in the User-Agent, the URL or the payload. Use curl's default User-Agent or a generic one.
   - `curl -s "https://openlibrary.org/api/books?bibkeys=ISBN:<isbn>&format=json&jscmd=details"` gives the edition record. Use these fields:
     - title, authors, publishers, publish_date, number_of_pages
     - `by_statement` and `contributions`: the translator and editor
     - `edition_name`: the edition statement
     - `physical_format`: the binding
   - If a field is missing, also try `curl -s https://openlibrary.org/isbn/<isbn>.json`; some ISBNs have a second record.
   - If Open Library has nothing, ask him for a photo of the copyright page.

### Library or borrowed copy
- Create an unowned Edition (`Owned` NO, `Verified` ✓, `Source` Library loan) and link it, **or** just set `Format read`.
- His default for library ebooks: `Format read` = Kindle, with the Read note "Library ebook (not owned)".

### "I sold / gave away / donated / traded / lost X"
1. Find the Edition. If he owns more than one copy, ask which one.
2. Set `Owned` NO, `date:Disposed:start` = the date, and `Disposition`. Optionally add a note (buyer, price, who received it).
3. Don't touch the Reads. The Book's Status updates itself: Read if he read it, Previously owned if no copy is left and he never read it, Unread if he still owns another copy.

### Wishlist / up next
- Find or create the Book. Don't create an Edition.
- Add `Lists` "Up next" (or whatever he names), and set `date:Wishlisted:start` = today.
- If he removes it from the list, take the tag off.

### The sync's questions ("anything to resolve?", "confirm all", "N: <answer>")
The nightly Goodreads sync (repo `sync/goodreads_sync.py`) creates and closes Reads from Goodreads and asks a question for every uncertain case in the **Sync queue**, with the default already applied in Notion (`Default applied` ✓, the value in `Proposed`). Rows stay `Open` until Dan confirms or answers; the sync never confirms for him. The sync applies answers at the start of its next run (nightly, or when the GitHub Actions workflow *Goodreads sync* is run by hand).

- **"anything to resolve?"** (or "any sync questions?", "what's unconfirmed?"): query the Sync queue for rows with `Status` = Open and `Kind` ≠ Sync run, sorted by `Asked` descending, and list them numbered, one line each: `N. [Kind] Question — default: Proposed`. Show the `Options` only when he asks what he can answer. Keep the numbering for the rest of the conversation. "Nothing to resolve" when there are none. Also mention, if `sync/unmatched.json` in the repo is at hand, the Books it reports as removed from the Goodreads want-to-read shelf.
- **"confirm all"** / **"confirm all except 3, 5"**: set `Confirm` = `__YES__` on every listed Open row except the excluded numbers, one `notion-update-page` call per row (parallel calls are fine). Reply with how many were confirmed and that the next run applies them (Edition inferred cleared and "Confirmed by Dan" noted on confirmed editions).
- **"N: <answer>"** (e.g. "2: Paper", "4: 858", "4: ended 2026-10-14, 858", "1: reopen", "3: finished 2026-10-15 pages 300", "6: drop"): set `Answer` on row N to the text as given. The sync parses it on the next run; the accepted forms per Kind are in `sync/README.md` ("Questions"). `drop` dismisses a row.
- Never set `Status`, `Resolved`, `Proposed` or `Default applied` yourself; the sync does. Never create Sync queue rows by hand. If he wants the answer applied now rather than tonight, do the Notion change yourself by the workflows above **and** set `Answer` so the row is closed on the next run.
- To see what the sync did: the Read's `Notes` carries one dated line per automatic change ("Created from Goodreads …", "Closed from Goodreads …", "Reopened from Goodreads …", "Started … from Goodreads …"); the Reading log holds the events.

### Questions
- "What am I reading?" → Reads where `Outcome` is empty.
- "How many did I read this year?" → Books whose `Read when` is "This year" (distinct works, DNF excluded). Mention any rereads separately.
- "Pages this year" → sum of Reads `Pages counted` where `When` is "This year". Formulas aren't visible to SQL, so use rows mode, or SQL on Outcome, `date:Ended:start` and `Pages read`, with the copy's pages as the fallback.
- Views Dan uses:
  - Books: Books read (all time), Books read this year, Want to read, Unread on my shelf, By genre, Genres to review, My tags changed.
  - Reads: Currently reading, Finished this year, By year, and the Pages per year / Books per year charts.
- "Do I own X?" → the Book's Editions with `Owned` ✓ and no Disposed date. Say which formats.
- Answer in the chat. Don't create pages for one-off questions.

### Batch imports (Libib CSV, Kindle library, Goodreads export)
Follow the runbook's *Batch shelf scan* and *Goodreads export CSV* procedures. In outline:
1. Match on ISBN first, then on title + author.
   - A title match onto an existing copy that has no ISBN is only safe once the translation and edition are confirmed to be the same.
   - If they aren't, create a new Edition, and leave the old copy and its Reads as they are.
2. Plan everything in a scratch file and show Dan the counts and any UNSURE matches.
3. Write in batches of up to 100 pages with `notion-create-pages`.
4. Verify with a query afterwards.

The old to-read list and DNF list are archived on purpose. Never import them.

## After writing

- Re-query what you changed and check that the relations landed. If you touched an Edition's title or translator, check that its `Title check` is blank (rows mode with a formula `resultFilter`).
- Reply in one or two lines with what changed, e.g. "Logged *Middlemarch* as finished on Oct 16 (Oxford Kindle)." No recap of steps.
- When you close or reopen a Read that the sync created or closed, append a dated line to its `Notes` ("Closed by Dan in chat 2026-10-17") so the sync can tell your changes from its own.
- If Dan made a new rule or a one-off decision, append it to the runbook's **Decisions log** with the date.