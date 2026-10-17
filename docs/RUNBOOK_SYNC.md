# Runbook section: Sync (draft, to add to the Notion runbook once Dan approves)

Insert as a new `## Sync (Goodreads → Notion, nightly)` section after "## Procedures" in the "Library runbook (for Claude)" page.

---

## Sync (Goodreads → Notion, nightly)

A GitHub Actions job (`minikdj/reading-library-dashboard`, `sync/goodreads_sync.py`, spec `docs/SYNC_REVIEW.md`) reads Dan's public Goodreads feeds every night at about 23:45 Eastern and writes the **events** into Notion. Goodreads is never a source of truth about the past: only events after the last run (less 48 h) are written, and shelf state is consulted only inside that fence.

- **Reading log** (`collection://ced88101-f6c5-4e4e-9c1f-8305afeb2e7e`): one row per event (Started / Progress / Finished / DNF) linked to its Read and Book, deduplicated by `Goodreads id` and by Read + Kind + Eastern date + Page. Reads.Pages read stays Dan's figure; the log's Page is where he was that day.
- **Reads the sync changes, and nothing else:** it creates an open Read on a Goodreads start (`<Title> · <year>`, Edition = the only owned copy or the last Read's, `Edition inferred` ✓); closes one on Finished / DNF (Ended, Outcome, Pages read on DNF only); reopens one it closed itself when progress continues within 14 days; fills a blank or estimated Started. Every change is a dated "from Goodreads" line in the Read's Notes. It never deletes, never touches a Book's genres, an Edition, or a closed Read's copy, never changes an Ended or a Pages read Dan set.
- **Wishlist:** "wants to read" tags the Book (Lists Goodreads WTR, Wishlisted) and creates Author + Book (Source Goodreads, no Edition, genres blank) when missing. Removals from the Goodreads shelf are reported, never untagged.
- **Sync queue** (`collection://4e8f45b7-8f9c-4018-8108-7eff25029227`, view "Open questions"): every uncertain case, with the default already applied (`Default applied` ✓, value in `Proposed`). Edition and Pages read are asked on every Read the sync creates or closes. Rows stay Open until Dan ticks `Confirm` (keeps the default) or writes an `Answer` (a copy, a page count, `ended YYYY-MM-DD`, `reopen` / `reread`, `still` / `finished YYYY-MM-DD`, a Book or Read title, `drop`); the next run applies it and marks the row Answered. By chat: "anything to resolve?", "confirm all [except N]", "N: <answer>". `Sync run` rows are the job's log and watermark; never delete rows.
- **Gaps:** the updates feed keeps only a few days; when it dropped items the job raises a Gap question and the shelves still recover starts and finishes. Page-by-page progress for the span is filled by hand from the review pages, signed in, per `docs/GAP_FILL.md` (ids `Timeline<review>:<n>`).
- **Claude in chat:** log reads as before; the sync dedupes against what chat wrote. When closing or reopening a Read the sync made, add a dated "by Dan in chat" line to Notes so the two can be told apart. Never create Sync queue rows by hand, never set their Status.
