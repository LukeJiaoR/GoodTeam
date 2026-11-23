---
name: qa-browser-evidence
description: Verify browser behavior and attach screenshots of the observed results to the QA report.
---

# QA browser evidence

Use this method for QA tasks that verify a browser flow.

1. Confirm the candidate revision, test environment, and expected result.
2. Run the flow with the browser available to this task. Use Ego when available and authorized.
3. Capture a screenshot of each decisive UI state. For a before-and-after check, capture both states.
4. Save the screenshots as QA artifacts. Link each image beside the test step, expected result, observed result, and verdict.
5. Record the specific blocker when the browser, credentials, or screenshot destination is unavailable. Mark screenshot-dependent cases as unverified until their images are attached.

Use the task's authorized test identities and artifact paths. Exclude credentials and sensitive data from screenshots. Read [the evidence checklist](references/screenshots.md) before returning the report.
