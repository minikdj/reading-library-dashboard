# Working in this repo

This is Dan's reading library: Notion is the data store, this repo holds the analysis pipeline and the dashboard page, and the dashboard itself is a claude.ai Dashboard artifact (URL in README.md). The goal is a Goodreads replacement that improves by request.

## Ground rules

- **Read the Notion runbook first** (page `3fb1b15c8b7781409d37e348858b0b04`, "Library runbook (for Claude)"). It is the source of truth for the data model, counting rules and procedures; README.md only summarizes it.
- **Notion is read-only unless Dan asks for a change.** When he does, follow the runbook's procedures (one Book per work, one Edition per copy, Reads as the event log, derived fields never set by hand). Trashing pages needs his explicit OK on the exact list.
- **Never touch the archived Old Reading List** (`collection://bae2599f-4319-42a8-9f23-89919c21e23e`) and never use the local `notion-reading-list` MCP tools.
- **Privacy:** external lookups (Open Library etc.) must never carry Dan's email or any personal detail in a header, URL or payload. Use curl's default User-Agent.
- **Pull data into files and compute in Python**; don't page result sets through the chat. SQL returns at most 100 rows per call; formula/rollup columns are invisible to SQL.
- **Check fill rates before charting a field** and say the gaps in the chart's caption. If a number disagrees with the runbook's sanity checks, say so rather than smoothing it over.
- Genres: Classic / Canonical work rules in the runbook (Genre system v2). Claude-assigned genres set Genre auto ✓; never change Dan's unchecked genres.

## Dashboard

- `dashboard/index.html` runs inside the Dashboard artifact type: plain HTML + d3 v7, data via `dash.data(id)` / `dash.onData`, every number marked with `data-source`. No numbers in markup; every figure comes from a dataset file.
- Datasets are attached JSON files (not live queries, because the Notion connector caps SQL at 100 rows and cross-database joins need a higher plan). See README.md "Refreshing the dashboard".
- Keep fiction and non-fiction genre charts separate (Dan's request); "Literary fiction" is the catch-all for canonical novels with no sharper genre.
