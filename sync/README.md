# Goodreads → Notion nightly sync

Goodreads is where Dan marks a book started and updates his page each day; Notion is canonical for what book and edition he is reading. This job copies the Goodreads events into Notion every night so the library gets real Started dates and a per-read progress stream.

## What it does

`goodreads_sync.py` runs at 23:45 under launchd (plist in this folder):

1. Fetches Dan's Goodreads **updates RSS** (the activity stream, where "started reading", "is on page N of M", "is N% done" and "finished reading" items appear). The official Goodreads API was retired in 2020; the per-user RSS feeds are what remains, and their wording is not documented, so the parser keeps each item's raw sentence on the event.
2. Skips items it has seen (ids in `state.json`), then matches each new event to a Notion **Book** by title, tie-breaking on author surname, and to that Book's **open Read** (no Outcome yet). It never creates Books or Reads.
3. Appends one row per event to the **Reading log** database (append-only: this is the event stream the Reads rows summarise) and, for a Started event on a Read whose Started is blank, sets Started and clears Dates estimated.
4. Writes anything it couldn't match to `unmatched.json` and the log, for Dan to resolve by chat (usually "Goodreads says you started X; log it in Notion").

Only the public feed URL goes to Goodreads, with a generic User-Agent. Nothing personal leaves the machine.

## A better source: the review page timeline

Each Goodreads review page (`goodreads.com/review/show/<id>`) shows a **Reading progress** timeline: Started Reading date, every "page N" update with its date and note, Finished Reading, and shelvings such as did-not-finish. That is the complete per-read history, whereas the updates RSS only keeps recent items. Once Dan's profile URL is known and a review page has been fetched to see its markup, the plan is:

- nightly: take the currently-reading shelf RSS (review links), fetch those few review pages, parse the timeline, and log any event not yet in the Reading log; keep the updates feed as a cross-check;
- once, on Dan's say-so: walk the read shelf (RSS, paged), fetch each review page politely (one request every few seconds), and backfill Started dates and progress history for past reads. The Goodreads CSV export never carried Started dates, which is why Notion has none.

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

1. **Integration.** Use the existing Notion integration from the CLI project (its token is in `~/claude/.env`) or create one at notion.so/my-integrations. In Notion, open each of Books, Reads, Authors and Reading log › `···` › Connections › add the integration.
2. **Reading log database.** Create it under the Library page with the schema above (Claude can do this through the Notion connector on Dan's OK). Put its data source id in `.env` as `NOTION_LOG_DS`.
3. **Config.** `cp sync/.env.example sync/.env` and fill in `NOTION_TOKEN`, `NOTION_LOG_DS`, `GOODREADS_USER_ID` (and `GOODREADS_RSS_KEY` if the profile is private).
4. **Verify the feed.** `python3 sync/goodreads_sync.py --parse-only` prints the events parsed from the live feed. If a sentence isn't recognised, add its pattern to `PATTERNS`.
5. **Dry run.** `python3 sync/goodreads_sync.py --dry-run` matches against Notion and prints the writes without saving.
6. **Install the job.**
   ```bash
   cp sync/com.dan.reading-library.goodreads-sync.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.dan.reading-library.goodreads-sync.plist
   ```
   Run it once by hand: `launchctl start com.dan.reading-library.goodreads-sync`.

## Day to day

- Log: `~/Library/Logs/goodreads-sync.log`. State: `sync/state.json`. Unmatched events: `sync/unmatched.json`.
- `python3 sync/goodreads_sync.py --fixture sync/fixtures/updates_sample.xml --parse-only` exercises the parser offline.
- A missed night (Mac asleep) runs at the next wake. The updates feed only keeps recent items, so a Mac off for many days can miss progress updates; Started and Finished still arrive as long as the item is within the feed window.
- To stop: `launchctl unload ~/Library/LaunchAgents/com.dan.reading-library.goodreads-sync.plist`.
