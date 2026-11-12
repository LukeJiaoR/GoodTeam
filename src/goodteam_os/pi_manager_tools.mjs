// Fixed Manager capability extension, selected only by the captured Manager contract.
// The nonce correlates trusted-extension startup; it is not an OS security boundary.
export default function (pi) {
  const raw = JSON.parse(process.env.GOODTEAM_PI_TOOL_POLICY || 'null');
  const permitted = new Set(['goodteam_skill_resource']);
  if (!raw || raw.version !== 'pi-manager-tools.v1' || typeof raw.nonce !== 'string' ||
      !Array.isArray(raw.tools) || raw.tools.some(name => !permitted.has(name)) ||
      new Set(raw.tools).size !== raw.tools.length) throw new Error('Missing explicit host tool policy');
  const allowed = [...raw.tools].sort();
  let witnessed = false;
  // Defence in depth: registry filtering comes from the host's fixed --tools.
  // This hook also blocks calls before the authenticated witness completes.
  pi.on('tool_call', async event => {
    if (!witnessed || !allowed.includes(event.toolName)) {
      return { block: true, reason: 'Tool unavailable in this Manager invocation' };
    }
  });
  pi.on('session_start', async () => {
    witnessed = false;
    pi.setActiveTools(allowed);
    const active = [...pi.getActiveTools()].sort();
    const available = pi.getAllTools().map(tool => tool.name).sort();
    if (JSON.stringify(active) !== JSON.stringify(allowed) ||
        JSON.stringify(available) !== JSON.stringify(allowed)) throw new Error('Tool policy mismatch');
    const response = await fetch(process.env.GOODTEAM_SKILL_RESOURCE_URL, {
      method: 'POST', redirect: 'error',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.GOODTEAM_SKILL_RESOURCE_TOKEN}` },
      body: JSON.stringify({ operation: 'tool_policy_witness', version: raw.version,
        nonce: raw.nonce, active, available }),
    });
    if (!response.ok || (await response.json()).status !== 'policy_witness_received') {
      throw new Error('Host rejected tool policy');
    }
    witnessed = true;
  });
}
