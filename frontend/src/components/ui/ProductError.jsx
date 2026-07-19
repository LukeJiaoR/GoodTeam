import Button from './Button';

function recoveryLabel(action, t) {
  const key = `projects.recovery.${action}`;
  const translated = t(key);
  return translated === key ? t('product.errors.recovery', { action: String(action).replaceAll('_', ' ') }) : translated;
}

export default function ProductError({ error, onRetry, t }) {
  const offline = error?.code === 'offline';
  const permissionDenied = error?.status === 401 || error?.status === 403 || ['permission_denied', 'same_origin_required'].includes(error?.code);
  const title = permissionDenied ? 'product.states.permissionDenied' : offline ? 'product.states.offline' : 'product.states.failed';
  return (
    <section className="product-error-state" role="alert">
      <div>
        <strong>{t(title)}</strong>
        <p>{offline ? t('product.errors.offline') : permissionDenied ? t('product.errors.permissionDenied') : error?.message || t('product.errors.generic')}</p>
        {error?.code ? <code>{error.code}</code> : null}
        {error?.requestId ? <code>{t('projects.requestId', { requestId: error.requestId })}</code> : null}
        {error?.recovery?.length ? <ul>{error.recovery.map((action) => <li key={action}>{recoveryLabel(action, t)}</li>)}</ul> : null}
      </div>
      {onRetry ? <Button variant="secondary" onClick={onRetry}>{t('product.actions.retry')}</Button> : null}
    </section>
  );
}
