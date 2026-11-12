"""Explicit Pi tool-level read-only policy, NOT an OS sandbox admission.

Source contract: earendil-works/pi v1.1.0 (abe508e). Only newly captured readonly_pi_methods.v1 requests select this policy.
Installed SDK/model acceptance remains separate from this host contract.
Only operator-created instances may be wired into the original Runtime.
"""
import json
import secrets
import threading


class PiReadOnlyPolicyError(RuntimeError):
    pass


class PiReadOnlyTools:
    version = 'pi-read-only-tools.v1'
    # No shell, file writers, codemode, tool discovery, MCP or arbitrary extension.
    read_tools = ('read', 'grep', 'find', 'ls')
    rpc_commands = frozenset({'get_state', 'get_messages', 'get_session_stats',
        'get_last_assistant_text', 'get_available_models', 'get_commands',
        'prompt', 'steer', 'follow_up', 'abort', 'clear_queue', 'get_entries', 'set_thinking_level'})

    def __init__(self, *, methods: bool, authorized_search: bool = False):
        if methods is not True or type(authorized_search) is not bool:
            raise PiReadOnlyPolicyError('Read-only methods require bound Skills and an explicit search capability choice.')
        self.tools = tuple(sorted((*self.read_tools,
            *(('goodteam_skill_resource',) if methods else ()),
            *(('goodteam_web_search',) if authorized_search else ()))))
        self._lock = threading.Lock()
        self.begin_start()

    def begin_start(self):
        """Each process/session startup must own a fresh witness, never reuse one."""
        with self._lock:
            self.nonce = secrets.token_hex(24)
            self.ready = threading.Event()
            self.failed = False
            return self.nonce

    def invalidate(self):
        with self._lock:
            self.failed = True
            self.ready.set()

    def arguments(self):
        # Fixed nonempty strings override independent SYSTEM/APPEND_SYSTEM lookup.
        return ['--tools', ','.join(self.tools), '--no-mcp', '--no-extensions',
                '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-themes',
                '--no-approve', '--offline', '--system-prompt',
                'Perform the bounded task using only the supplied task context and available tools.',
                '--append-system-prompt', 'This invocation uses an explicit host-supplied method input.']

    def environment(self):
        return {'GOODTEAM_PI_TOOL_POLICY': json.dumps({'version': self.version,
                    'nonce': self.nonce, 'tools': list(self.tools)}, separators=(',', ':'))}

    def accept(self, payload):
        """Called only by the authenticated, context-bound host resource channel."""
        with self._lock:
            valid = (isinstance(payload, dict) and set(payload) == {'operation', 'version', 'nonce', 'active', 'available'}
                and payload['operation'] == 'tool_policy_witness' and payload['version'] == self.version
                and payload['nonce'] == self.nonce
                and isinstance(payload['active'], list) and isinstance(payload['available'], list))
            if valid:
                valid = (payload['active'] == list(self.tools) and payload['available'] == list(self.tools))
            if not valid:
                self.failed = True
                self.ready.set()
                return {'status': 'policy_rejected'}
            if self.failed:
                return {'status': 'policy_rejected'}
            self.ready.set()
            return {'status': 'policy_witness_received'}

    def require_ready(self, nonce, timeout):
        with self._lock:
            event = self.ready
        observed = event.wait(timeout)
        with self._lock:
            valid = observed and event is self.ready and nonce == self.nonce and not self.failed
        if not valid:
            raise PiReadOnlyPolicyError('Pi tool-policy witness is missing or invalid; do not send a model prompt.')

    def check_rpc(self, command):
        if not isinstance(command, dict) or command.get('type') not in self.rpc_commands:
            raise PiReadOnlyPolicyError('RPC command is outside the read-only invocation contract.')
        # This is only a command-kind gate. Original task/session identity and
        # supported actions still must be checked by the existing Runtime.

    def guard(self, command):
        self.check_rpc(command)
        if command['type'] in {'prompt', 'steer', 'follow_up'}:
            self.require_ready(self.nonce, 0)
