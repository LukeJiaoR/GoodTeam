# Design — Context onboarding for existing projects

> Stage: **architect**. Defines how GoodTeam acquires a project's context when it
> adopts/loads an existing project, so it can actually understand a codebase well
> enough to iterate on it (including itself). Grounded in the current loader on
> `main`; file:line cited. Founder-directed (see the dialog that produced this).

## 1. The problem (grounded)

Today `collect_project_context` (`graph.py:139`) → `single_root_context_files`
(`graph.py:228`) loads, for a single-root repo: `README*`, dependency manifests,
`docs/**` (rglob, ≤2 levels) **capped at `[:20]`**, plus a shallow directory tree.
Three real gaps surfaced while pointing GoodTeam at its own sandbox:

1. **`docs/` capped at 20 files** — GoodTeam's `docs/plans/` alone is ~40 files;
   most design docs never reach context.
2. **`CLAUDE.md` (root) is never loaded** — it isn't a README/manifest and isn't
   under `docs/`, yet it holds the most important project instructions.
3. **The `fix` profile loads no context at all** — only the greenfield runners
   call `collect_project_context` (`graph.py:398/515/597`); `run_fix_until_next_gate`
   (`graph.py:2103`) / `run_fix_implementation` (`graph.py:2136`) do not.

## 2. Design principle

> **On load, run an L1→L4 cascade (cheapest-first) to produce "GoodTeam's
> understanding + an index"; gate every generation behind an up-front token/cost
> estimate with a cheap-model default; cache the result as the L1 entry so the
> first load is the only expensive one. Methodology belongs in GoodTeam tooling;
> a project only carries its project-specific context.**

## 3. The onboarding cascade (cheapest-first)

| Tier | Situation | Action | Cost |
| --- | --- | --- | --- |
| **L1** | A GoodTeam-native context dir already exists (the cached entry, e.g. `.goodteam/context/`) | Read it directly; no generation | ~0 |
| **L2** | User declares doc sources — local files, directories, **and URLs** — whatever they choose (NOT auto-capped at 20) | Ingest → generate GoodTeam's understanding + index/TOC | medium |
| **L3** | Project already has good docs (`README`, `docs/`, `CLAUDE.md`) | Feed them in as L2 sources (does **not** conflict with L2 — same ingestion) | medium |
| **L4** | Nothing usable → read the code to synthesize context | **Last resort** | ⚠️ high |

L2 and L3 are the same ingestion with different source origins. L4 is the fallback
when there are no docs.

## 4. The cost guard (the load-bearing safety mechanism)

A single context generation over a large codebase can be tens of millions of
tokens. Therefore **no generation runs without an up-front estimate + confirmation**:

- **Estimate before generate (always, not just for code):** sum the source size
  (chars/bytes of the selected local files + fetched URLs + code skeleton),
  convert to an approximate token count, and present
  `"~X tokens / ~¥Y at <model>. Proceed?"`.
- **Cheap-model default + recommendation:** context generation defaults to the
  cheapest capable model tier (NOT the dispatch model). Recommend it explicitly;
  let the user override. (Pull the tier from `models.yaml` / a new
  `context_generation` model slot.)
- **L4 is tiered, never "read everything":**
  1. **Skeleton first** — directory tree + file inventory + symbol signatures +
     a few key entrypoints → a cheap, lightweight context.
  2. **Deep-read on demand** — only pull a file's full body when a task actually
     needs it (route through the memory/retrieval engine).
  This converts "tens of millions of tokens" into "hundreds of thousands now,
  more only when needed."

## 5. Generate-once → cache as L1 (self-upgrading cascade)

- Whatever L2/L3/L4 produce — **GoodTeam's curated understanding + an index/TOC** —
  is written to the **L1 native context dir** (e.g. `.goodteam/context/`):
  - `understanding.md` — the curated top-level project understanding (the **single
    entry file** from the companion design).
  - `index.md` (or `index.json`) — a table-of-contents mapping topics → source
    locations (local paths / URLs) for **on-demand retrieval**, not bulk loading.
