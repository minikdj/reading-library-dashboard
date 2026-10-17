# Handoff: Dan's reading library: stats and data visualization

## What I want
Use my Notion library to give me a rich picture of my reading and my collection, with charts I can come back to. Cover three things:
1. **What I've read, historically:** books and pages per year and month, pace, rereads, DNFs, formats, genres over time, eras and centuries, fiction vs non-fiction, canonical works, translators, most-read authors, longest books.
2. **What I own:** copies by format, binding and publisher; how much of my shelf I've read; unread-on-shelf backlog in books and pages; Kindle buying over time.
3. **What I haven't read:** the wishlist and the unread shelf, broken down by genre, era and length, plus how long the backlog would take at my current pace.

Build it as an HTML page I can keep. Use a Dashboard artifact if that type is available, otherwise a single self-contained HTML page. Lead with a few headline numbers, then the charts.

## Ground rules
- **Read-only.** Don't write to Notion unless I ask.
- **Never touch the archived Old Reading List** (`collection://bae2599f-4319-42a8-9f23-89919c21e23e`), and don't use any local `notion-reading-list` tools.
- **Never send my email or other personal details** to an outside service, whether in a header, URL or payload.
- **Read the runbook first:** Notion page `3fb1b15c8b7781409d37e348858b0b04` ("Library runbook (for Claude)"). It's the source of truth for the rules below.
- A diagram of the data model is in my artifact "Library Data Model": https://claude.ai/artifact/4ubUCLUNUNKWtRoXU8Rzy8
- **Pull everything into local files first and compute in Python.** Don't page result sets through the chat.
  - SQL returns at most 100 rows per call, so page with LIMIT/OFFSET.
  - Formula and rollup columns aren't visible to SQL. Either rebuild them from the raw columns (below) or use rows mode.

## Databases (Notion connector)
| DB | Data source | One row per |
|---|---|---|
| Authors | `collection://f96682e9-8772-4493-abff-ec079148f1b2` | person (author, translator, editor) |
| Books | `collection://21e3da73-9608-41f8-a857-bd226043a391` | **work** (1,064) |
| Editions | `collection://d6b4c6a6-4099-4b89-b135-0fa50a918c57` | **copy** (paper volume or Kindle file), owned or not (620) |
| Reads | `collection://05439130-377c-4e0b-948e-7c1d7ffb065e` | **reading event** (233) |

- Page id = `substr(url,24)`.
- Relations are stored as JSON arrays of page URLs. Join with `b.Author LIKE '%'||substr(a.url,24)||'%'`.
- Multi-selects such as Genre and Lists are JSON-array strings.
- Checkboxes are `'__YES__'` / `'__NO__'`.
- Date columns are named like `"date:Ended:start"`.

### Key raw columns
- **Books:** Title, Author, Original year (negative = BCE), Pages, Genre, Form, Fiction, Lists (Goodreads WTR = wishlist; Up next), Wishlisted (date), Source, Genre auto (✓ = Claude's tags; unchecked = mine).
- **Editions:** Edition (title), Book, Translator, Publisher, Year, Format (Paper / Kindle), Binding, Pages, Owned, Acquired, Disposed, Disposition, Edition statement.
- **Reads:** Read (title), Book, Edition, Started, Ended, Outcome (Finished / DNF / Paused; blank = currently reading), Pages read, Format read, Dates estimated, Edition inferred.

## Counting rules
- **"Read" means Outcome = Finished.** A DNF never counts as read.
  - "Books read" counts distinct Books, so a reread or a second translation counts once.
  - "Reads" counts events, rereads included.
- **Pages counted per read:**
  - If Pages read is filled in, use it. These are the pages I actually read; I skip indexes and appendices.
  - Otherwise, for a finished read, use the largest Pages among its linked Editions, falling back to the Book's Pages.
  - Otherwise, 0.
  - For a DNF, Pages read is how far I got. Show it separately; don't add it to the read totals.
- **Owned now:** an Edition with Owned ✓ and no Disposed date. Editions are copies; never count them as books.
- **Book status**, highest precedence first:
  - Reading: there's an open Read.
  - Read: there's a finished Read.
  - Abandoned: only DNFs.
  - Unread: I own a copy.
  - Previously owned: only disposed copies.
  - Wishlist: none of the above.
- **Collections:** one Edition can link several Books (for example, a Mann collection holding 12 stories). Reads point at the individual work.
- **Genres:**
  - **Classic** = Greek and Roman antiquity, up to Augustine.
  - **Canonical work** = any other canonical work from before 1970, plus four exceptions I picked: Gravity's Rainbow, Gulag, Killer Angels and Bury My Heart.
  - **Language** = grammar, usage and writing.
  - The canon tag is always paired with content genres.

## Sanity checks (as of 2026-10-16)
- **Reads:** 230 Finished (Aug 2021 to Oct 2026), 2 DNF, 1 currently reading.
- **Pages by year:**

  | Year | Pages |
  |---|---|
  | 2021 | 2,383 (partial year; tracking started in Aug) |
  | 2022 | 23,363 |
  | 2023 | 27,334 |
  | 2024 | 11,760 |
  | 2025 | 17,445 |
  | 2026 | 19,545 (as of Oct 15) |

- **2026 so far:** 41 finished books.
- **Owned now:** 400 paper and 201 Kindle copies.
- **Genres:** 37 Classic and 287 Canonical work. 7 Books have no genre.

## Data caveats to respect
- **Acquired dates exist only for Kindle copies,** so an acquisition timeline is Kindle-only.
- **Dates estimated ✓** means Started is a guess. Leave those reads out of reading-speed stats.
- **Pages coverage:** 43 Books have no Pages. Say how many books a page-based chart is missing.
- **Genre auto ✓ marks my tags as Claude's guesses,** not my confirmed picks. That's fine for charts.
- **History starts in 2021.** Anything older isn't recorded.
- **Check fill rates before charting any field,** and note the gaps in the charts' captions.

If the data surprises you, or a number doesn't match the sanity checks, tell me rather than smoothing it over.