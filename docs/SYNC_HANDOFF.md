# Handoff: build and test the self-correcting Goodreads → Notion sync

Paste everything below the line into Claude Code, opened in `~/Projects/reading-library-dashboard`.

---

I'm Dan. `docs/SYNC_REVIEW.md` is a reviewed, decided specification for `sync/goodreads_sync.py`. I want it built and tested end to end, with me in the loop only where the spec says so. Work in this repo on `master`, one commit per step, tests first for each step. Commit `docs/SYNC_REVIEW.md` and this file as your first commit.

## Read first
1. `CLAUDE.md`, `README.md`, `docs/SPEC.md`, `docs/brief.md`.
2. `docs/SYNC_REVIEW.md` in full — the findings, then every section headed "(Dan, 2026-10-17)". Where the review supersedes itself (the "Events, not state" section replaces the earlier unbounded shelf comparison; "No Goodreads credentials" is final), the later section wins.
3. The Notion runbook page `3fb1b15c8b7781409d37e348858b0b04` through the Notion connector. Note the Reading log already has 630 rows (618 `Timeline…` ids from the backfill plus 12 `Backfill:…` ids added by chat on 2026-10-17; the latter need the secondary dedupe key).
4. `sync/goodreads_sync.py`, `sync/backfill.py`, `sync/README.md`, the fixtures in `sync/fixtures/`.

## Ground rules
- Notion is written only as the spec allows; the four invariants under "Dan's decisions" are hard rules. Never trash a page. Never store Goodreads credentials anywhere.
- My email or personal details never go into a request header, URL or payload. Goodreads is fetched anonymously with the generic User-Agent.
- Every automatic change is written to the Read's Notes with a date and "from Goodreads". Every uncertain case is a Sync queue question with a default applied, never a guess without a record.
- Tests before code for each step; `python3 -m unittest discover sync` must pass before every commit.
- If anything in the spec is contradictory or impossible against the real Notion schema or the real feeds, stop and tell me rather than choosing silently.

## Build order (one commit each; the review's "Suggested order of work" and later sections)
1. Mechanical fixes: `read_norm` carries Book and Dates estimated; the Notion-side reading check is live; Started replaces an estimated date; Eastern-time date slicing; one-decimal percent; stale `book_cache` entries dropped; secondary dedupe key (Read + Kind + date + Page) everywhere a row is created, including `backfill.py --apply`.
2. Sync queue: create the Notion database under Media › Library (schema in "Questions for Dan" plus `Default applied`, `Proposed`, `Confirm`); `--init-queue`; pending events retried from it each run; answers and `Confirm` ✓ applied at the start of a run; `unmatched.json` becomes a mirror of the open rows. Watermark stored as a `Sync run` row there.
3. Watermark fence: only events after the watermark (−48 h); fenced shelf reconciliation for currently-reading / read / did-not-finish; gap check; feed archive to `sync/feed_archive/` (git-ignored). **Ask me before starting step 4.**
4. Reads: create on Started/shelf with the edition default and an always-asked question; close on Finished/DNF with an always-asked question; reopen within 14 days of a sync close; the stray-Finished skip; the >14-day reread path; Goodreads edition signals → question only, closed Reads' Editions never touched.
5. Wishlist: `wants to read` events and fenced to-read shelf items → Goodreads WTR + Wishlisted, Book created per the runbook when missing; removals reported only.
6. Hardening: fetch retries, partial-run behaviour, `last_success`, non-zero exit only when nothing could be done; `--dry-run` reads Notion for the existence check.
7. GitHub Actions: `.github/workflows/goodreads-sync.yml` (nightly cron ≈ 23:45 Eastern, `workflow_dispatch` with a `dry_run` input, unit tests then the sync). Tell me the exact secret names to add; I'll add them. Keep the launchd plist only as a documented manual fallback.
8. `reading` skill and docs: the three phrases ("anything to resolve?", "confirm all [except N]", "N: <answer>"); a "Sync" section added to the Notion runbook (ask me before writing to it); `docs/GAP_FILL.md` for the manual timeline fill; README updated.
9. Dashboard: an "Unconfirmed" tile from a new `sync_queue` dataset (open count, click-through list with defaults), published per README "Refreshing the dashboard".

## Testing (the review's "Full-cycle test plan", steps 1–4 are yours)
- Unit tests for every rule listed there, idempotence, the fence and the question lifecycle.
- Offline replay of `sync/fixtures/live_*.xml` against `data/raw` must match a committed `sync/fixtures/expected_live.json`.
- `--dry-run` against the real Notion: zero writes, all 630 existing rows recognised.
- A `workflow_dispatch` dry run must pass in the job log before you hand steps 5–7 to me.

When steps 1–4 pass, give me the three live-cycle scripts (parts 1–3 in the plan) as a checklist I can follow on Goodreads, with the exact command or workflow button to press after each, and what I should see in Notion and in chat. Then we run them together.

## What I will say yes/no to along the way
- Before step 4 (creating/closing Reads goes live).
- Before writing the Sync section into the runbook.
- The secret names and the first `workflow_dispatch` run.
- Anything you want to delete, rename or restructure in Notion.
