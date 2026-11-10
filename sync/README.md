# Goodreads → Notion sync

Goodreads is where Dan marks a book started, posts his page each day and marks it finished; Notion is canonical for which book and which copy. `goodreads_sync.py` copies the Goodreads **events** into Notion once a night, so the library gets Started and Ended dates and a per-read progress stream without anything typed in chat. It is never a source of truth about the past: Goodreads shelf *state* is only compared with what the last run saw, to find what changed. The decided specification is `docs/SYNC_REVIEW.md`; the hand-off that built it is `docs/SYNC_HANDOFF.md`.

**Sources since 2026-11-10.** Goodreads retired every RSS feed on 2026-10-28 (all 404) and put the shelf pages behind a sign-in; the job failed nightly from then until this change. It now reads two public pages, anonymously:

| Page | What it gives | What it lacks |
|---|---|---|
| Status list `/user_status/list/<id>?page=N` | every page update ("is on page N of M", "N% done") with its comment, its time ("15 hours ago" within a day, exact; older ones a wall-clock time, see below) and its own id `UserStatus<n>`, the ids the old feed used; 30 per page, the **whole history** | starts, finishes, DNFs, want-to-reads |
| Shelf widget `/review/custom_widget/<id>?shelf=…` | the newest 100 books of a shelf: title, author, review id, Goodreads book id (from the cover) | **any date**; paging (one page of at most 100) |

**Wall-clock times are in a zone Goodreads picks per request**, following where the request comes from: Pacific on most GitHub runners, Central European (+9 h) on one, 2026-11-10. Each run therefore calibrates: updates already in the Reading log with an exact time (from the RSS feed, or seen as "N hours ago") give the zone's offset, and the first candidate zone matching every one converts all wall-clock times with its own daylight-saving rules. With no reference on the page the times are read as Pacific and the row's Raw says "time zone unverified" (such rows are never used as references).

So page updates are now complete (the old feed kept about nine items and lost updates posted between runs), while starts, finishes, DNFs and want-to-reads are shelf changes with **estimated dates** (below). The RSS parser stays for the archived feeds and the tests.

## What a run does

1. **Watermark.** The last successful run's time, from the newest `Sync run` row in the Sync queue, minus 48 hours. Anything earlier is history and is never compared, matched or touched. No row yet = the whole feed. `--since YYYY-MM-DD` overrides it.
2. **Answers.** Sync queue rows Dan answered or confirmed since the last run are applied first (see *Questions*). Pending events (an earlier run could not place them) are retried.
3. **Status list**: page updates after the watermark, read back page by page to 35 days (or to `--since`). One not yet in the Reading log (by `Goodreads id`, then by Read + Kind + Eastern date + Page, and for progress the same page within two days, which catches rows typed in by hand while the job was down) is written, one row each. The author, which the list does not show, comes from the shelves by Goodreads book id.
4. **Shelves, by change.** The widget has no dates, so each shelf is read for what changed since the last successful run (the run row keeps a snapshot):
   - `currently-reading`: a Book with no open Read is a start.
   - `read`, `did-not-finish`: an item new at the top of the shelf (read is sorted by date read) is a finish or a DNF; before the first snapshot existed, any item whose Book has an open Read. An item that was already there never closes a Read: an open Read on such a Book is a reread Goodreads wasn't told about, and the *Still reading* question covers it.
   - `to-read`: items whose review id is above the last run's highest (review ids only grow; the first run used Vanity Fair's, shelved 2026-10-18).
   Each becomes an event `Shelf<review>:<kind>:<date>` and goes through the same path. **The date is an estimate**: the most recent evening (the run time minus 12 hours, as GitHub starts the 23:45 schedule hours late and Dan reads in the evening), never before the last run or the Read's last page update, and Dan's own Ended when he closed the Read by chat first. A close by estimate says so in the Read's Notes and in its *Pages read* question ("around 2026-11-09 (estimated)"), where `ended YYYY-MM-DD` corrects it. A read-shelf item that disagrees with a closed Read raises a question; it never edits.
