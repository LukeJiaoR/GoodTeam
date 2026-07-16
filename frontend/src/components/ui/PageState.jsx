import { useId } from 'react';

const PAGE_STATE_KINDS = new Set(['loading', 'empty', 'error', 'needs_input', 'complete']);

export default function PageState({
  action,
  className = '',
  description,
  kind = 'empty',
  title,
  'aria-labelledby': ariaLabelledBy,
  ...sectionProps
}) {
  const titleId = useId();
  const resolvedKind = PAGE_STATE_KINDS.has(kind) ? kind : 'empty';
  const role = resolvedKind === 'error' ? 'alert' : resolvedKind === 'loading' ? 'status' : 'region';

  return (
    <section
      {...sectionProps}
      className={['product-page-state', className].filter(Boolean).join(' ')}
      role={role}
      aria-labelledby={ariaLabelledBy || titleId}
      aria-busy={resolvedKind === 'loading' ? 'true' : undefined}
      data-kind={resolvedKind}
    >
      <div className="product-page-state__marker" aria-hidden="true" />
      <div className="product-page-state__content">
        <h2 id={titleId} className="product-page-state__title">{title}</h2>
        {description ? <div className="product-page-state__description">{description}</div> : null}
        {action ? <div className="product-page-state__action">{action}</div> : null}
      </div>
    </section>
  );
}
