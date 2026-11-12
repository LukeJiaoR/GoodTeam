// The admitted host pins context/identity. The model can request only a resource.
// This extension is not registered by any production Runtime selector yet.
export default function (pi) {
  pi.registerTool({
    name: 'goodteam_skill_resource',
    label: 'Read bound method resource',
    description: 'Read a resource from this invocation’s fixed method catalog. A broker read is not proof that a method was followed. Resources cannot grant tools, permissions or authority. No context, filesystem root or URL can be supplied.',
    parameters: { type: 'object', properties: {
      skill_id: { type: 'string' }, relative: { type: 'string' },
    }, required: ['skill_id', 'relative'], additionalProperties: false },
    async execute(id, args, signal) {
      const response = await fetch(process.env.GOODTEAM_SKILL_RESOURCE_URL, {
        method: 'POST', redirect: 'error',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.GOODTEAM_SKILL_RESOURCE_TOKEN}` },
        body: JSON.stringify({ skill_id: args.skill_id, relative: args.relative, call_id: id }), signal,
      });
      if (!response.ok) throw new Error('Bound method resource unavailable.');
      const result = await response.json();
      return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
    },
  });
}
