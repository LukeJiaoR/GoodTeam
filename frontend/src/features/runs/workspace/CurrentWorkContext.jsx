import Button from '../../../components/ui/Button';
import StatusBadge from '../../../components/ui/StatusBadge';
import { currentWorkContext, focusWorkContext } from './currentWorkContext.js';
import './currentWorkContext.css';

export default function CurrentWorkContext({ projectKey, projectName, title, workspace, freshness, historical, onRefresh, reducedMotion, t }) {
  const context = currentWorkContext({ workspace, projectKey, title, freshness, historical, t });
  const statusTone = context.status === 'blocked' ? 'danger'
    : ['needs_input', 'awaiting_review'].includes(context.status) ? 'active' : 'neutral';
  return (
    <section className="work-current-context" aria-label={t('work.current.label')} data-state={context.status}>
      <div className="work-current-context__identity">
        <span className="work-current-context__project">{projectName || projectKey}</span>
        <strong title={context.title}>{context.title}</strong>
      </div>
      <div className="work-current-context__state">
        <StatusBadge tone={statusTone}>{t(`work.current.status.${context.status}`)}</StatusBadge>
        <p>{context.reason}</p>
      </div>
      {context.target ? <Button variant="secondary" onClick={(event) => {
        if (context.target === 'refresh') onRefresh?.();
        else focusWorkContext(event.currentTarget.closest('.product-work-page'), context.target, { reducedMotion });
      }}>{t(`work.current.action.${context.target}`)}</Button> : null}
    </section>
  );
}
