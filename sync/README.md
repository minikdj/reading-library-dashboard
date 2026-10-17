# Goodreads → Notion sync

Goodreads is where Dan marks a book started, posts his page each day and marks it finished; Notion is canonical for which book and which copy. `goodreads_sync.py` copies the Goodreads **events** into Notion once a night, so the library gets real Started and Ended dates and a per-read progress stream without anything typed in chat. It is never a source of truth about the past: Goodreads shelf *state* is consulted only inside a time fence, to recover events the short-lived updates feed has already dropped. The decided specification is `docs/SYNC_REVIEW.md`; the hand-off that built it is `docs/SYNC_HANDOFF.md`.

## What a run does

1. **Watermark.** The last successful run's time, from the newest `Sync run` row in the Sync queue, minus 48 hours. Anything earlier is history and is never compared, matched or touched. No row yet = the whole feed. `--since YYYY-MM-DD` overrides it.
2. **Answers.** Sync queue rows Dan answered or confirmed since the last run are applied first (see *Questions*). Pending events (an earlier run could not place them) are retried.
3. **Updates feed** (`/user/updates_rss/<id>`): started / page N of M / N% done / finished / did not finish / wants to read, each with a unique id. Events after the watermark that are not yet in the Reading log (by `Goodreads id`, then by Read + Kind + Eastern date + Page) are written, one row each.
4. **Shelves, fenced.** `currently-reading` (always), `read`, `did-not-finish` and `to-read` items dated after the watermark, or whose Book has an open Read, become events (`Shelf<review>:<kind>:<date>`) and go through the same path, so a Finished the feed aged out still closes the Read and a start still creates one. A read-shelf item that disagrees with a closed Read raises a question; it never edits.
5. **Reads** (the only automatic changes, invariant 2 in the script's docstring):
   - *Create* an open Read on a Started (or a currently-reading item, or progress on a Book whose last Read closed more than 14 days ago): `<Title> · <year>` [(reread)], Started = the event's Eastern date, Dates estimated only when the date is a shelf date or a progress-derived guess, Edition = the only owned copy, else the copy of the last Read, else blank with Format read from the last Read; Edition inferred ✓ when set; Notes "Created from Goodreads <date>".
   - *Close* on Finished / DNF: Ended (never one Dan set), Outcome, on DNF Pages read = the last logged page; Notes "Closed from Goodreads <date>". A Read with Dan's own Pages read is not closed (question instead).
   - *Reopen* when progress arrives within 14 days of a close the sync made (Notes "Reopened from Goodreads <date>: progress continued"). After a close Dan made it is a question. Later than 14 days is a reread.
   - *Started* is filled when blank and replaces an estimated one; a shelf date only fills a blank and stays flagged estimated.
   - A Finished followed by later progress in the same feed is a stray: skipped and noted once on the Read.
   - A changed Goodreads page total on an open Read is an *Edition change* question only. A closed Read's copy is never written by the sync.
6. **Wishlist.** "Wants to read" and fenced to-read items add Lists `Goodreads WTR` and `Wishlisted`; a missing Book is created per the runbook (Author checked by Name and Sort name, Source Goodreads, no Edition, genres left for chat) with a *Book created* question. Books tagged Goodreads WTR that are no longer on the to-read shelf are reported in the log and `unmatched.json`, never untagged.
   A Book that came from Goodreads (this sync or the import) also gets a blank Original year or Pages filled from the to-read shelf; Dan's own Books are never touched.
7. **Gap check.** The feed window is recorded on the Sync run row; when tonight's oldest item is newer than last night's newest, a *Gap* question says which span may be missing (`docs/GAP_FILL.md` fills it by hand). A last successful run older than two days raises one too.
8. **Sync run row** written only after a complete, error-free run, so a partial run (a feed down, a Notion error on one event) leaves the watermark where it was and the next run picks everything up again.

Every automatic change is written to the Read's Notes with the date and "from Goodreads"; a rerun on the same feed writes nothing new.

**What the updates feed does not keep.** Goodreads shows only the *latest* status per book in the feed (about nine items in all). Two page updates on the same book between two runs keep only the second; the first is gone before any run sees it (seen 2026-10-17: a p.165 update replaced by p.164 seven minutes later). Started, Finished and DNF survive because the shelves recover them; only the intermediate page updates are lost, and `docs/GAP_FILL.md` fills them by hand when they matter. This is also why the job runs once a night and not after every update.

## Questions (the Sync queue, Notion under Media › Library)

Every uncertain case is a row in the **Sync queue** (data source `4e8f45b7-8f9c-4018-8108-7eff25029227`) with the default already applied in Notion (`Default applied` ✓, the value in `Proposed`). Questions are confirmations, not blockers, and never expire. Kinds and what an answer does on the next run:

| Kind | Asked when | `Confirm` ✓ | `Answer` |
|---|---|---|---|
| Edition | every Read the sync creates | keeps the copy, clears Edition inferred, notes "Confirmed by Dan" | a copy (`Paper`, `Kindle`, a publisher, an Edition title) sets it and clears the flag |
| Pages read | every Read the sync closes | keeps Ended and Pages read as applied | a page count, `ended YYYY-MM-DD`, or both |
| Reread or reopen | progress on a Read Dan closed within 14 days | nothing | `reopen` or `reread` |
| Finish date | a finish that disagrees with a closed Read, or lands on no Read, or on a Read with Dan's Pages read | leaves Notion as is | `attach`, `close`, `new`, or a Read title |
| Still reading | an open Read older than two days that is not on the currently-reading shelf | still reading (asked again after 14 days) | `still`, `finished YYYY-MM-DD [pages N]`, `dnf YYYY-MM-DD [pages N]` |
| Edition change | Goodreads' page total changed on an open Read | keeps the copy | a copy, as for Edition |
| Unmatched book | no, or several, Books match the title | leaves the event unlogged | a Notion Book title |
| No open Read | progress with no Read to land on | leaves it unlogged | a Read title |
| Book created | a wishlist Book was created | nothing to do | a Book title if it duplicates one |
| Gap | the updates feed dropped items, or no run for two days | noted | — |

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

**GitHub Actions** (`.github/workflows/goodreads-sync.yml`): nightly at 04:45 UTC (23:45 Eastern in winter, 00:45 in summer) and on *Run workflow* with a `dry_run` tick box and a `since` override. The job runs the unit tests, then the sync, and keeps `unmatched.json` and the feed archive as a run artifact. Repository secrets: `NOTION_TOKEN`, `GOODREADS_USER_ID` (and `GOODREADS_RSS_KEY` only if the profile ever goes private). Durable state lives in Notion, so the throw-away checkout is fine; the job commits nothing. A failed run emails through GitHub's default notifications.

**Manual fallback from the Mac**: `cp sync/.env.example sync/.env`, fill in the token, then `python3 sync/goodreads_sync.py --dry-run` to see the plan, or without the flag to run it. The launchd plist in this folder (`com.dan.reading-library.goodreads-sync.plist`, 23:45 nightly) still works for a Mac-only setup: `cp` it to `~/Library/LaunchAgents/` and `launchctl load` it; keep only one runner enabled.

Only the public feed URLs go to Goodreads, with the generic User-Agent `reading-library-sync/1.0`. No Goodreads password, cookie or key is stored anywhere; review pages are never fetched by the job.

## Commands

```
python3 -m unittest discover sync                       the tests (122, offline; the fake Notion is in test_sync.py)
python3 sync/goodreads_sync.py --dry-run                fetch, read Notion, print the plan, write nothing
python3 sync/goodreads_sync.py --since 2026-10-01       take events and shelf items after this date (recovery)
python3 sync/goodreads_sync.py --parse-only --fixture f.xml   print the parsed events of a saved feed
python3 sync/goodreads_sync.py --fixture … --shelf-fixture … --read-fixture … --dnf-fixture … --offline-library data/raw --plan out.json
                                                        replay saved feeds against the repo's raw pull; sync/fixtures/expected_live.json is the committed plan
python3 sync/goodreads_sync.py --export-queue data/out/sync_queue.json   the open questions as the dashboard dataset
python3 sync/goodreads_sync.py --init-queue             create the Sync queue database (done 2026-10-17)
python3 sync/backfill.py --apply [--dry-run]            write a timeline plan (docs/GAP_FILL.md); dedupes by id and by Read + Kind + date + Page
```

Files: `sync/state.json` (the Goodreads-id → Book cache, optional), `sync/unmatched.json` (mirror of the open questions and this run's report), `sync/feed_archive/<date>.jsonl` (the raw feed items, appended by id; git-ignored), `~/Library/Logs/goodreads-sync.log` (launchd only).
