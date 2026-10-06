# Merge Candidate Validation

GoodTeam no longer needs to spend a full remote CI run on every development push.

## Agent workflow

1. Develop on a short-lived branch and run focused local checks while iterating.
2. When the branch is ready to become a PR candidate, sync it with the current `main`, make the working tree clean, and record `git rev-parse HEAD`. Candidate dispatch refuses a SHA that does not contain the current `main` HEAD.
3. Run the trusted workflow definition from `main` against that exact SHA:

   ```bash
   gh workflow run ci.yml --repo LukeJiaoR/GoodTeam --ref main \
     -f "target_sha=$(git rev-parse HEAD)"
   ```

4. Wait for the run to finish successfully. It classifies the diff from `main`, runs the planned browser/backend jobs on the self-hosted runner, and records commit status `merge-candidate/validated`.
5. Open or update the PR only after the exact SHA has that successful status.
6. Any new commit invalidates the old evidence automatically because the PR head SHA changes. Re-run candidate CI for the new SHA before merge.
7. If `main` advances after validation, the PR gate requires the head to include the new base SHA. Sync `main`, producing a new SHA, and run candidate CI again.

The PR-side `CI / gate` becomes metadata-only after `.github/ci/candidate-gate-v1` is present on the PR base. It checks the exact head status instead of re-running the heavy suite. The PR that introduces this mechanism uses the previous heavy path because its base does not yet contain the activation marker.

The candidate dispatch must use `--ref main`: the workflow definition is therefore the reviewed default-branch policy while the checked-out application source is the explicit candidate SHA.
