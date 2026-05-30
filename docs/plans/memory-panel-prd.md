# PRD + Architecture — Memory visualization panel

> Stage: **product_requirement + architect**. This is the PRD/RFC gate — review
> before the per-role implementation prompts are issued.

## 1. Intent (why)

GoodTeam's pitch (Moat #2) is *compounding memory*: every run makes the next
sharper. Today that memory layer is **invisible** — it lives as markdown +
`events.jsonl` under each target's `.goodteam/memory/`, and the BM25 retrieval we
just shipped silently feeds worker pickup packets. A user has no way to *see*
what an agent "remembers" or *which facts* it pulled for a task.

This panel makes the memory layer **visible and inspectable** in the dashboard:
per role, show its memory horizons + the relevant facts retrieved for its latest
job (with `mem:` handles). It turns an invisible backend capability into a
trust-building, debuggable surface — and it's the UI that M1 (write-back) will
fill in.

## 2. Users & value

- **The founder/operator** (primary): "what does the architect role know about
  this project, and what did it actually retrieve when it last ran?" — trust,
  debugging, and seeing the memory compound over time.
- **Design partners** (later): the visible proof that the memory moat is real.

## 3. User-facing acceptance

- In a project's detail view there is a **"Memory"** tab/section.
- It lists the roles that have memory, each showing:
  - long-term / mid-term **summaries** and **short-term** text,
  - an **event count**,
  - the **relevant facts** retrieved for that role's most recent worker job —
    each with its `mem:` handle, source, and (optional) score.
- Empty states are graceful: a role with no events shows "no events yet"; a
  project with no runs shows an explanatory note (no error, no blank screen).

## 4. Scope

**In:** read-only visualization of existing memory + the per-job pickup packets
already written to disk (`role_pickup_packet.json`). A dedicated lazy API.

**Out (this round):** editing memory from the UI; clickable `mem:` → full source
event drill-down (nice-to-have, note as a follow-up); anything that writes memory
(that's M1).

## 5. Known dependency / honest caveat

Retrieved facts come from `events.jsonl`, which is populated by **memory
write-back (M1) — not yet built**. So today the "relevant facts" area will
usually be **empty** unless events were seeded manually. The panel still delivers
value (it makes the memory structure + pickup packets visible and is the surface
M1 fills), but reviewers should expect sparse retrieval data until M1 lands. QA
will seed a couple of events to verify the retrieval display works.

## 6. Architecture / contract (decision record)

**Decision: a dedicated lazy endpoint, not an extension of `/api/state`.**
The dashboard polls `/api/state`; memory can be large and is only needed when the
user opens the Memory tab. So add `GET /api/memory?project=<KEY>`, fetched on
demand. Keeps the hot path lean. (Rejected alt: stuffing it into `/api/state` —
bloats every poll.)

**Data sources (already on disk, read-only):**
- Per-role memory: `RoleMemoryStore(project_root).build_pickup_packet(role)` →
  `summaries`, `short_term`, `event_count`.
- Retrieved facts: the most recent worker job's stored
  `<job_root>/role_pickup_packet.json` already contains `relevant_events`
  (BM25 results with `mem:` ids), because workers build the packet with the task
  as the query. Use the latest job per role.
- `project_root` (the target runtime root where `.goodteam/` lives) is resolved
  the same way `/api/state` already does it.

**Contract — `GET /api/memory?project=<KEY>` →**
```json
{
  "project_key": "CONTENTOPS",
  "roles": [
    {
      "role": "implementation",
      "summaries": { "long-term": "…", "mid-term": "…" },
      "short_term": "…",
      "event_count": 3,
      "latest_retrieval": {
        "job_id": "job-abc",
        "relevant_events": [
          { "id": "mem:abc123", "score": 1.2, "horizon": "mid-term",
            "text": "…", "source": "job-x/result.md", "timestamp": "…" }
        ]
      }
    }
  ]
}
```
- `roles`: enumerate role memory dirs under `project_root/.goodteam/memory/roles/`
  (or the dispatched roles if none). Omit `latest_retrieval` (or send
  `relevant_events: []`) for roles with no jobs.
- Errors: HTTP 400 `{ "error": "…" }`, matching the existing `/api/state` style.
- No secrets in the payload (memory is non-secret by policy, but don't echo env).

## 7. Tracks (issued after this gate is approved)
- **backend (Codex):** the `/api/memory` endpoint + a `build_memory_overview(
  project_root)` helper + tests.
- **frontend (Gemini):** the "Memory" tab in `frontend/src/App.jsx`, lazy-fetch
  on open, render per-role cards + retrieved-facts list with `mem:` handles.
- **QA:** seed a couple of events, then verify the panel end-to-end in a browser.
