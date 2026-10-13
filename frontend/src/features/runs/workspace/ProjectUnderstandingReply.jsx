import MarkdownView from '../../../components/ui/MarkdownView';

const fields = [
  ['stack', /^(?:技术栈\s*\/\s*)?Stack:\s*(.*)$/i],
  ['entries', /^(?:入口\s*\/\s*)?Entry points:\s*(.*)$/i],
  ['commands', /^(?:命令\s*\/\s*)?Commands:\s*(.*)$/i],
  ['risks', /^(?:风险\s*\/\s*)?Risks:\s*(.*)$/i],
];
const knownLimits = {
  'Repository scan reached the bounded entry limit; deeper indexing is recommended.': 'entries',
  'Repository scan reached the bounded depth limit; deeper indexing is recommended.': 'depth',
  'The protected snapshot omitted files at its safety limits; inventory coverage is incomplete.': 'snapshot',
};

export default function ProjectUnderstandingReply({ content, scan, t }) {
  const values = {};
  const remaining = [];
  for (const line of (content || '').split('\n').filter((line) => line.trim())) {
    const field = fields.find(([, pattern]) => pattern.test(line.trim()));
    if (field && !values[field[0]]) values[field[0]] = line.trim().match(field[1])[1];
    else remaining.push(line);
  }
  if (!Object.keys(values).length) return <MarkdownView content={content} />;
  const list = (value) => [...new Set((value || '').split(',').map((item) => item.trim()).filter(Boolean))];
  const entries = list(values.entries);
  const commands = list(values.commands);
  return <div className="project-understanding-reply">
    {values.stack ? <p>{t('assistant.understanding.stack', { stack: values.stack })}</p> : null}
    <p>{t('assistant.understanding.scope')}</p>
    {typeof scan?.complete === 'boolean' ? <p>{t(`assistant.understanding.coverage.${scan.complete ? 'complete' : 'incomplete'}`, {
      files: scan.files_seen ?? 0, directories: scan.directories_seen ?? 0, omitted: scan.snapshot_omitted_files ?? 0,
    })}</p> : null}
    {values.risks ? <div className="project-understanding-reply__limits">
      <strong>{t('assistant.understanding.limits')}</strong>
      <p>{knownLimits[values.risks] ? t(`assistant.understanding.limit.${knownLimits[values.risks]}`) : values.risks}</p>
    </div> : null}
    {[[entries, 'entries'], [commands, 'commands']].map(([items, key]) => items.length ? <details key={key}>
      <summary>{t(`assistant.understanding.${key}`, { count: items.length })}</summary>
      <ul>{items.map((item) => <li key={item}><code>{item}</code></li>)}</ul>
    </details> : null)}
    {remaining.length ? <MarkdownView content={remaining.join('\n')} /> : null}
  </div>;
}
