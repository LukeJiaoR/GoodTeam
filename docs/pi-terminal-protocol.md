# Pi terminal protocol boundary (#700)

GoodTeam requires Pi **1.1.0 or newer** for the current persistent-session
contract. An available binary or nonempty version alone is not protocol
compatibility. Unknown/older versions are not ready and do not advertise the
verified capability set. No installation or permission change is automatic.

Official Pi history (earendil-works/pi): get_entries/get_tree appeared in 0.80.3,
agent_settled in 0.80.4, clear_queue in 0.84.4, and agent_settled.aborted in 1.1.0.
See [CHANGELOG](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/CHANGELOG.md)
and [RPC commands](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-commands.md).

A Runtime-originated `agent_settled` with `aborted: true` now marks the original
operation aborted and the Worker failed with `pi_aborted`, even if no local
abort method initiated it. It never becomes completed/delivered. Existing
protocol events remain available as historical evidence.

Explicit Stop clears pending messages before aborting the active turn, because
Pi abort alone preserves the queue. If clearing the queue has an unknown or
failed result, abort is still attempted; its acknowledgement cannot wash away
the unconfirmed queue-clear result. Neither command is automatically retried.
Acknowledgement remains distinct from process exit. Existing close/cleanup owns
the process lifecycle.

This is a prerequisite correction for trustworthy supervision, not Manager
control delegation or control-to-reply attribution. Normal session events do
not carry a command request ID. `queued`/`handled` acknowledgement and generic
assistant output must not be relabeled as the response to a particular control.

Tests use free local subprocess fixtures, including the actual Worker path for
Runtime-side cancellation. No installed Pi binary, paid provider, deployment or
full continuous-project acceptance is claimed.
