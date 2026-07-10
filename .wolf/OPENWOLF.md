# OpenWolf Operating Protocol

You are working in an OpenWolf-managed project. These rules apply every turn.

## File Navigation

1. Check `.wolf/anatomy.md` BEFORE reading any file. It has a 2-3 line description and token estimate for every file in the project.
2. If the description in anatomy.md is sufficient for your task, do NOT read the full file.
3. If a file is not in anatomy.md, search with Grep/Glob, then update anatomy.md with the new entry.

## Code Generation

1. Before generating code, read `.wolf/cerebrum.md` and respect every entry.
2. Check the `## Do-Not-Repeat` section — these are past mistakes that must not recur.
3. Follow all conventions in `## Key Learnings` and `## User Preferences`.
4. Ground domain terms in root `CONCEPTS.md` — use the project's vocabulary, do not invent synonyms for terms already defined there.

## Domain Vocabulary (CONCEPTS.md)

Root `CONCEPTS.md` is the shared domain glossary. Keep it alive — the bar is LOW:

- When a real, non-obvious domain term surfaces in work and is NOT yet in
  `CONCEPTS.md`, add it: one short definition + where it lives (file path). Seed
  the term's own area; do not bulk-dump the whole repo.
- When a defined concept moves, is renamed, or changes meaning, update its entry.
- Do not duplicate `CONCEPTS.md` content into `cerebrum.md`; cerebrum is for
  preferences/learnings/bugs, CONCEPTS.md is for stable domain vocabulary.
- After editing `CONCEPTS.md`, update `.wolf/anatomy.md` like any other file.

## After Actions

1. After every significant action, append a one-line entry to `.wolf/memory.md`:
   `| HH:MM | description | file(s) | outcome | ~tokens |`
2. After creating, deleting, or renaming files: update `.wolf/anatomy.md`.

## Cerebrum Learning (MANDATORY — every session)

OpenWolf's value comes from learning across sessions. You MUST update `.wolf/cerebrum.md` whenever you learn something useful. This is not optional.

**Update `## User Preferences` when the user:**
- Corrects your approach ("no, do it this way instead")
- Expresses a style preference (naming, structure, formatting)
- Shows a preferred workflow or tool choice
- Rejects a suggestion — record what they preferred instead
- Asks for more/less detail, verbosity, explanation

**Update `## Key Learnings` when you discover:**
- A project convention not obvious from the code (e.g., "tests go in __tests__/ not test/")
- A framework-specific pattern this project uses
- An API behavior that surprised you
- A dependency quirk or version constraint
- How modules connect or data flows through the system

**Update `## Do-Not-Repeat` (with date) when:**
- The user corrects a mistake you made
- You try something that fails and find the right approach
- You discover a gotcha that would trip up a fresh session

**Update `## Decision Log` when:**
- A significant architectural or technical choice is made
- The user explains why they chose approach A over B
- A trade-off is explicitly discussed

**The bar is LOW.** If in doubt, add it. A cerebrum entry that's slightly redundant costs nothing. A missing entry means the next session repeats the same discovery process.

## Bug Logging (MANDATORY)

**Log a bug to `.wolf/buglog.json` whenever ANY of these happen:**
- The user reports an error, bug, or problem
- A test fails or a command produces an error
- You fix something that was broken
- You edit a file more than twice to get it right
- An import, module, or dependency is missing or wrong
- A runtime error, type error, or syntax error occurs
- A build or lint command fails
- A feature doesn't work as expected
- You change error handling, try/catch blocks, or validation logic
- The user says something "doesn't work", "is broken", or "shows wrong X"

**Before fixing:** Read `.wolf/buglog.json` first — the fix may already be known.

**Dedup before appending (MANDATORY — buglog already has 300+ entries, keep it from rotting):**
Before writing a NEW entry, grep `.wolf/buglog.json` for an existing match. Run a few targeted, case-insensitive searches over `error_message`, `file`, and `tags` using the keywords of the current bug (module name, error string, symptom).

- **Match found (same root cause, same file/area):** do NOT add a new entry. Instead, on the existing entry: increment `occurrences`, set `last_seen` to today, and append any new keyword to `tags`. If the recurrence revealed a deeper or different cause, refine `root_cause`/`fix` in place rather than duplicating.
- **Related but distinct (same area, different cause):** add a new entry, and cross-link both ways via `related_bugs` (put each other's `id` in the array).
- **No match:** add a new entry as below.

Scoring rule of thumb (mirrors ce-compound overlap): if 4–5 of {error_message, root_cause, file, fix approach, tags} match → it's the same bug, bump `occurrences`; 2–3 match → related, cross-link; 0–1 → distinct.

**After fixing:** ALWAYS append to `.wolf/buglog.json` with this structure:
```json
{
  "id": "bug-NNN",
  "timestamp": "ISO date",
  "error_message": "exact error or user complaint",
  "file": "file that was fixed",
  "root_cause": "why it broke",
  "fix": "what you changed to fix it",
  "tags": ["relevant", "keywords"],
  "related_bugs": [],
  "occurrences": 1,
  "last_seen": "ISO date"
}
```

**The threshold is LOW.** When in doubt, log it. A false positive in the bug log costs nothing. A missed bug means repeating the same mistake later.

## Token Discipline

- Never re-read a file already read this session unless it was modified since.
- Prefer anatomy.md descriptions over full file reads when possible.
- Prefer targeted Grep over full file reads when searching for specific code.
- If appending to a file, do not read the entire file first.

## Design QC

When the user asks to check, evaluate, or improve the design/UI of the app,
invoke the `openwolf-designqc` skill (`.claude/skills/openwolf-designqc/SKILL.md`).

## Reframe — UI Framework Selection

When the user asks to change, pick, migrate, or "reframe" the project's UI
framework, invoke the `openwolf-reframe` skill (`.claude/skills/openwolf-reframe/SKILL.md`).

## Session End

Before ending or when asked to wrap up:

1. Write a session summary to `.wolf/memory.md`.
2. Review the session: did you learn anything? Did the user correct you? Did you fix a bug? If yes, update `.wolf/cerebrum.md` and/or `.wolf/buglog.json`.
