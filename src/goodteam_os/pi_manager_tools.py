"""Manager-only Pi tool contract: supplied Context and bound methods, not an OS sandbox.

Ordinary research/development Workers and historical read-only requests are unchanged.
The original Runtime enforces a fresh witness per process before any model prompt.
"""
import json
import secrets
import threading


from goodteam_os.pi_rpc import PiRpcError


class PiManagerPolicyError(PiRpcError):
    pass


class PiManagerTools:
    version = 'pi-manager-tools.v1'
    # No shell, file writers, codemode, tool discovery, MCP or arbitrary extension.
    read_tools = ()
    rpc_commands = frozenset({'get_state', 'get_messages', 'get_session_stats',
        'get_last_assistant_text', 'get_available_models', 'get_commands',
        'prompt', 'steer', 'follow_up', 'abort', 'clear_queue', 'get_entries', 'set_thinking_level'})

    def __init__(self):
        self.tools = ('goodteam_skill_resource',)
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
                'Coordinate the project using supplied Context and bound Role methods. Delegate professional work through the supplied structured action contract.',
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
            raise PiManagerPolicyError('Pi tool-policy witness is missing or invalid; do not send a model prompt.')

    def check_rpc(self, command):
        if not isinstance(command, dict) or command.get('type') not in self.rpc_commands:
            raise PiManagerPolicyError('RPC command is outside the Manager invocation contract.')
        # This is only a command-kind gate. Original task/session identity and
        # supported actions still must be checked by the existing Runtime.

    def guard(self, command):
        self.check_rpc(command)
        if command['type'] in {'prompt', 'steer', 'follow_up'}:
            self.require_ready(self.nonce, 0)
