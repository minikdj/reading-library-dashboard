# Live cycle: the three scripts (docs/SYNC_REVIEW.md, test plan 5–7)

Dan does the Goodreads part; each step says what to press afterwards and what should appear in Notion, in the job log and in chat. Run the job from GitHub: **Actions → Goodreads sync → Run workflow** (leave *dry_run* unticked for these; the log is under the *Sync* step), or from the Mac with `python3 sync/goodreads_sync.py` (same log, in the terminal). Stop at the first step that does not match and say what you saw.

Before part 1 (once): secrets `NOTION_TOKEN` and `GOODREADS_USER_ID` added in the repo settings, `master` pushed, and one *Run workflow* with **dry_run ticked** that ends `done: 0 logged, … (dry run, nothing saved)`.

## Part 1: progress and a wishlist add (one evening)

1. On Goodreads, post a page update on *Lonesome Dove* (the open reread) and one on *Fundamentals of Software Architecture*. Add a book to want-to-read that is **not** in Notion.
2. Run the workflow.
3. Job log: `logged progress Lonesome Dove <page> -> Lonesome Dove · 2026 (reread)`, `logged progress Fundamentals of Software Architecture <page> -> …`, `created Author created: … , Book created: …` (or only the Book when the author exists), `QUESTION Book created - …`, and a `done:` line with `3 logged` (`already there` counts the shelf items it recognised; `WISHLIST REMOVED?` lines are a report only). No other `QUESTION` lines; no `ERROR`.
4. Notion: Reading log has two new Progress rows (`<Title> · Progress p.N · <today>`) on the two Reads; Books has the new title with Lists `Goodreads WTR`, `Wishlisted` today, Source Goodreads, no Edition; the Sync queue has one Open row of Kind *Book created* (Default applied ✓) and one *Sync run* row (Done).
5. Chat: "anything to resolve?" → `1. [Book created] Wants to read “…”: created in Notion from Goodreads — default: Author created…, Book created…`. Say "confirm all" if you like (fills the genres by chat afterwards).

## Part 2: a start, the edition question, the answer

1. On Goodreads, mark a short book as currently reading. Pick one you own in two formats **and** have read before: the default copy is then the one on the last Read, so the question shows it. (Two copies and no earlier Read means Edition is left blank and the question lists both copies; that works too.)
2. Run the workflow.
3. Job log: `created Read <Title> · 2026 - Started <today> - copy: <Format · Publisher> (the copy of <last Read>)`, `QUESTION Edition - Which copy are you reading “<Title>” in? [default: …]`, `logged started …`.
4. Notion: a new open Read `<Title> · 2026` with Started today, Dates estimated unchecked, Edition = the default copy, Edition inferred ✓, Notes `Created from Goodreads <today>`; a Started row in the Reading log; the Sync queue has an Open *Edition* row with `Proposed` = that copy and Default applied ✓.
5. Chat: "anything to resolve?" → `1. [Edition] Which copy…? [default: …]`. Answer "confirm all" (keeps it) or "1: Paper" (changes it).
6. Run the workflow again.
7. Notion: the Read's Edition inferred is cleared; Notes gained `Confirmed by Dan <date>` (or `Edition set to Paper · … by Dan <date>` with the copy changed); the queue row is Answered with a Resolved date. Job log: `resolved Answered - Which copy…`.

## Part 3: the finish, the pages question, the stray reopen

1. On Goodreads, mark that book finished.
2. Run the workflow.
3. Job log: `closed <Title> · 2026: Finished, Ended <today>`, `QUESTION Pages read - Finished “<Title>” on <today>; pages read = blank?`, `logged finished …`.
4. Notion: the Read has Ended today, Outcome Finished, Pages read blank, Notes `+ Closed from Goodreads <today>`; a Finished row in the log; an Open *Pages read* row (`Proposed`: `Ended <today>, Pages read blank`).
5. Dashboard: the Unconfirmed tile shows the count as of the last publish. To see 1 there, say "refresh the dashboard" and Claude runs `--export-queue` and publishes (README "Refreshing the dashboard"); the Notion view *Open questions* is live meanwhile.
6. Chat: "confirm all" (or "1: 312" for the page count). Run the workflow. The row is Answered; Notes `+ Confirmed by Dan <date>` (or `Pages read 312 by Dan …`).
7. The stray case: within a day, post a page update on the same book on Goodreads (it goes back to currently reading). Run the workflow.
8. Job log: `reopened <Title> · 2026 - Reopened from Goodreads <date>: progress continued`, `logged progress …`. Notion: the same Read is open again (Outcome and Ended cleared), Notes `+ Reopened from Goodreads <date>: progress continued`, a Progress row logged, **no second Read**, no new question.
9. Mark it finished again; run; it closes again (`Closed from Goodreads`, a fresh *Pages read* question); "confirm all"; run. Done.

## What a wrong result looks like, and what to do

- A second Read for the same book, a Read whose Edition changed without an answer, a Book or Edition edited, anything deleted: stop and report; these break the invariants and are bugs.
- `ERROR` in the log: the run went on without that event and left the watermark; it is retried the next night. Report it.
- A question you did not expect: answer it or "drop" it; say which so the rule can be checked.
