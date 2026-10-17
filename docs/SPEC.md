# Reading library — specification

Status: v0.1, 2026-10-16. Owner: Dan. The Notion runbook ("Library runbook (for Claude)", page `3fb1b15c8b7781409d37e348858b0b04`) governs the data model and procedures; this spec governs the product: what the system is for, what it must do, and how we judge a change.

## 1. Goal

Replace Goodreads with a system Dan fully owns: a record of every book he reads, owns and wants, kept in Notion, with a dashboard that shows his reading over time and his collection, and that improves by asking Claude for changes rather than by waiting for an app.

Three questions it must always answer:

1. **What have I read?** Books and pages per year and month, pace, rereads, DNFs, formats, genres over time, eras and centuries, fiction vs non-fiction, canonical works, translators, most-read authors, longest books.
2. **What do I own?** Copies by format, binding and publisher; how much of the shelf is read; the unread backlog in books and pages; Kindle buying over time.
3. **What haven't I read?** The wishlist and the unread shelf by genre, era, length and canon; how long the backlog would take at the current pace.

## 2. Principles

- **One source of truth.** Notion holds the data. Nothing on the dashboard is typed in by hand; every number is computed from Notion by code in this repo and attached to the dashboard as a dataset the reader can inspect.
- **Rules as code.** The counting rules (runbook § Model, § Reading stats) are implemented once, in `pipeline/analyze.py`, and verified against the runbook's sanity numbers on every run.
- **Honest charts.** A chart's caption says what the data can't support (fill rates, estimated dates, Claude-assigned tags). Gaps are shown, not smoothed.
- **Works, copies, events.** A Book is a work; an Edition is one physical or digital copy; a Read is one reading event. Owning never implies reading. A reread is a new Read on the same Book. A collection is one Edition linked to every work it contains.
- **Change by request.** Dan asks; Claude changes the pipeline or page, commits, republishes. Nothing requires a release.

## 3. Data model (summary; runbook is authoritative)

| Database | One row per | Key fields |
|---|---|---|
| Authors | person | Name, Sort name, Roles (Author / Translator / Editor) |
| Books | work | Title, Author, Original year (negative = BCE), Pages (fallback), Genre (multi, content genres + one canon tag), Form, Fiction, Lists, Wishlisted, Source, Genre auto |
| Editions | copy | Book(s), Translator(s), Publisher, Year, Format (Paper / Kindle / Audio), Binding, Edition statement, ISBN-13 / ASIN, Pages, Owned, Acquired, Disposed, Disposition |
| Reads | reading event | Book, Edition, Started, Ended, Outcome (Finished / DNF / Paused; blank = reading), Pages read, Format read, Dates estimated, Edition inferred |

Lists (Books.Lists): Goodreads WTR (the imported want-to-read shelf), Steve 100, Western Canon, Up next.

Derived in Notion (never set by hand): Books.Status, Owned n, Disposed n, Times read, Reads.Pages counted, and the per-year helpers. The pipeline recomputes these from raw columns because formulas are invisible to SQL.

## 4. Counting rules

- **Read** = Outcome Finished. DNF never counts. "Books read" counts distinct works; "reads" counts events.
- **Pages per read** = Pages read if filled (indexes and appendices skipped); else, for a finished read, the largest page count among linked Editions, else the Book's Pages; else 0. DNF pages are shown separately, never added.
- **Owned now** = Owned ✓ and no Disposed date. Copies are never counted as books.
- **Status** precedence: Reading › Read › Abandoned › Unread (owns a copy) › Previously owned › Wishlist.
- **Pace** = pages per calendar day (per-read speed is impossible while Dates estimated is ✓ on 229 of 230 reads). "Current pace" = the last 365 days.
- **Backlog years** = (counted pages + uncounted books × the pool's median page count) ÷ current pace.
- **Canon**: Classic = Greek and Roman antiquity through Augustine; Canonical work = other canon first published before 1970 plus four exceptions; both always beside content genres; a book never has both.
- **Era bands**: Antiquity (to 500), Medieval (500–1499), 1500–1799, 1800s, 1900–1949, 1950–1999, 2000s.

## 5. Dashboard requirements

- Built from the claude.ai Dashboard artifact type: plain HTML + d3, `dash.data` / `dash.onData`, every figure marked with its dataset so Sources can show where it came from. No numbers in markup.
- Order: headline tiles (books read, pages read, pace, copies owned, shelf read share, backlog) → What I've read → What I own → What I haven't read.
- Genre charts are split fiction / non-fiction. Series colors follow the entity (Fiction, Paper, Unread…) and never change with filtering.
- Every chart has a one-line note and a caption naming its data gaps. Tables sort; the full reads log searches.
- Works at ~400 px beside a chat and full width.
- Datasets are attached JSON files produced by `pipeline/bundle.py`; their titles and descriptions live in `dashboard/datasets.json`.

## 6. Pipeline requirements

- Input: the four databases pulled verbatim into `data/raw/*.json` (SQL mode, 100 rows per page, raw columns only).
- `analyze.py` prints and must match the runbook's sanity numbers for the pull date; any mismatch is reported to Dan, not patched.
- Every output is a flat JSON array of row objects, one file per dataset, so the dashboard and Sources can read it.
- Personal details never leave the machine in a lookup; external lookups use a generic User-Agent.

## 7. Non-goals (for now)

- A standalone app, login, or hosting outside claude.ai.
- Live Notion queries from the dashboard (the connector caps SQL at 100 rows; joins need a higher plan).
- Ratings as a core feature (Dan doesn't rate; Read Notes hold reviews).
- Importing anything from the archived Old Reading List.

## 8. Open items

- **Dates going forward:** record Started on every new Read so per-read speed becomes possible.
- **Page counts:** 43 Books have none (deliberate: multi-volume works, collections, conflicting sources); 41 of them are on the unread shelf.
- **Genre review:** 925 of 1,064 books carry Claude's tags (Genre auto ✓); Dan reviews via the Books view "My tags changed".
- **Translators undercounted:** 42 library reads link no edition; 63 of 230 reads link a translated edition.
- **Kindle acquired dates:** 52 copies share 2023-11-15 (likely an import date).
- **Missing list works:** 22 works from Steve's lists are being added as unowned Penguin / Oxford copies with Lists tags (in progress 2026-10-16).
- **Runbook:** note the Western Canon option, the New Covenant dating decision (publication year, Barnstone as author), and the Herodotus / Bible merges.

## 9. Roadmap

1. Scheduled refresh (weekly pull → analyze → republish).
2. Logging reads, purchases and wishlist adds by chat (the `reading` skill), feeding the dashboard.
3. Yearly goal / challenge tile; "what to read next" ranked by list membership, era, length and shelf.
4. Covers via Open Library by ISBN.
5. Per-read speed charts once Started dates accumulate.

## 10. Acceptance for any change

- Sanity numbers still match (or the difference is explained by a known Notion edit).
- Every new number on the page is marked with its dataset and has a caption for its gaps.
- The page renders at phone width and full width, light and dark.
- The change is a commit with a message saying what and why.