- Next load hits **L1** → cheap. **The first import is the only expensive one.**
- **Refresh:** the `retro_memory` stage updates the entry/index as the project
  evolves, so it never goes stale. (Adoption's existing `takeover-context.md`
  (`graph.py:1969-1995`) is the natural generator for the first `understanding.md`.)

## 6. The entry-file contract

The cached L1 entry is what every later run reads instead of slurping files:

```
.goodteam/context/
  understanding.md   # curated top-level context (what the project is, shape,
                     #   key invariants, where things live) — GoodTeam-generated
  index.md           # topic → source map (local path | url), for on-demand pulls
  sources.yaml       # the declared source set (local globs + urls) + last-refresh
```

A run reads `understanding.md` to get top-level context and consults `index.md`
to pull deeper material only when a task needs it.

## 7. Methodology → GoodTeam tooling (the CLAUDE.md split)

`CLAUDE.md` mixes two kinds of content; separate them:

- **Generic engineering methodology** → **bake into GoodTeam tooling** (role
  prompts, workflow guards, defaults): TDD, branch→PR→CI→human-merge, lane
  discipline, prompt self-containment, "never run smoke in an active workspace",
  reviewer-dissent, commit hygiene. A new project inherits these automatically —
  it should NOT have to re-declare them in a CLAUDE.md.
- **Project-specific context** → stays as project context (the L1 entry): repo
  map, current state, which agents/models, self-referential safety notes.

This makes "GoodTeam owns the engineering-org protocol" concrete: the methodology
becomes a versioned product asset, not per-project boilerplate.

**Caveat:** the split isn't always clean (some CLAUDE.md notes are self-referential,
e.g. "never run smoke in *this* repo"); the migration needs a line-by-line triage,
and baked-in methodology must itself be versionable.

## 8. URL ingestion + security

- URL sources are fetched content: enforce a **size cap per source + total**, count
  them in the cost estimate, and time-box fetches.
- Treat fetched content as **untrusted** (it can carry prompt-injection); fence it
  as data, never as instructions, when it feeds generation.
- Record each source + fetch time in `sources.yaml` for provenance/refresh.

## 9. fix-profile context (minor)

`run_fix_until_next_gate` / `run_fix_implementation` should load context the same
way (read the L1 entry, or call `collect_project_context`). Low priority — a fix
run can generate/refresh context on start if missing. One-line wiring.

## 10. Track decomposition

- **Track A — loader cascade + entry cache (BE):** L1 read; lift the `[:20]` cap;
  add `CLAUDE.md` (and root-level `*.md` instruction files) to the single-root
  sources; write/read the `.goodteam/context/` entry; `collect_project_context`
  resolves L1 → else generate. Lane: `graph.py` context loaders, new
  `context.py`, tests.
- **Track B — cost guard (BE):** token estimator over a source set; a
  `context_generation` cheap-model slot; an estimate/confirm seam the UI/CLI calls
  before generation; L4 skeleton-first + on-demand deep read.
- **Track C — source declaration UX (FE + BE):** let the user add local files /
  dirs / URLs as context sources at import (New Project / adopt flow), with the
  cost preview + cheap-model recommendation shown before "Generate".
- **Track D — methodology→tooling migration (BE):** triage `CLAUDE.md`, move
  generic methodology into role prompts / workflow defaults, leave project-specific
  context behind. Sequenced carefully (it changes worker prompts).
- **Track E — fix-profile context (BE, tiny):** wire context into the fix runners.

## 11. Open questions

1. **Entry format** — `understanding.md` + `index.md` markdown, or a structured
   `index.json` the retrieval engine queries directly?
2. **Cheap-model slot** — a new `models.yaml` `context_generation` profile, or
   reuse an existing tier? What's the default cheap model?
3. **Refresh trigger** — only `retro_memory`, or also on detected source drift
   (mtime/hash change in `sources.yaml`)?
4. **L4 skeleton depth** — signatures only, or signatures + docstrings + key file
   bodies? Where's the cost/quality knee?
5. **Methodology versioning** — how does a project pin/override a GoodTeam
   methodology version once it's tooling?
