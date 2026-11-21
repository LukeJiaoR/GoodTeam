# Former self-hosted CI runner

As of 2026-10-10, all active GitHub Actions jobs use GitHub-hosted
`ubuntu-latest` runners. No active workflow selects `goodteam-ci` or invokes its
runner cleanup hooks.

The retired provisioning and cleanup scripts were removed from the repository.
Their [last source version][runner-source] remains in Git history. This does
not unregister a runner or stop its host service. The former host was shared
with YUVA CI; any host cleanup must be handled separately without affecting it.

[runner-source]: https://github.com/LukeJiaoR/GoodTeam/tree/b0904326905b001ea52f1d11c39c7c3bdd512417/ops/ci-runner

The former runner's design and operations notes are preserved in the
[archive](archive/self-hosted-ci-runner.md). Current verification and runner
policy is documented in [testing strategy](testing-strategy.md).
