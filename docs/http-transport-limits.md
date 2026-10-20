# Dashboard HTTP transport limits

Both the project-only handler and the production dashboard use the same bounded
POST reader. Existing route, same-origin, loopback, protected-execution, and
server-action authorization checks remain in their existing order. This is a
resource boundary, not a new authorization policy.

- A POST body can contain at most **8 MiB of encoded request bytes**, including
  JSON syntax and escaping. The dashboard sends JSON commands, configuration,
  conversation text, and editable artifact text; imports reference local paths or
  Git sources rather than uploading repository archives. The existing context
  source cap is 1 MiB, conversation context is 32,000 characters, and per-worker
  additional prompts are 4,000 characters. 8 MiB leaves room for a 1 MiB text
  document even with worst-case six-byte JSON escaping, plus command metadata.
  Artifact edits previously had no transport cap; edits larger than 8 MiB now
  fail explicitly with 413 instead of allocating unbounded memory.
- Missing Content-Length retains the empty-body behavior, and zero is accepted.
  One unsigned decimal Content-Length is required for a nonempty body. Duplicate
  headers (even equal ones), signed/negative/malformed values and Transfer-Encoding
  are rejected with 400 before any body read. Oversized numeric values give 413.
- Socket I/O has a **15-second idle timeout**, including request-line/header
  reception. POST bodies additionally have a **60-second total read deadline**,
  so sending individual bytes cannot extend a body read indefinitely. These are
  transport limits, not a deadline for a workflow, native picker, or provider call.
  A slow response reader can also time out during a socket write.
  Header trickling is still bounded by the connection cap and standard-library
  header size/count limits; this is not an Internet-facing reverse proxy.
- Truncated bodies give 400; timed-out bodies give 408. These responses use the
  existing JSON error shape and request_id. Failed framing/read requests close
  their connection without invoking application handlers or draining extra data.
  A peer that has disconnected might not receive the error.
- The dashboard server permits at most **32 active connections/handler threads**.
  This gives headroom for multiple tabs (normal project polling is every five
  seconds), concurrent static assets, and synchronous commands. A slot is held
  through application processing and released on success, disconnect, or failure.
  Above the cap the newly accepted socket is closed immediately; clients may
  retry after an in-flight request finishes. The accept/heartbeat loop never waits
  for a free slot or an overloaded client's response. The OS listen backlog is
  unchanged. Long-running application operations remain subject to their existing
  lifecycle controls, rather than being interrupted by a transport timer.

The exported web.ThreadingHTTPServer constructor remains compatible with existing
callers and test fixtures, and now refers to the bounded subclass. Integrators
embedding build_dashboard_handler should use that server too; an independently
chosen standard server retains its own thread-admission policy. The handler still
sets its own read timeout and validates body limits in that case.

Tests use only isolated loopback sockets, synthetic bodies, and temporary
workspaces. No real project, worker/provider, or remote service is needed.
