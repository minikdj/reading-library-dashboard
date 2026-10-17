# Handoff prompt for Claude Code

Paste everything below the line into a new Claude Code session on your Mac.

---

I'm Dan. I'm moving my reading-library project into this environment so I can iterate on it with you. It is my Goodreads replacement: Notion holds the data, a Python pipeline computes the stats, and a claude.ai Dashboard artifact shows them. Set it up, get up to speed, and then we'll iterate on how the dashboard works.

## 1. Put the repo in place

- The project is in `~/Downloads/reading-library-dashboard.tar.gz`. It already contains a git repo with one commit.
- Extract it into `~/Projects/reading-library-dashboard` (create `~/Projects` if it doesn't exist; if I keep projects elsewhere I'll say so). Don't leave a copy in Downloads once it's extracted.
- Confirm `git log` shows the initial commit and `git status` is clean. Create the GitHub remote `minikdj/reading-library-dashboard` (private) with `gh repo create --private --source . --push` if `gh` is authenticated; if it isn't, tell me and skip the push.
- Run `python3 pipeline/analyze.py` and `python3 pipeline/bundle.py` from the repo root and show me the sanity numbers it prints. They should match: 230 finished reads, 2 DNF, 1 reading; pages by year 2,383 / 23,363 / 27,334 / 11,760 / 17,445 / 19,545; 400 paper + 201 Kindle copies; 37 Classic / 287 Canonical work; 7 books with no genre; 43 with no pages. (Note: the raw pull in `data/raw/` is from 2026-10-16 and predates a few later Notion edits — the Herodotus and Bible merges and the Steve 100 / Western Canon tags — so a fresh pull will differ slightly; that's expected.)

## 2. Get up to speed

- Read `CLAUDE.md`, then `README.md`, then `docs/SPEC.md`, then `docs/brief.md` (the original brief with the counting rules).
- Read the Notion runbook page `3fb1b15c8b7781409d37e348858b0b04` ("Library runbook (for Claude)") through the Notion MCP connector. It is the source of truth for the data model and procedures. If the Notion connector isn't available in this session, tell me and we'll add it before touching data.
- Skim `pipeline/analyze.py` (the rules as code) and `dashboard/index.html` (the page) so you can explain how a number on the dashboard is produced.
- Then give me a short summary: what's in the repo, what the dashboard shows, the three data caveats that most limit it, and the open items from the spec.

## 3. Ground rules (also in CLAUDE.md)

- Notion is read-only unless I ask for a change; trashing pages needs my OK on the exact list.
- Never touch the archived Old Reading List (`collection://bae2599f-4319-42a8-9f23-89919c21e23e`) or the local `notion-reading-list` MCP tools.
- Never send my email or personal details to an outside service.
- Pull data into files and compute in Python; don't page result sets through the chat. Check fill rates before charting a field and caption the gaps.
- If a number disagrees with the sanity checks, tell me instead of smoothing it over.

## 4. How publishing works

The dashboard page and datasets live in the artifact's own store, written with the `Artifact` and `ArtifactData` tools. Those tools exist in claude.ai sessions and Claude Code on the web, not in a terminal session. So: edit `dashboard/index.html` and the pipeline here, commit, and when a change is ready to publish either (a) do it from a claude.ai chat with this folder connected, or (b) tell me and I'll open the repo in a web session. `README.md` "Refreshing the dashboard" lists the exact steps; `dashboard/make_docs.py` prepares the store documents. If you do have the Artifact tools in this session, the artifact URL is in `dashboard/datasets.json`.

## 5. Then

Ask me what I want to change first. Likely candidates are on the roadmap in README.md and the open items in docs/SPEC.md.
