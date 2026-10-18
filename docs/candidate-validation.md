# Merge Candidate Validation

GoodTeam no longer needs to spend a full remote CI run on every development push.

## Agent workflow

1. Develop on a short-lived branch and run focused local checks while iterating. Open or update a Draft PR whenever it is useful for review; full candidate CI is not a prerequisite for a Draft.
2. When preparing the final candidate for merge, sync it with the current `main`, make the working tree clean, and record `git rev-parse HEAD`. Candidate dispatch refuses a SHA that does not contain the current `main` HEAD.
3. Publish the committed SHA to a temporary `codex/candidate-*` branch and verify that the remote ref points to it. This publication selects the final candidate for automatic validation; ordinary development branches do not trigger heavy CI. GitHub must be able to resolve the commit before dispatch. For an existing ready-for-review PR, keep its current head unchanged while validating the replacement candidate; Draft heads may change during local iteration. Publishing the temporary ref is not evidence that validation passed.
4. The read-only `Candidate published` workflow announces the branch creation or push. The `Candidate automation` workflow on reviewed `main` checks the live candidate ref and current base, then dispatches `ci.yml` from `main` with the exact recorded SHA. No human dispatch or separately logged-in local terminal is required. The existing command remains available for recovery:

   ```bash
   gh workflow run ci.yml --repo LukeJiaoR/GoodTeam --ref main \
     -f "target_sha=<recorded-40-character-sha>"
   ```

5. Wait for the run to finish successfully. It classifies the diff from `main`, runs the planned browser/backend jobs on GitHub-hosted runners, and records commit status `merge-candidate/validated`.
6. Open a ready-for-review PR, update an existing ready PR, or mark a Draft ready only after its exact head has that successful status. The PR-side `CI / gate` must also succeed on that head before merge. Automation refreshes the exact-head metadata gate if it started before the successful candidate result; a gate that fails after that refresh remains a blocker.
7. Any new commit invalidates the old evidence automatically because the PR head SHA changes. Re-run candidate CI for the new SHA before merge.
8. If `main` advances after validation, the PR gate requires the head to include the new base SHA. Sync `main`, producing a new SHA, and run candidate CI again.

The PR-side `CI / gate` becomes metadata-only after `.github/ci/candidate-gate-v1` is present on the PR base. It checks the exact head status instead of re-running the heavy suite. The PR that introduces this mechanism uses the previous heavy path because its base does not yet contain the activation marker.

With that marker present, opening or updating a Draft can trigger the lightweight
PR check, but does not dispatch full candidate CI. The check can fail while the
Draft head has no successful candidate status; that is not a reason to run heavy
CI on every iteration. A Draft's existence or review feedback does not satisfy
merge validation. Run candidate CI when preparing to merge, then ensure the
metadata gate succeeds for that exact head and current base. These rules do not
waive required checks or change runner and credential isolation.

## Automatic dispatch and gate refresh

Publish final candidates under `codex/candidate-*`, for example
`codex/candidate-pr577-a5a4d35`. Branch creation through the GitHub API and
subsequent Git pushes both announce publication. The publisher has read-only
permissions and does not check out or execute candidate code.

The downstream `workflow_run` controller uses the default-branch definition.
Its only write permission is `actions: write`; it reads repository metadata,
commit statuses and PR identities. It does not check out source, read candidate
reports, evaluate supplied commands, write statuses, use provider secrets, merge
or deploy. Existing CI code-execution jobs retain `contents: read`, and only the
existing isolated status writers can record candidate outcomes.

Before dispatch the controller verifies the originating workflow identity,
same-repository branch, exact SHA, current candidate ref and current `main`
ancestry. Existing pending, successful or failed candidate results are preserved.
The `CI <target_sha>` run name also prevents duplicate dispatch while the pending
status is not yet visible. Real failures and ambiguous API outcomes are reported;
they do not cause an unbounded retry loop or a fabricated successful status.

