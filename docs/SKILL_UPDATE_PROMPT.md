# Prompt for Claude chat: update the `reading` skill

Paste everything below the line into a claude.ai chat (the one with the skill-editing ability, or Settings › Capabilities › Skills › reading › edit, pasting the four pieces by hand). The full updated skill is `skills/reading/SKILL.md` in the repo; this prompt carries only the changes.

---

Update my existing skill named **reading** (do not create a new one). Keep everything in it as it is and make these four changes, exactly:

1. In the "Databases" table, add these two rows after the Reads row:

| Reading log | `collection://ced88101-f6c5-4e4e-9c1f-8305afeb2e7e` | **progress event** from Goodreads (Started / Progress / Finished / DNF); written by the nightly sync, read-only for you |
| Sync queue | `collection://4e8f45b7-8f9c-4018-8108-7eff25029227` | **question** from the nightly sync, with its default already applied |

2. In the "Workflows" section, add this step before step 1 of **"I started X"**:

0. If he also marks books on Goodreads, the nightly sync will create the Read itself from the "started reading" event (with the copy as the only owned one or the last Read's, and an Edition question). Logging it here first is fine too: the sync then only adds the Started row and fills the date. Never create a second Read for the same start.

3. Insert this new subsection immediately before the **"### Questions"** subsection:

### The sync's questions ("anything to resolve?", "confirm all", "N: <answer>")
The nightly Goodreads sync (repo `sync/goodreads_sync.py`) creates and closes Reads from Goodreads and asks a question for every uncertain case in the **Sync queue**, with the default already applied in Notion (`Default applied` ✓, the value in `Proposed`). Rows stay `Open` until Dan confirms or answers; the sync never confirms for him. The sync applies answers at the start of its next run (nightly, or when the GitHub Actions workflow *Goodreads sync* is run by hand).

- **"anything to resolve?"** (or "any sync questions?", "what's unconfirmed?"): query the Sync queue for rows with `Status` = Open and `Kind` ≠ Sync run, sorted by `Asked` descending, and list them numbered, one line each: `N. [Kind] Question — default: Proposed`. Show the `Options` only when he asks what he can answer. Keep the numbering for the rest of the conversation. "Nothing to resolve" when there are none. Also mention, if `sync/unmatched.json` in the repo is at hand, the Books it reports as removed from the Goodreads want-to-read shelf.
- **"confirm all"** / **"confirm all except 3, 5"**: set `Confirm` = `__YES__` on every listed Open row except the excluded numbers, one `notion-update-page` call per row (parallel calls are fine). Reply with how many were confirmed and that the next run applies them (Edition inferred cleared and "Confirmed by Dan" noted on confirmed editions).
- **"N: <answer>"** (e.g. "2: Paper", "4: 858", "4: ended 2026-10-14, 858", "1: reopen", "3: finished 2026-10-15 pages 300", "6: drop"): set `Answer` on row N to the text as given. The sync parses it on the next run; the accepted forms per Kind are in `sync/README.md` ("Questions"). `drop` dismisses a row.
- Never set `Status`, `Resolved`, `Proposed` or `Default applied` yourself; the sync does. Never create Sync queue rows by hand. If he wants the answer applied now rather than tonight, do the Notion change yourself by the workflows above **and** set `Answer` so the row is closed on the next run.
- To see what the sync did: the Read's `Notes` carries one dated line per automatic change ("Created from Goodreads …", "Closed from Goodreads …", "Reopened from Goodreads …", "Started … from Goodreads …"); the Reading log holds the events.

4. In "After writing", add this bullet after the one that begins "Reply in one or two lines":

- When you close or reopen a Read that the sync created or closed, append a dated line to its `Notes` ("Closed by Dan in chat 2026-10-17") so the sync can tell your changes from its own.

Then show me the skill's "The sync's questions" subsection as saved, so I can check it.