5. **Reads** (the only automatic changes, invariant 2 in the script's docstring):
   - *Create* an open Read on a Started (or a currently-reading item, or progress on a Book whose last Read closed more than 14 days ago): `<Title> · <year>` [(reread)], Started = the event's Eastern date, Dates estimated only when the date is a shelf date or a progress-derived guess, Edition = the only owned copy, else the copy of the last Read, else blank with Format read from the last Read; Edition inferred ✓ when set; Notes "Created from Goodreads <date>".
   - *Close* on Finished / DNF: Ended (never one Dan set), Outcome, on DNF Pages read = the last logged page; Notes "Closed from Goodreads <date>". A Read with Dan's own Pages read is closed too, and his figure is kept (no Pages read question); see the runbook decision of 2026-10-27.
   - *Reopen* when progress arrives within 14 days of a close the sync made (Notes "Reopened from Goodreads <date>: progress continued"). After a close Dan made it is a question. Later than 14 days is a reread.
   - *Started* is filled when blank and replaces an estimated one; a shelf date only fills a blank and stays flagged estimated.
   - A Finished followed by later progress in the same feed is a stray: skipped and noted once on the Read.
   - A changed Goodreads page total on an open Read is an *Edition change* question only. A closed Read's copy is never written by the sync.
6. **Wishlist.** "Wants to read" and fenced to-read items add Lists `Goodreads WTR` and `Wishlisted`; a missing Book is created per the runbook (Author checked by Name and Sort name, Source Goodreads, no Edition, genres left for chat) with a *Book created* question. Books tagged Goodreads WTR that are no longer on the to-read shelf are reported in the log and `unmatched.json`, never untagged.
   A Book that came from Goodreads (this sync or the import) also gets a blank Original year or Pages filled from the to-read shelf; Dan's own Books are never touched.
7. **Gap check.** The feed window is recorded on the Sync run row; when tonight's oldest item is newer than last night's newest, a *Gap* question says which span may be missing (`docs/GAP_FILL.md` fills it by hand). A last successful run older than two days raises one too.
8. **Sync run row** written only after a complete, error-free run, so a partial run (a feed down, a Notion error on one event) leaves the watermark where it was and the next run picks everything up again.

Every automatic change is written to the Read's Notes with the date and "from Goodreads"; a rerun on the same feed writes nothing new.

**What the public pages do not give.** Exact start and finish days: Goodreads keeps them on the review pages, which need a sign-in, so `docs/GAP_FILL.md` reads them from Dan's own signed-in browser when an estimate matters. The newest 100 books per shelf: enough for nightly changes, so *Wishlist removed* can only be checked when the to-read shelf has fewer than 100 books (it has hundreds; the check is skipped). Page updates are complete: the status list keeps every one.

## Questions (the Sync queue, Notion under Media › Library)

Every uncertain case is a row in the **Sync queue** (data source `4e8f45b7-8f9c-4018-8108-7eff25029227`) with the default already applied in Notion (`Default applied` ✓, the value in `Proposed`). Questions are confirmations, not blockers, and never expire. Kinds and what an answer does on the next run:

| Kind | Asked when | `Confirm` ✓ | `Answer` |
|---|---|---|---|
| Edition | every Read the sync creates | keeps the copy, clears Edition inferred, notes "Confirmed by Dan" | a copy (`Paper`, `Kindle`, a publisher, an Edition title) sets it and clears the flag |
| Pages read | every Read the sync closes, unless Dan already set Pages read | keeps Ended and Pages read as applied | a page count, `ended YYYY-MM-DD`, or both |
| Reread or reopen | progress on a Read Dan closed within 14 days | nothing | `reopen` or `reread` |
| Finish date | a finish that disagrees with a closed Read, or lands on no Read | leaves Notion as is | `attach`, `close`, `new`, or a Read title |
| Still reading | an open Read older than two days that is not on the currently-reading shelf | still reading (asked again after 14 days) | `still`, `finished YYYY-MM-DD [pages N]`, `dnf YYYY-MM-DD [pages N]` |
| Edition change | Goodreads' page total changed on an open Read | keeps the copy | a copy, as for Edition |
| Unmatched book | no, or several, Books match the title | leaves the event unlogged | a Notion Book title |
| No open Read | progress with no Read to land on | leaves it unlogged | a Read title |
| Book created | a wishlist Book was created | nothing to do | a Book title if it duplicates one |
| Gap | no run for two days, or the status history went back further than a run reads | noted | — |

`drop` dismisses any row. In chat, the `reading` skill answers "anything to resolve?", "confirm all [except N]" and "N: <answer>" by writing `Confirm` or `Answer` on the rows; the next run applies them. `sync/unmatched.json` mirrors the open rows after each run and the dashboard's **Unconfirmed** tile shows their count (`--export-queue`, then `docs/REFRESH.md`).

## Reading log schema (Notion, under Media › Library)

| Property | Type | Notes |
|---|---|---|
| Name | title | `<Book> · <Kind> [p.N] · <Eastern date>` |
| Read | relation → Reads | the reading episode this event belongs to |
| Book | relation → Books | |
| When | date (with time) | the Goodreads timestamp (Pacific); dates elsewhere are Eastern |
| Kind | select: Started / Progress / Finished / DNF | |
| Page, Of pages, Percent | number | from "page N of M" / "N% done"; Percent has one decimal; Of pages is Goodreads' edition, information only |
| Source | select: Goodreads / Manual | |
| Goodreads id | text | `ReadStatus…` / `UserStatus…` (feed), `Shelf<review>:<kind>:<date>` (shelf), `Timeline<review>:<n>` (backfill, `docs/GAP_FILL.md`), `Backfill:…` (by hand) |
| Note, Raw | text | the comment Dan attached; the feed sentence as received |

Reads.Pages read stays Dan's own figure (pages actually read); Page here is where he was on a given day.

## Where it runs

**GitHub Actions** (`.github/workflows/goodreads-sync.yml`): nightly at 04:45 UTC (23:45 Eastern in winter, 00:45 in summer) and on *Run workflow* with a `dry_run` tick box and a `since` override. The job runs the unit tests, then the sync, and keeps `unmatched.json` and the status archive as a run artifact. Repository secrets: `NOTION_TOKEN`, `GOODREADS_USER_ID`. The profile must stay public: both pages are read signed out. Durable state lives in Notion, so the throw-away checkout is fine; the job commits nothing. A failed run emails through GitHub's default notifications.

**Manual fallback from the Mac**: `cp sync/.env.example sync/.env`, fill in the token, then `python3 sync/goodreads_sync.py --dry-run` to see the plan, or without the flag to run it. The launchd plist in this folder (`com.dan.reading-library.goodreads-sync.plist`, 23:45 nightly) still works for a Mac-only setup: `cp` it to `~/Library/LaunchAgents/` and `launchctl load` it; keep only one runner enabled.

Only the two public pages are fetched (about six requests a night, a second apart), with the generic User-Agent `reading-library-sync/1.0`. No Goodreads password, cookie or key is stored anywhere; review pages are never fetched by the job. A sign-in redirect or a bot check fails the fetch (it is never read as an empty shelf), so the run is partial and the watermark stays.

## Commands

```
python3 -m unittest discover sync                       the tests (168, offline; the fake Notion is in test_sync.py)
python3 sync/goodreads_sync.py --dry-run                fetch, read Notion, print the plan, write nothing
python3 sync/goodreads_sync.py --since 2026-10-01       take events and shelf items after this date (recovery)
python3 sync/goodreads_sync.py --parse-only --fixture sync/fixtures/status_list.html   print the parsed events of a saved status list page
python3 sync/goodreads_sync.py --fixture … --shelf-fixture … --read-fixture … --dnf-fixture … --to-read-fixture … --offline-library data/raw --plan out.json
                                                        replay saved feeds against the repo's raw pull; sync/fixtures/expected_live.json is the committed plan
python3 sync/goodreads_sync.py --export-queue data/out/sync_queue.json   the open questions as the dashboard dataset
python3 sync/goodreads_sync.py --init-queue             create the Sync queue database (done 2026-10-17)
python3 sync/backfill.py --apply [--dry-run]            write a timeline plan (docs/GAP_FILL.md); dedupes by id and by Read + Kind + date + Page
```

Files: `sync/state.json` (the Goodreads-id → Book cache, optional), `sync/unmatched.json` (mirror of the open questions and this run's report), `sync/feed_archive/<date>.jsonl` (the raw status items, appended by id; git-ignored), `~/Library/Logs/goodreads-sync.log` (launchd only).

## Changes

- **2026-11-10 (2)** — Status times calibrated per run (above). Two guards for reruns over old history (`--since`): an update dated before the open Read's (non-estimated) Started goes to the earlier closed Read covering that day, or becomes a *No open Read* question; and an event older than 14 days never creates a Read (a question instead). Found by a full-history dry run that put June *Lonesome Dove* updates on the October reread and opened a June *Qur'an* Read.

- **2026-11-10** — Goodreads retired its RSS feeds on 2026-10-28; the job had failed every night since. Sources replaced by the status list (page updates, complete history, exact times) and the shelf widget (shelf changes, no dates). Starts, finishes, DNFs and want-to-reads are now found by comparing each shelf with the last run's snapshot and dated by estimate, flagged in Notes and in the Pages read question. A progress row typed in by hand during the outage that is a day off the Goodreads time counts as the same update. Sync run rows keep long JSON whole (several rich-text pieces) instead of cutting it at 2000 characters. Fixtures in `sync/fixtures/` are trimmed captures of the real pages (2026-11-10).

- **2026-10-27** — A Goodreads Finished/DNF now closes an open Read even when Dan already set Pages read; his figure is kept and no Pages read question is asked. Questions about a Goodreads event are asked once: an answered or dropped event is not re-asked, including when the read shelf reports the same finish under a different id. (Found on *Uncovering Dinosaur Behavior*, held open 10-18 → 10-27 and re-asked every other night.)