On CI completion, the controller binds the successful status URL back to a
completed `ci.yml` dispatch on current `main` for that exact target. It refreshes
only a matching open same-repository PR's metadata-only run whose failed gate
started before that validation result. It rechecks the live PR head/base,
preserves failed test/prepare results, and does not repeatedly rerun a gate that
started after validation. Stacked PRs also re-read their current parent branch
and must contain its exact head before their metadata gate is refreshed. The
resulting `CI / gate` remains the merge authority.

Automation becomes active after these workflows land on `main`. Existing
candidates must be synchronized with that new base and republished. Publication
must emit a GitHub branch/push event: GitHub suppresses most events produced by a
workflow's own `GITHUB_TOKEN`, so workflow-based publishers must use their
existing appropriately authorized GitHub App or user identity. Do not add or
broaden credentials implicitly. A missing event, disabled workflow or API
permission error is an infrastructure failure, not a required human approval.

The candidate dispatch must use `--ref main`: the workflow definition is therefore the reviewed default-branch policy while the checked-out application source is the explicit candidate SHA.

Preparation pins the current main SHA in its metadata output. The full-history
candidate checkout already contains this ancestor; classification verifies that
relationship and uses the pinned SHA without fetching after checkout credentials
have been removed. If main advances, the PR gate still requires a new synchronized
candidate and new validation.

## Evidence and cleanup

Record the candidate SHA, pinned base SHA, workflow-definition ref/SHA, run URL,
verification tier, planned job results, and any skipped checks or limitations.
For manual dispatch, the run's `headSha` belongs to the workflow definition;
the `target_sha` input, preparation output, checkout identity check, and commit
status identify the application source being tested. Do not describe a run on
an older candidate as verification of a new head.

Keep a concise durable summary with reproduction commands in the PR or source
documentation. Actions logs and runner-local reports have bounded retention;
routine CI does not upload GitHub Actions artifacts. Preserve local `output/`
unless the user explicitly confirms it is disposable. After merging, remove
temporary candidate refs/worktrees and feature branches according to
`docs/development-branch-hygiene.md`; report anything retained.

## Recovering a broken default-branch workflow

Normal candidate validation always dispatches `--ref main`. If that workflow
fails before it can validate a repair to its own infrastructure, use this
bounded recovery procedure:

1. Preserve the failed run and status. Identify the failing step and prepare a
   minimal CI repair from the latest `main` in a clean branch, separately from
   product changes. Run the relevant local workflow and contract checks; report
   their actual scope and any earlier full-suite evidence without assigning it
   to a new SHA.
2. Prepare the actual temporary workflow and its complete diff from the repair
   candidate's intended `ci.yml`. Allow manual dispatch only, bind preparation
   to one temporary ref and one exact candidate SHA, and require the candidate
   to include the current `main`. Record the current base and workflow digest.
   Keep the reviewed test plan, GitHub-hosted runner selection, credential isolation, hosted
   status writer, and final gate unchanged. Validate the temporary workflow's
   runner boundary and rejection paths locally before publishing it.
3. Obtain explicit user or maintainer authorization for that reviewed workflow,
   temporary ref, candidate SHA, and base before dispatching from it. A request
   to edit agent rules is not authorization for the exceptional run. If the
   workflow, candidate, or base changes, reconcile the scope and authorization
   before proceeding; an approval for an obsolete candidate does not carry over.
4. Run the authorized workflow against the repair candidate. Require every
   planned job, the actual `merge-candidate/validated` status, and the PR-side
   `CI / gate` to succeed on that exact repair head before merging. Never create
   a success status by hand, reduce the test plan to force success, or merge
   through a failed gate. Preserve failures and cancellations honestly.
5. Keep the temporary dispatch exception out of `main`. Remove its remote ref
   after recovery and report retained local evidence. Validate subsequent
   product candidates using the repaired default-branch workflow and their new
   exact SHAs.

Documenting this procedure does not itself authorize a recovery run. It does
not permit arbitrary feature workflows, privileged runners, or production
deployment. Runner and credential rules also apply to temporary workflows; see
`docs/testing-strategy.md`.
