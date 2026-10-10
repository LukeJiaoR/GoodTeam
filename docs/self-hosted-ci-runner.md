# Former self-hosted CI runner

As of 2026-10-10, all active GitHub Actions jobs use GitHub-hosted
`ubuntu-latest` runners. No active workflow selects `goodteam-ci` or invokes its
runner cleanup hooks.

This repository change redirects CI jobs; it does not unregister a previously
registered runner or stop its host service. The former host was shared with
YUVA CI, so any infrastructure cleanup must be handled separately without
affecting that service.

The former runner's design and operations notes are preserved in the
[archive](archive/self-hosted-ci-runner.md). Current verification and runner
policy is documented in [testing strategy](testing-strategy.md).
