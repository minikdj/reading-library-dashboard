# Review: `sync/goodreads_sync.py` (nightly Goodreads → Notion)

Reviewed 2026-10-17 against commit `0ee931c`, by reading the script, `sync/backfill.py`, `sync/README.md`, the launchd plist, and running `--parse-only` and `--offline-library data/raw` on the live fixtures captured 2026-10-17 02:25. The job has not yet run for real (no `sync/state.json`, no log); `unmatched.json` is from an offline run.

Goal stated by Dan: the job must be **self-correcting** — every run should leave Notion more accurate than it found it, never less, and anything it can't fix should stay visible until it is fixed.

What is already right: idempotent writes (Notion is checked by `Goodreads id` before every create, so a lost `state.json` can't double rows); unmatched events are not marked seen, so they retry while still in the feed; the match cache is keyed by Goodreads book id; nothing personal leaves the machine; dry-run and offline modes exist; stdlib only.

## Findings, by severity

### 1. The updates feed is tiny, and missed events are lost for good — HIGH

The live feed holds **9 items spanning ~36 hours** (Oct 15 05:29 → Oct 16 19:18). The Lonesome Dove page updates from Oct 4–14 were already gone when it was captured. A once-a-night poll will miss progress on any day Dan posts more than a handful of updates, and the Mac-asleep "runs at next wake" fallback makes the gap bigger.

Fixes:
- Poll **every 4 hours** (cloud routine `0 */4 * * *`; launchd `StartInterval` 14400). Each run is ~25 Notion requests; the cost is nothing.
- Persist the **raw feed items** to `sync/feed_archive/<date>.jsonl` (append, by guid) before parsing. Then a parser fix or a later matching fix can be replayed over everything the feed ever showed, instead of only what is in the window now.
- Log the feed's own window (`oldest item at`, `newest item at`, `n items`) every run, and emit a CHECK when the oldest item is newer than the previous run's newest: that is the signature of a gap.

### 2. Unmatched events are overwritten, not queued — HIGH (this is the self-correcting gap)

`unmatched.json` is rewritten with only *this run's* unmatched events (`report = {...}` then `write_text`). An event that fails to match tonight, drops out of the feed tomorrow, and is never retried against Notion is silently gone. Yet the common fix — Dan creates the Read by chat the next morning — happens *after* the event has left the feed.

Fix: make `unmatched` a **pending queue** in `state.json` (`pending: {guid: event}`), keyed by guid, merged across runs. Every run, before fetching, **retry every pending event against Notion** (`lib.match` + `store.open_read`); events that now match are written and removed. `unmatched.json` becomes a view of the queue, not of the run. Add `--resolve <guid> --read <page_id>` and `--drop <guid>` flags so a chat can clear items by hand. Keep an entry's `first_seen`, `last_tried`, `tries`.

### 3. The Notion-side half of the reading check is dead in live mode — HIGH

`check_reading` compares the Goodreads shelf with Notion's open Reads "both ways", but `read_norm()` does not carry the Book relation, so in live mode every Notion open Read hits `if bid is None and store.notion: continue`. "Notion: open Read, but not on the Goodreads currently-reading shelf" can only ever fire offline. Since the open Read with no Goodreads shelf entry is exactly the case where Dan finished on Goodreads and forgot Notion (or vice versa), this matters.

Fix: `read_norm` adds `'book': (prop_rel(p.get('Book')) or [None])[0]` and `'dates_estimated': (p.get('Dates estimated') or {}).get('checkbox')`; delete the `continue`.

### 4. A real Started date never replaces an estimated one — MEDIUM

`if ev['kind'] == 'started' and ev['at'] and not read.get('started')`: a Started event lands on a Read whose Started is already filled, even when `Dates estimated` is ✓. The 7 Reads still marked estimated (and any future Read Dan logs by chat with a guessed date) will keep the guess.

Fix: update when Started is blank **or** `dates_estimated` is true; log "replaced estimated Started 2026-10-01 with 2026-10-04".

### 5. Finished / DNF events are logged but never close the Read — MEDIUM

When Goodreads says *finished* and the matched Read is still open, the script writes the log row and leaves the Read open. The reading check will later report it as a shelf mismatch, but the Read is wrong in the meantime, and the dashboard's "reading now" with it.

Fix (self-correcting, still conservative): on a Finished/DNF event that lands on an **open** Read, set `Ended` = event date and `Outcome` = Finished/DNF, and for DNF set `Pages read` to the last Progress page in the log for that Read. Append to the Read's Notes: "Closed from Goodreads <date>; add Pages read." Emit a CHECK so Dan supplies Pages read by chat (the runbook says Pages read is his figure, so leave it blank on Finished rather than guessing). Never reopen a closed Read from Goodreads.

### 6. A Started event with no open Read goes nowhere — MEDIUM (design decision for Dan)

Today: UNMATCHED "no open Read in Notion". This is the Lonesome Dove reread case and it will recur every time Dan starts a book on Goodreads first (which the README says is the normal order). Two options:

- **Create the Read** when the Book matches unambiguously and has no open Read: `Read` = `<Title> · <year>`, `Started` = event date, `Dates estimated` = NO, `Edition` = the Book's single owned copy if there is exactly one (mark `Edition inferred` ✓), otherwise leave Edition blank and set `Format read` from the last Read of that Book, with a Note "Created from Goodreads; confirm the copy." Emit a CHECK either way. This is the self-correcting choice and matches the runbook's Log-reading procedure except for the "ask which copy" step, which the CHECK replaces.
- **Keep it pending** (finding 2) and let Dan create the Read by chat; the queued event then lands on it automatically.

Recommendation: create the Read. Notion stays canonical for *which copy*; the fact that a read began is Goodreads' to report.

### 7. "Wants to read" events are dropped — MEDIUM

The feed carries `Dan wants to read 'X'` (3 of the 9 live items). The runbook defines Goodreads WTR as the wishlist (`Lists` = Goodreads WTR, `Wishlisted` = date added), and the dashboard's wishlist is built from it. Ignoring these means the wishlist in Notion decays from the moment of the import.

Fix: parse `wants to read` as kind `wishlisted`. If the Book exists, add `Goodreads WTR` to `Lists` and set `Wishlisted` if blank. If it doesn't exist, follow the runbook's Goodreads-export rule: create Author(s) (after checking Name and Sort name) and the Book with `Source` = Goodreads, `Original year` and `Pages` if the shelf feed has them, no Edition; emit a CHECK listing what was created. Also handle the reverse: a Book tagged Goodreads WTR that has left the Goodreads want-to-read shelf (the `to-read` shelf RSS gives the current list) — report it, don't untag automatically.

### 8. Dates are sliced in Goodreads' timezone — LOW/MEDIUM

`ev['at'][:10]` and the Name's `when[:10]` take the date from the feed timestamp, which is `-07:00` (Pacific). Dan is in `America/New_York`. Anything he logs after 9 pm ET lands on the previous day; late-night page updates are the common case for a reader. The 2026-10-15 05:29-07:00 finish in the live feed is 08:29 ET, same day, so the fixtures didn't expose it.

Fix: `at.astimezone(ZoneInfo('America/New_York')).date()` for Started/Ended and the Name; keep the full ISO timestamp in `When`. `TZ` in `.env`.

### 9. Percent is rounded inconsistently — LOW

The sync rounds `100*page/total` to an integer (`7` for 63/869); the backfill wrote two decimals (`4.67`). The dashboard will mix them. Pick one (one decimal) in `log_props` and `backfill.py`.

### 10. Secondary dedupe key — LOW

Dedupe is by `Goodreads id` only. The 12 rows I added by chat on 2026-10-17 carry `Backfill:…` ids (the parked file had no timeline ids), and `backfill.py --apply` dedupes on `Timeline*` ids only, so a rerun would double them. Add a second guard everywhere a row is created: skip when a row with the same `Read`, `Kind`, `When` date and `Page` already exists.

### 11. Fetch failure aborts the whole run — LOW

`sys.exit('could not fetch Goodreads')` on any feed error; no retry, and the other feed is not tried. Retry 3× with backoff; if the updates feed fails but the shelf succeeds, still run the pending-queue retry and the shelf check, and vice versa. Exit non-zero only when nothing could be done, and record `last_success` in state so a CHECK fires after 2 days without one.

### 12. Smaller points

- `check_reading`'s `since` is `user_date_added` — the date the book was first shelved, not when it moved to currently-reading. Label it "shelved" or drop it.
- `recent_read` is used for Finished/DNF with no open Read: if the newest Read is already Finished with an Ended that matches the event date ±2 days, it's Dan's own entry and the event should attach quietly; if the dates differ by more, emit a CHECK (that's how the three finish-date mismatches in the backfill would have surfaced).
- `book_cache` can point at a trashed Book after a merge (Herodotus, the Bibles on 2026-10-16); it falls through to matching, which is fine, but drop the stale entry so the cache doesn't mask a later rename.
- `state['runs']` keeps 30 entries; also keep `last_success` and the feed window (finding 1).
- The cloud routine throws away `state.json`, so there the pending queue (finding 2) must live somewhere durable: commit `sync/state.json` from the routine, or keep the queue in a small Notion database ("Sync queue"), which also lets Dan resolve items from Notion. Prefer Notion: it's visible and survives both runners.
- `--dry-run` still calls `already_logged` as `False` path and prints creates for rows that exist; make dry-run read Notion for the existence check so its output is what a real run would do.
- Tests: there are fixtures but no test runner. Add `sync/test_sync.py` with the parser cases (page/total, percent-only, note with curly quotes, title with "by Author", series "(Series, #1)", a liked item, a wants-to-read), the matcher cases (subtitle truncation, series prefix, Stalin vol. 1 vs 2, Animal Liberation vs Now), and the timezone edge. `python3 -m unittest` in the routine before the sync runs.

## Suggested order of work

1. Findings 3, 4, 8, 9 — small, mechanical, no design decisions (one commit).
2. Finding 2 (pending queue in Notion) and finding 1 (4-hourly, feed archive, gap CHECK).
3. Findings 5 and 6 (close and create Reads) — Dan's call on 6 first.
4. Finding 7 (wishlist) and the dedupe/retry hardening (10, 11).
5. Tests, then a `--replay sync/feed_archive` mode so parser fixes re-run over history.

Each step keeps the current guarantee (nothing is ever deleted or overwritten in Notion except Started-when-estimated and Ended/Outcome-when-open) and adds a CHECK for every automatic correction, so Dan can audit what the job did from `unmatched.json` or the Sync queue.

## Dan's decisions (2026-10-17) and the design they imply

**Schedule: once a day, with a gap check.** The updates feed has no "since" parameter and keeps only a short window (9 items over ~36 h in the live capture), so a gap can be detected but not refilled from that feed. Recovery comes from the shelf feeds instead, which the daily run must reconcile every time:

- `read` shelf: `user_read_at` is the finish date for every finished book. Any Read in Notion that is open while its book is on the read shelf with a `user_read_at` after `Started` → close it (see below). Any read-shelf finish with no Read in Notion at all → question for Dan.
- `currently-reading` shelf: a book here with no open Read in Notion → create one (see below); an open Read in Notion whose book is not here → question ("still reading, or did you finish?").
- `did-not-finish` shelf: same as read, with Outcome DNF.
- Gap check: record the feed window's oldest and newest item timestamps per run; when tonight's oldest is later than last night's newest, emit `CHECK gap: page updates between <t1> and <t2> may be missing` so the progress stream's holes are labelled, not silent.

**Creating and closing Reads: yes, with no duplicates and the edition settled with Dan.**

- *Create* an open Read when a Started event (or a currently-reading shelf item) has an unambiguous Book and that Book has no open Read **and** no Read closed within the last 14 days (that case is a reopen, below). Read = `<Title> · <year>`, Started = event date (Eastern), Dates estimated = NO, Notes = "Created from Goodreads <date>". Duplicate guard: before creating, re-query Notion for an open Read on that Book (not the cached library), and also check the Sync queue for a pending create on the same Book.
- *Edition* is never decided from Goodreads. Goodreads' edition is whatever Dan happened to shelve and changes for reasons that have nothing to do with the copy in his hands; its page total goes into `Of pages` on each log row as information only. On create: if the Book has exactly one owned copy, link it with `Edition inferred` ✓; if it has several, or none, leave Edition blank and put a **question** in the Sync queue: "Which copy are you reading *Lonesome Dove* in? Paper (Simon & Schuster) / Kindle / other". A change in `Of pages` mid-read is logged, never acted on. The sync never changes an Edition that is set; a Read with a confirmed edition keeps it whatever Goodreads says.
- *Close* an open Read when a Finished or DNF event (or the shelf reconciliation) says so: Ended = event date, Outcome = Finished / DNF, Notes += "Closed from Goodreads <date>"; for DNF, Pages read = the last Progress page in the log. Pages read is left blank on Finished (it is Dan's figure) and a question is queued: "Finished *X* on <date> — how many pages did you read?" The sync never closes a Read that has a Pages read value Dan entered, and never changes an Ended that Dan set.

**Reopening: a Read closed by the sync that gets more progress is reopened, not duplicated.**

- If a Progress or Started event lands on a Book whose newest Read is Finished/DNF **by the sync** (Notes carries "Closed from Goodreads") and the event is after Ended and within 14 days of it: clear Outcome and Ended, Notes += "Reopened from Goodreads <date>: progress continued", and attach the event. This is the Lonesome Dove Oct 9 pattern.
- If the newest Read was closed **by Dan** (Pages read filled, or no sync note) and progress arrives within 14 days: don't touch it; queue a question ("Goodreads shows you back on *X* after you finished it on <date> — reread, or reopen?").
- If more than 14 days have passed: treat it as a reread and create a new Read (with the edition question).
- A Finished event that is followed within the same run by later Progress on the same book is skipped as a stray and noted on the Read, so the close/reopen pair never happens.

**Questions for Dan: a Sync queue in Notion, not a JSON file.** A small database under Media › Library: Question, Book (relation), Read (relation), Kind (edition / pages read / reread-or-reopen / unmatched book / gap), Options, Answer, Status (Open / Answered / Dismissed), Asked, Resolved. The sync writes questions there and applies answers on its next run (Answer "Kindle" → link the Kindle copy; a page count → Pages read). Dan answers in Notion, or by chat — the `reading` skill reads the open questions, and "anything to resolve?" lists them. The dashboard can show the open count. This replaces `unmatched.json` as the durable queue and survives the cloud routine's throw-away checkout.

**Invariants the sync keeps (write these into its docstring and tests):**

1. Never deletes anything. Never changes a Book, an Edition, or a Read's Edition once set.
2. Changes to a Read are limited to: Started when blank or estimated; Ended/Outcome when open (close) or when closed by the sync within 14 days (reopen); Pages read on DNF only; Notes appended.
3. Every automatic change is written to the Read's Notes with the date and "from Goodreads", and every uncertain case becomes a question rather than a guess.
4. A rerun with the same feed produces no new rows and no new changes (dedupe by Goodreads id and by Read + Kind + date + Page).

## Where the job runs (recommendation, 2026-10-17)

Run it as a **GitHub Actions scheduled workflow** on `minikdj/reading-library-dashboard`, not on the MacBook and not as a cloud routine:

- `.github/workflows/goodreads-sync.yml`: `on: schedule: cron: '45 4 * * *'` (23:45 Eastern during DST; add a second entry for standard time or accept the hour's drift) plus `workflow_dispatch` for a manual run. Steps: checkout, `python3 -m unittest discover sync`, `python3 sync/goodreads_sync.py`. No commits from the job.
- Secrets: `NOTION_TOKEN`, `GOODREADS_USER_ID` (and `GOODREADS_RSS_KEY` if the profile ever goes private). The workflow passes them as env; the script already reads env before `.env`.
- Durable state lives in Notion (the Sync queue and the Reading log's Goodreads ids), so the throwaway checkout is fine. Print the run summary and the open questions count to the job log; a failed run emails Dan through GitHub's default notifications.
- Keep the launchd plist only as a documented manual fallback (`--dry-run` from the Mac to debug), or delete it.

## Goodreads editions are current state, never history (Dan, 2026-10-17)

Goodreads keeps one review per book. When Dan picks a new edition for a reread, Goodreads shows the *entire* history of that book — earlier reads included — against the new edition. So:

1. The sync never writes an Edition onto a closed Read. Past Reads' Editions change only by Dan's instruction in chat.
2. For an open Read, an edition signal from Goodreads (a new `gr_book_id` for the same work, or a changed `Of pages`) only produces a Sync-queue question; it never edits the Read. Unanswered means unchanged.
3. `book_cache` maps a Goodreads book id to a Notion **Book** (the work), never to an Edition, and a cache miss on a new edition falls back to title matching.
4. `Of pages` on a log row is the Goodreads edition's page count at the time of the event — informational. Reads.Pages read stays Dan's figure.
5. Tests: a fixture where the same work appears under two Goodreads book ids across two reads must yield two Reads with their original Editions untouched and one open question.

## Events, not state: the watermark fence (Dan, 2026-10-17)

This supersedes the unbounded "compare all three shelves every night" wording above. Goodreads exposes two kinds of thing: **events** (the updates feed: started / page N of M / finished / did not finish / wants to read, each timestamped with a unique id) and **state** (the shelf feeds: where each book sits now, with the dates and edition Goodreads currently attributes to it). Only events are written. State is consulted only inside a time fence, to recover events the short-lived updates feed has already dropped.

Run order:

1. **Watermark** = the last successful run's timestamp, stored in Notion (a `Sync run` row in the Sync queue database), minus a 48-hour margin. Everything earlier is history: never compared, matched or touched.
2. **Updates feed** → events after the watermark that are not already in the Reading log (Goodreads id; then Read + Kind + date + Page). One Reading log row per event. Derived changes are exactly the set in "Dan's decisions": Started fill/replace-estimated; close on Finished/DNF; reopen within 14 days of a sync close; create an open Read when none exists; every change noted on the Read.
3. **Shelves, fenced** → currently-reading, read, did-not-finish items whose Goodreads date (`user_date_added` / `user_read_at`) is after the watermark, **or** whose Book has an open Read in Notion. These recover a Started or Finished the feed aged out. Items outside the fence are not read at all. A fenced shelf item that disagrees with a *closed* Read (date, edition) raises a question; it never edits.
4. **Wishlist** → "wants to read" events, and fenced new `to-read` shelf items, add Goodreads WTR + Wishlisted (creating the Book per the runbook if missing). Removals are reported only.
5. **Questions** → Sync queue; answers since the last run are applied at the start of the next.
6. **Gap check** → feed window logged; a gap raised when tonight's oldest item is newer than last night's newest. Watermark advanced only after a successful run.

Dan's intent, in one line: the Goodreads sweep exists to supply the granular events (page updates, start and finish days) so he doesn't have to type them in chat; it is never a source of truth about the past.

## Question policy: three tiers, defaults applied immediately (Dan, 2026-10-17)

| Event | Action | Question |
|---|---|---|
| Page / percent update | Reading log row | None |
| Started (no open Read) | Create the Read now: Started = event date, Dates estimated NO, Edition = default (below), `Edition inferred` ✓, Notes "Created from Goodreads <date>" | **Yes, always**: "Which copy? [default: <copy>]" — confirming or ignoring keeps the default; naming another copy changes it and clears Edition inferred |
| Finished / DNF (open Read) | Close the Read now: Ended = event date (Eastern), Outcome; DNF → Pages read = last logged page; Finished → Pages read blank; Notes "Closed from Goodreads <date>" | **Yes, always**: "Finished *X* on <date>; pages read = <blank / N>? [default]" — an answer sets Pages read or corrects Ended |
| Progress after a sync close (≤14 days) | Reopen | None (noted on the Read) |
| Progress after a Dan close (≤14 days), or any event >14 days after the newest Read closed | Nothing / new Read | Yes: "reread or reopen?" / edition question as above |
| Wants to read | Tag the Book (create per runbook if missing) | Only when a Book was created |
| Unmatched title, ambiguous match, Goodreads edition change on an open Read | Nothing | Yes |

**Edition default**, in order: the Book's only owned copy → the copy linked on the Book's most recent Read → blank, with the owned copies listed in the question. Goodreads' own edition is never the default (see "Goodreads editions are current state").

Questions are confirmations, not blockers: Notion is updated with the default first, the Sync queue row carries `Default applied` ✓ and the proposed value, and the next run applies any answer. Unanswered rows stay open (no auto-expiry) so the dashboard's "unconfirmed" count means something.

**Bulk confirmation (Dan, 2026-10-17).** Open questions never expire, but they must be cheap to clear in one go:

- Every Sync queue row carries `Default applied` ✓, `Proposed` (the default as applied: "Kindle · Simon & Schuster", "Ended 2026-10-09, Pages read blank"), `Status` = Open, and a `Confirm` checkbox.
- In Notion: a view "Open questions" grouped by Kind, where ticking `Confirm` on any number of rows (or the view's select-all) marks them Answered with the default kept. The next run clears `Edition inferred` on confirmed editions and appends "Confirmed by Dan <date>" to the Read's Notes.
- In chat: "anything to resolve?" lists the open rows with their defaults, one line each; "confirm all" or "confirm all except N" answers them in one write; "N: Paper" overrides one. The `reading` skill gets these three phrases.
- Dashboard: an "Unconfirmed" tile with the open count, clicking through to the list with the defaults shown, so a confirm-all is a glance away.
- The sync itself never confirms on Dan's behalf: a row stays Open until he ticks it or answers.

## No Goodreads credentials (Dan, 2026-10-17)

The job reads only the public RSS feeds, anonymously, with the generic User-Agent. No Goodreads/Amazon password, session cookie or `key=` token is stored in GitHub or the repo (the `GOODREADS_RSS_KEY` path stays in the code for the private-profile case only). Review pages (the full reading-progress timeline) are not fetched by the job. When the gap check reports a span worth recovering, Dan fills it on request from his own machine: Claude reads the affected review pages in the signed-in browser pane and writes the missing Reading log rows with their `Timeline<review>:<n>` ids, exactly as the backfill did. Document this as `docs/GAP_FILL.md` (the one-time procedure, with the dedupe keys).

## Full-cycle test plan (what "made and tested" means)

Claude Code runs 1–4; Dan takes part in 5–7. Each step has a pass condition; stop and report at the first failure.

1. **Unit tests** (`python3 -m unittest discover sync`), all offline, using the fixtures in `sync/fixtures/`:
   - parser: page/total, percent-only, note with curly quotes, "by Author" suffix, series "(Series, #1)", a liked item (dropped), wants-to-read, did-not-finish; Eastern-date slicing at 23:30 PT / 02:30 ET.
   - matcher: subtitle truncation, series prefix, Stalin vol. 1 vs vol. 2, Animal Liberation vs Animal Liberation Now, two Goodreads book ids for one work (edition change) → one Book.
   - rules: Started fills blank; Started replaces estimated; Started on a confirmed date is left alone; Finished closes an open Read and queues a question; Finished on a Read Dan closed within 14 days attaches quietly when dates agree, questions when they differ; Progress within 14 days of a sync close reopens; Progress after a Dan close questions; >14 days → new Read; stray Finished followed by Progress in one run is skipped; Of pages change → question only; closed Read's Edition never written.
   - idempotence: running the same fixture twice yields zero new writes (dedupe by Goodreads id and by Read+Kind+date+Page); a Backfill-id row blocks a Timeline-id duplicate of the same event.
   - fence: a read-shelf item dated before the watermark is never compared; one dated after it, with no Read, raises a question; one whose Book has an open Read closes it.
   - questions: a pending question with `Confirm` ✓ is applied on the next run (Edition inferred cleared, note appended); an unanswered one is left Open; "confirm all" answers every open row.
2. **Offline replay**: `--fixture sync/fixtures/live_updates.xml --shelf-fixture … --offline-library data/raw` prints the planned writes; they must match the expected list committed as `sync/fixtures/expected_live.json`.
3. **Dry run against Notion**: `--dry-run` with the real token; no writes; the log shows the same plan as step 2 adjusted for what is already in the Reading log (the 630 existing rows must all be recognised as already logged).
4. **Workflow**: `.github/workflows/goodreads-sync.yml` with cron + `workflow_dispatch`; secrets `NOTION_TOKEN`, `GOODREADS_USER_ID` set by Dan; a `workflow_dispatch` run in `--dry-run` mode passes (unit tests + dry run in the job log). The **Sync queue** database exists under Media › Library with the schema in "Questions for Dan", created by the script's `--init-queue` (or by Claude through the connector), and its id is in the workflow env / `.env.example`. The runbook page gets a short "Sync" section: what the job writes, the question policy, how to confirm.
5. **Live cycle, part 1 (Dan on Goodreads, one evening):** post a page update on the open *Lonesome Dove* reread and on *Fundamentals of Software Architecture*; add a book to want-to-read that is not in Notion. Run the workflow by hand. Pass: two Progress rows and one wishlisted Book appear, no questions except "Book created".
6. **Live cycle, part 2:** mark a short book started on Goodreads (one Dan owns in two formats, so the edition question fires). Run. Pass: an open Read exists with Edition inferred ✓ and the default copy; one Open question with the default shown. Dan answers it by chat ("anything to resolve?" → confirm or override). Run again. Pass: Edition inferred cleared (or Edition changed), question Answered, Notes appended.
7. **Live cycle, part 3:** mark it finished on Goodreads. Run. Pass: Read closed with Ended = that date, Pages read blank, one Open question with the default; the dashboard's Unconfirmed tile shows 1. "confirm all" in chat; run; question Answered. Then post a page update on the same book within a day (the stray case): Run. Pass: the Read is reopened with a note, no duplicate Read. Mark finished again; confirm; done.
8. **Soak:** leave the nightly job running for a week; the log shows one run a night, zero errors, gap checks only on nights Dan posted more than the window held. Then publish the refreshed dashboard (per README "Refreshing the dashboard") and check the "reading now" and pace tiles against Notion.

Acceptance: all of 1–7 pass, the invariants in "Dan's decisions" hold in the tests, and every automatic change made during 5–7 is visible in the Read's Notes.
