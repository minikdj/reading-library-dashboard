# Handoff for a claude.ai chat

Paste everything below the line into a claude.ai chat, or into a Claude Code web session opened on `minikdj/reading-library-dashboard`.

---

I'm Dan. This is my reading-library dashboard, my Goodreads replacement. Notion holds the data, the private GitHub repo `minikdj/reading-library-dashboard` (branch `master`) holds the pipeline and the dashboard page, and the claude.ai Dashboard artifact https://claude.ai/artifact/MWyZwTrUV7bjpk2nNHTn6G shows it. **The repo is the source of truth and the artifact is a published copy of it.** Keep them in sync: every change is made in the repo, committed, then published from the repo. Never edit the artifact's store without committing the same change.

## 1. Get up to speed

- Read the repo: `CLAUDE.md`, `README.md`, `docs/SPEC.md`, `docs/brief.md` (the counting rules). If you can't read the repo in this session (no GitHub access), tell me before doing anything else.
- Read the Notion runbook page `3fb1b15c8b7781409d37e348858b0b04` ("Library runbook (for Claude)") through the Notion connector before touching data. It is the source of truth for the data model and procedures.
- Ground rules (also in `CLAUDE.md`): Notion is read-only unless I ask, and trashing pages needs my OK on the exact list. Never touch the archived Old Reading List (`collection://bae2599f-4319-42a8-9f23-89919c21e23e`) and never use any local `notion-reading-list` tools. Never send my email or personal details to an outside service. Pull data into files and compute in Python; don't page result sets through the chat. If a number disagrees with the sanity checks, tell me instead of smoothing it over.

## 2. The dashboard today (commit `b11592a`, 2026-10-16)

- The page is `dashboard/index.html`: plain HTML and d3 on the Dashboard type's `dash` API. Every number comes from one of the 17 attached datasets in `data/out/ds/`; every caption figure is a `headline` span, so nothing is typed into the markup. `pipeline/analyze.py` is the rules as code and prints the sanity numbers; `pipeline/bundle.py` folds its output into the datasets; `dashboard/datasets.json` holds each dataset's title, description and blob id.
- Interactions: click a bar (or one segment of a stacked bar) and a translucent panel covers that card with the data behind it: the list of reads for time, genre, form, century, era, author and translator charts; counts, shares, pages and years at pace for the copies, shelf, Kindle and backlog charts. A grey × or Escape closes it. On the six time charts, click a year on the x-axis to see its months and a month to see its Monday-start weeks; the trail above the chart climbs back up.
- Sanity numbers for the 2026-10-16 pull in `data/raw/`: 230 finished reads, 2 DNF, 1 reading; pages by year 2,383 / 23,363 / 27,334 / 11,760 / 17,445 / 19,545; 400 paper + 201 Kindle copies; 37 Classic / 287 Canonical work; 7 books with no genre; 43 with no pages. A fresh pull will differ slightly (later Notion edits: the Herodotus and Bible merges, the Steve 100 and Western Canon tags); say what changed.

## 3. Publishing a change (the artifact can't read the repo itself)

The artifact's page is the `files/index.html` document in its own store and its datasets are uploaded files, so nothing on it updates by itself. The steps, from `README.md` "Refreshing the dashboard":

1. **Drift check first.** Read `files/index.html` from the artifact's store (`ArtifactData` get) and compare its `text` with the committed `dashboard/index.html`. If they differ, the store holds edits that aren't in git: tell me and stop rather than overwriting.
2. Make the change in the repo and commit it with a message saying what and why.
3. If the data should refresh: pull the four databases into `data/raw/*.json` (SQL mode, 100 rows per call, raw columns only), then `python3 pipeline/analyze.py` and check the printed sanity numbers, then `python3 pipeline/bundle.py`.
4. For each changed `data/out/ds/<id>.json`: upload it as an artifact asset, put the new blob id in `dashboard/datasets.json`, delete the old blob.
5. `python3 dashboard/make_docs.py`, then one `ArtifactData` batch write of the changed documents (`files/index.html`, each changed `datasets/<id>`), pinning `if_version` from a fresh read of each.
6. Commit the blob ids and push. Then read the dashboard's `data/users/me` `check` document once: no document means the page threw nothing.

Acceptance for any change (`docs/SPEC.md` §10): sanity numbers still match or the difference is explained; every new number is marked with its dataset and captioned for its gaps; the page works at phone width and full width, light and dark; the change is a commit.

## 4. What I'm likely to ask for next

- A scheduled refresh (weekly pull → analyze → publish from `master`), so the artifact follows the repo without a chat.
- Logging reads, purchases and wishlist adds by chat (the `reading` skill), feeding the dashboard.
- A yearly goal tile and a "what to read next" view ranked by list membership, era, length and shelf.
- The dataset descriptions in `dashboard/datasets.json` still hard-code a few counts (`kindle_by_quarter`, `reads_log`, `by_year`); make them generic.
- I don't care about backfilling Started dates on past reads; per-read speed is a going-forward feature only.

Ask me what I want to change first.
