---
name: qa-browser-evidence
description: Verify browser behavior and attach screenshots of the observed results to the QA report.
---

<!--
[INPUT]: receives a bounded QA task and its authorized test environment
[OUTPUT]: produces browser observations, screenshots, and a reproducible QA report
[POS]: the QA browser method used by default QA Role tasks; it selects Ego as the host browser adapter
[PROTOCOL]: Update this header on change, then check AGENTS.md
-->

# QA browser evidence

Use this method for QA tasks that verify a browser flow.

1. Confirm the candidate revision, test environment, and expected result.
2. Before the first browser action, run `ego-browser skill` and follow the printed guide.
3. When the guide succeeds, run the flow with Ego and record the task-space and page evidence it returns.
4. When the command fails or the guide is incomplete, record its exact failure and mark browser-dependent checks unverified.
5. Capture a screenshot of each decisive UI state. For a before-and-after check, capture both states.
6. Save the screenshots as QA artifacts. Link each image beside the test step, expected result, observed result, and verdict.
7. Record the specific blocker when the browser, credentials, or screenshot destination is unavailable. Mark screenshot-dependent cases as unverified until their images are attached.

Use the task's authorized test identities and artifact paths. Exclude credentials and sensitive data from screenshots. Read [the evidence checklist](references/screenshots.md) before returning the report.
