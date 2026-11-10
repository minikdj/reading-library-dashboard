# Filling a gap in the Reading log by hand

Since 2026-11-10 the nightly sync reads two public Goodreads pages, anonymously: the status list, which keeps **every** page update with its time, and the shelf widget, which has **no dates**. So page updates no longer go missing, but a start, finish or DNF found on a shelf gets an estimated date (the most recent evening; the Read's Notes and its Pages read question say "estimated"). The exact days live on the review page's **Reading Progress** timeline, which needs a sign-in. This is how to read them, on request, from Dan's own machine. The job itself never fetches review pages and never holds Goodreads credentials.

(Before 2026-10-28 the job read the RSS feeds, whose updates feed kept about nine items, and this procedure filled lost page updates. Goodreads retired the feeds that day.)

## When it is worth doing

Only when an estimated start or finish date matters to Dan (reading speed, a year boundary), or a Gap question names a span the job did not read. A question with `Confirm` ticked means "doesn't matter"; nothing else happens. For an estimated finish, the quicker fix is answering the Pages read question with `ended YYYY-MM-DD`.

## Procedure (Claude, in a desktop session with the browser pane)

1. Find the span: the Gap question names it, and `sync/unmatched.json` (or the run's artifact in GitHub Actions) shows the feed window of each run.
2. Open the review page of each affected book in the **built-in browser pane**, signed in as Dan: `https://www.goodreads.com/review/show/<review id>`. The review id is in the Reading log row's `Raw` text for shelf-derived rows, in the Sync run rows, or on Goodreads under the book's "My activity". Do not fetch the page from the shell; anonymous requests redirect to sign-in, and the sign-in stays in the pane.
3. Read the **Reading Progress** timeline on the page (the same rows `sync/backfill.py` parses: `October 4, 2026 – page 45 4.67%`, `– Started Reading`, `– Finished Reading`, `Shelved as: did-not-finish`). Copy the rows for the span into a JSON file `sync/backfill/timelines/batch_gap_<date>.json` in the backfill's shape: `[{"id": "<review id>", "title": "...", "authors": ["..."], "status": 200, "rows": ["October 4, 2026 – page 45 4.67%", ...]}]`.
4. Build the plan against a fresh raw pull (`docs/REFRESH.md` §1) or the current one: `python3 sync/backfill.py --timelines sync/backfill/timelines --out sync/backfill/plan_gap.json`. The plan's rows carry `Goodreads id` = `Timeline<review>:<n>` and Eastern dates, one decimal percent.
5. Apply: `python3 sync/backfill.py --apply --out sync/backfill/plan_gap.json --dry-run`, check the count, then without `--dry-run`. `--apply` skips any row already in the log by Goodreads id **or** by the secondary key (Read + Kind + date + Page), so rows the feed did catch are not doubled.
6. Tick `Confirm` on the Gap question (or say "confirm N" in chat).

## Dedupe keys, for the record

| Key | Where it comes from |
|---|---|
| `Goodreads id` = `ReadStatus…` / `UserStatus…` | the updates feed until 2026-10-28; `UserStatus…` from the status list since (the sync) |
| `Goodreads id` = `Shelf<review>:<kind>:<date>` | a shelf item inside the fence (the sync) |
| `Goodreads id` = `Timeline<review>:<n>` | a review page's timeline (the backfill, this procedure) |
| `Goodreads id` = `Backfill:…` | rows added by hand in chat on 2026-10-17 |
| Read + Kind + Eastern date + Page | the secondary key every writer checks before creating a row |
