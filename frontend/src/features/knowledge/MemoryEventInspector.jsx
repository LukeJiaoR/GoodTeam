import './memoryProvenance.css';
import { isMemoryEventId } from './memoryProvenance';

export default function MemoryEventInspector({ elementRef, event, projectKey, onOpenRelated, onClose, t }) {
  const newer = Array.isArray(event.superseded_by) ? event.superseded_by.filter(isMemoryEventId) : [];
  const references = Array.isArray(event.cited_by_jobs) ? event.cited_by_jobs : [];
  const status = event.is_superseded === true ? 'superseded'
    : event.is_superseded === false ? 'notSuperseded' : 'statusUnknown';
  return <aside ref={elementRef} className="knowledge-memory-inspector memory-provenance" aria-label={t('knowledge.memory.inspector')} tabIndex={-1}>
    <header><div><p>{projectKey} · {event.role}</p><h3>{t('knowledge.memory.inspector')}</h3></div>
      <button type="button" className="product-button product-button--secondary" onClick={onClose}>{t('knowledge.memory.closeSource')}</button>
    </header>
    <p className="memory-provenance__boundary">{t('knowledge.memory.readOnly')}</p>
    <p className="memory-provenance__status" data-superseded={event.is_superseded === true}>{t(`knowledge.memory.${status}`)}</p>
    <blockquote className="memory-provenance__text">{event.text || t('knowledge.memory.noText')}</blockquote>
    <dl>
      <div><dt>{t('knowledge.memory.source')}</dt><dd>{event.source || t('knowledge.memory.unknown')}</dd></div>
      <div><dt>{t('knowledge.memory.recordedAt')}</dt><dd>{event.timestamp || t('knowledge.memory.unknown')}</dd></div>
      <div><dt>{t('knowledge.memory.role')}</dt><dd>{event.role || t('knowledge.memory.unknown')}</dd></div>
      <div><dt>{t('knowledge.memory.horizon')}</dt><dd>{event.horizon || t('knowledge.memory.unknown')}</dd></div>
      <div><dt>{t('knowledge.memory.eventId')}</dt><dd><code>{event.id}</code></dd></div>
    </dl>
    <section aria-label={t('knowledge.memory.correctionChain')}>
      <h4>{t('knowledge.memory.correctionChain')}</h4>
      {isMemoryEventId(event.supersedes) ? <p>{t('knowledge.memory.replaces')} <button type="button" onClick={() => onOpenRelated(event.supersedes)}>{event.supersedes}</button></p> : null}
      {newer.length ? <div><p>{t('knowledge.memory.replacedBy')}</p><ul>{newer.map(id => <li key={id}><button type="button" onClick={() => onOpenRelated(id)}>{id}</button></li>)}</ul></div> : null}
      {!isMemoryEventId(event.supersedes) && !newer.length ? <p>{t('knowledge.memory.noCorrectionLink')}</p> : null}
    </section>
    <details><summary>{t('knowledge.memory.jobReferences')}</summary>
      <p>{t('knowledge.memory.referenceBoundary')}</p>
      {references.length ? <ul>{references.map((job, index) => <li key={job.id || index}><code>{job.id}</code><p>{[job.role, job.status].filter(Boolean).join(' · ')}</p>{job.summary ? <p>{job.summary}</p> : null}</li>)}</ul>
        : <p>{t('knowledge.memory.noJobReferences')}</p>}
    </details>
    <details><summary>{t('knowledge.memory.rawRecord')}</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
  </aside>;
}
