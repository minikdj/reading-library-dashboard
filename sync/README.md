# Goodreads → Notion nightly sync

Goodreads is where Dan marks a book started and updates his page each day; Notion is canonical for what book and edition he is reading. This job copies the Goodreads events into Notion every night so the library gets real Started dates and a per-read progress stream.

## What it does

`goodreads_sync.py` runs nightly, as a Claude Code cloud routine (see Setup) or, as a fallback, under launchd on the Mac (plist in this folder):

1. Fetches Dan's Goodreads **updates RSS** (the activity stream, where "started reading", "is on page N of M", "is N% done" and "finished reading" items appear). The official Goodreads API was retired in 2020; the per-user RSS feeds are what remains, and their wording is not documented, so the parser keeps each item's raw sentence on the event.
2. Skips items it has seen (ids in `state.json`), then matches each new event to a Notion **Book** by title, tie-breaking on author surname, and to that Book's **open Read** (no Outcome yet). It never creates Books or Reads.
3. Appends one row per event to the **Reading log** database (append-only: this is the event stream the Reads rows summarise) and, for a Started event on a Read whose Started is blank, sets Started and clears Dates estimated.
4. Compares the Goodreads **currently-reading shelf** with Notion's open Reads, both ways, and reports every difference (for example "Goodreads: currently reading Lonesome Dove since Oct 9, but Notion's last Read of it is Finished, ended 2026-06-30").
5. Writes anything it couldn't match, and the shelf differences, to `unmatched.json` and the log, for Dan to resolve by chat (usually "Goodreads says you started X; log it in Notion").

Only the public feed URL goes to Goodreads, with a generic User-Agent. Nothing personal leaves the machine.

## What the feeds do and don't carry (checked 2026-10-16 against Dan's account)

- **Updates feed** (`/user/updates_rss/<id>`): "started reading", "is on page N of M" (with the note Dan typed), "finished reading", "did not finish", "wants to read", plus likes of other people's updates (ignored). Only the most recent items; polled nightly.
- **Shelf feeds** (`/review/list_rss/<id>?shelf=…&per_page=200&page=n`): every book on a shelf with `user_read_at` (finished date), `user_date_added`, `user_date_created` (when the review row was made), author, ISBN. No Started date. The read shelf had 221 items on 2026-10-16.
- **Review pages** (`/review/show/<id>`), which show the full Reading progress timeline with the Started date and every page update, redirect anonymous requests to sign-in, so a script can't read them. Backfilling the full history would need Dan signed in to Goodreads in the Claude browser pane, with Claude reading each review page there (one-time, 221 pages).
- A cheaper partial backfill: for reads where Dan shelved the book as currently-reading when he started it, `user_date_created` on the read shelf is the day he started. Where it falls within a plausible window before `user_read_at` it can fill a blank or estimated Started; bulk-imported rows (created long before or on the same day as the finish) are skipped.

## Reading log schema (Notion, under Media › Library)

| Property | Type | Notes |
|---|---|---|
| Name | title | `<Book> · <Kind> [p.N] · <date>` |
| Read | relation → Reads | the reading episode this event belongs to |
| Book | relation → Books | |
| When | date (with time) | the Goodreads timestamp |
| Kind | select: Started / Progress / Finished / DNF | |
| Page | number | from "page N of M" |
| Of pages | number | the M; Goodreads' page count for its edition, not necessarily Dan's copy |
| Percent | number | from "N% done", or derived from page/of pages |
| Source | select: Goodreads / Manual | |
| Goodreads id | text | the feed item id; unique per event, used to dedupe |
| Raw | text | the feed sentence as received |

Reads.Pages read stays Dan's own figure (pages actually read, indexes skipped); Page here is where he was on a given day.

## Setup (once)

1. **Notion token.** Dan already has an internal integration (made for the old reading-list CLI; its token is `NOTION_API_KEY` in `~/claude/.env`). Reuse it: in Notion open each of **Books, Reads, Authors, Reading log** › `···` (top right) › **Connections** › add that integration. Or make a new one at notion.so/my-integrations (Internal, read + insert + update content) and connect it the same way. The token is the value starting `ntn_` or `secret_`.
2. **Reading log database.** Exists since 2026-10-16 under Media › Library (data source `ced88101-f6c5-4e4e-9c1f-8305afeb2e7e`, the script's default).
3. **Cloud routine (preferred).** In claude.ai/code create an environment for `minikdj/reading-library-dashboard` with environment variables `NOTION_TOKEN` and `GOODREADS_USER_ID=151183858`; the routine runs `python3 sync/goodreads_sync.py` nightly and commits nothing. `unmatched.json` is printed in the run log, since the cloud checkout is thrown away.
4. **Local fallback (launchd).** `cp sync/.env.example sync/.env`, fill in `NOTION_TOKEN`, then:
   ```bash
   cp sync/com.dan.reading-library.goodreads-sync.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.dan.reading-library.goodreads-sync.plist
   ```
5. **Checks before trusting it.** `--parse-only` prints the parsed events; `--fixture … --shelf-fixture … --offline-library data/raw` matches against the repo's raw pull with no token; `--dry-run` matches against Notion and prints the writes without saving.

## Day to day

- Log: `~/Library/Logs/goodreads-sync.log`. State: `sync/state.json`. Unmatched events: `sync/unmatched.json`.
- `python3 sync/goodreads_sync.py --fixture sync/fixtures/updates_sample.xml --parse-only` exercises the parser offline.
- A missed night (Mac asleep) runs at the next wake. The updates feed only keeps recent items, so a Mac off for many days can miss progress updates; Started and Finished still arrive as long as the item is within the feed window.
- To stop: `launchctl unload ~/Library/LaunchAgents/com.dan.reading-library.goodreads-sync.plist`.
