# Reading library

Dan's reading library: a Notion-backed replacement for Goodreads, with a dashboard that is iterated on by asking Claude for changes rather than by shipping an app.

- **Data store:** four Notion databases — Authors, Books (one row per work), Editions (one row per copy), Reads (one row per reading event). The rules live in the Notion page **"Library runbook (for Claude)"** (`3fb1b15c8b7781409d37e348858b0b04`); read it before touching anything.
- **Dashboard:** a claude.ai Dashboard artifact, https://claude.ai/artifact/MWyZwTrUV7bjpk2nNHTn6G. Its page is `dashboard/index.html`; its numbers are attached JSON files produced by the pipeline here.
- **Lists:** Books.Lists holds Goodreads WTR (the old want-to-read shelf), Steve 100 and Western Canon (two lists from Steve), Up next.

## Layout

```
pipeline/analyze.py    raw Notion pulls -> data/out/*.json (every stat, sanity checks printed)
pipeline/bundle.py     data/out/*.json  -> data/out/ds/*.json (the files the dashboard attaches)
pipeline/lists.py      matches Steve's lists against Books; duplicate-work checks
dashboard/index.html   the dashboard page (d3 v7 + the Dashboard type's `dash` API)
dashboard/datasets.json  dataset titles, descriptions and the artifact blob id each one points at
dashboard/make_docs.py   writes the store documents for an ArtifactData batch write
data/raw/              books, editions, reads, authors as pulled from Notion (2026-10-16)
data/out/              computed stats; data/out/ds/ the attached datasets
docs/brief.md          the original brief and counting rules
docs/CHAT_HANDOFF.md   paste-ready brief for a claude.ai chat (publishing procedure, sync rule)
docs/lists/            Steve's lists as matched, the tag plan, the missing-works research
```

## Refreshing the dashboard

The repo is the source of truth and the artifact is a published copy: the artifact's page is the `files/index.html` document in its own store and its datasets are uploaded files, so nothing on it reads the repo. Change the repo, commit, then publish. Before overwriting `files/index.html`, read it from the store and compare its `text` with the committed file; a difference means the store has edits that aren't in git. `docs/CHAT_HANDOFF.md` is the paste-ready brief for a claude.ai chat.

1. **Pull** the four databases into `data/raw/*.json` with the Notion connector's SQL mode, 100 rows per call (`ORDER BY url LIMIT 100 OFFSET n`), selecting only the raw columns `analyze.py` reads. Formula and rollup columns are not visible to SQL; the pipeline rebuilds them from raw columns. Keep each page's rows verbatim.
2. `python3 pipeline/analyze.py` — check the printed sanity numbers against the runbook (230 finished reads, pages by year, 400 paper + 201 Kindle, 37 Classic / 287 Canonical work, 43 books without pages, as of 2026-10-16).
3. `python3 pipeline/bundle.py`.
4. Upload each changed `data/out/ds/<id>.json` as an artifact asset (`Artifact` publish with `asset: true`), put the new blob id in `dashboard/datasets.json`, delete the old blob.
5. `python3 dashboard/make_docs.py`, then an `ArtifactData` batch write of `dashboard/docs/batch.json` to the artifact (pin `if_version` on every existing document).

Steps 1, 4 and 5 go through Claude's tools, not the shell.

## Counting rules (short form; the runbook is authoritative)

- Read = Outcome Finished. DNF never counts. "Books read" = distinct works; "reads" = events.
- Pages per read = Pages read if filled, else the largest linked-edition page count, else the Book's, else 0.
- Owned now = Owned ✓ and no Disposed date. Editions are copies, never books.
- Status precedence: Reading > Read > Abandoned > Unread (owned) > Previously owned > Wishlist.
- Classic = Greek and Roman antiquity to Augustine; Canonical work = other pre-1970 canon plus four exceptions; canon tags always sit beside content genres.

## Data caveats

- Dates estimated is ✓ on 229 of 230 finished reads: no per-read reading speed exists; pace is pages per calendar day.
- Acquired dates exist for Kindle copies only; 52 share 2023-11-15.
- 43 Books have no page count; 7 have no genre; genre tags on 925 of 1,064 books are Claude's (Genre auto ✓).
- History starts Aug 2021.

## Roadmap (ask for any of these)

- Scheduled refresh of the dashboard (weekly pull + republish).
- Logging reads and purchases by chat (the `reading` skill already does this against Notion).
- Goodreads features still missing: ratings/reviews per read (Read Notes exists), reading challenges and yearly goals, shelves beyond Lists, a "what to read next" view ranked by list membership, era and length.
- Per-read reading speed once Started dates are recorded going forward.
- Covers (Books.Cover via Open Library by ISBN).
