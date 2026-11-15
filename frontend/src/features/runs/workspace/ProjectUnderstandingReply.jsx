/**
 * [INPUT]: 依赖历史项目扫描结果、MarkdownView 与 SourceEvidence
 * [OUTPUT]: 提供ProjectUnderstandingReply
 * [POS]: 解释扫描覆盖边界，将入口和命令放入来源侧栏
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import MarkdownView from '../../../components/ui/MarkdownView';
import SourceEvidence from '../../../components/ui/SourceEvidence';

const fields = [
  ['stack', /^(?:技术栈\s*\/\s*)?Stack:\s*(.*)$/i],
  ['entries', /^(?:入口\s*\/\s*)?Entry points:\s*(.*)$/i],
  ['commands', /^(?:命令\s*\/\s*)?Commands:\s*(.*)$/i],
  ['constraints', /^(?:约束\s*\/\s*)?Constraints:\s*(.*)$/i],
  ['risks', /^(?:风险\s*\/\s*)?Risks:\s*(.*)$/i],
];
const knownLimits = {
  'Repository scan reached the bounded entry limit; deeper indexing is recommended.': 'entries',
  'Repository scan reached the bounded depth limit; deeper indexing is recommended.': 'depth',
  'The protected snapshot omitted files at its safety limits; inventory coverage is incomplete.': 'snapshot',
};

export default function ProjectUnderstandingReply({ content, scan, incomplete = false, t }) {
  const values = {};
  const remaining = [];
  for (const line of (content || '').split('\n').filter((line) => line.trim())) {
    const field = fields.find(([, pattern]) => pattern.test(line.trim()));
    if (field && !values[field[0]]) values[field[0]] = line.trim().match(field[1])[1];
    else remaining.push(line);
  }
  const list = (value) => [...new Set((value || '').split(',').map((item) => item.trim()).filter(Boolean))];
  const entries = list(values.entries);
  const commands = list(values.commands);
  const limited = incomplete || scan?.complete === false;
  return <div className="project-understanding-reply">
    {values.stack ? <p>{t('assistant.understanding.stack', { stack: values.stack })}</p> : null}
    {entries.length || commands.length ? <p>{t('assistant.understanding.discovered', { entries: entries.length, commands: commands.length })}</p> : null}
    {typeof scan?.complete === 'boolean' ? <p>{t(`assistant.understanding.coverage.${scan.complete ? 'complete' : 'incomplete'}`, {
      files: scan.files_seen ?? 0, directories: scan.directories_seen ?? 0, omitted: scan.snapshot_omitted_files ?? 0,
    })}</p> : null}
    {values.risks ? <div className="project-understanding-reply__limits">
      <strong>{t('assistant.understanding.limits')}</strong>
      {list(values.risks).map((risk, index) => <p key={index}>{knownLimits[risk] ? t(`assistant.understanding.limit.${knownLimits[risk]}`) : risk}</p>)}
    </div> : null}
    {values.constraints ? <p>{values.constraints}</p> : null}
    {remaining.length ? <MarkdownView content={remaining.join('\n')} /> : null}
    <p>{t('assistant.understanding.scope')}</p>
    <p>{t(`assistant.understanding.next.${limited ? 'incomplete' : 'complete'}`)}</p>
    {entries.length || commands.length ? <SourceEvidence t={t} label={t('assistant.understanding.sources')}>
      {[[entries, 'entries'], [commands, 'commands']].map(([items, key]) => items.length ? <section className="product-source-evidence" key={key}>
        <h3>{t(`assistant.understanding.${key}`, { count: items.length })}</h3>
        <ul>{items.map((item) => <li key={item}><code>{item}</code></li>)}</ul>
      </section> : null)}
    </SourceEvidence> : null}
  </div>;
}
