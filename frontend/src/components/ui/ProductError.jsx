/**
 * [INPUT]: 依赖结构化错误、翻译与 Button
 * [OUTPUT]: 提供ProductError
 * [POS]: 将已知错误转为产品文案，未知错误使用通用提示
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import Button from './Button';

function recoveryLabel(action, t) {
  const key = `projects.recovery.${action}`;
  const translated = t(key);
  return translated === key ? '' : translated;
}

export default function ProductError({ error, onRetry, t }) {
  const offline = error?.code === 'offline';
  const permissionDenied = error?.status === 401 || error?.status === 403 || ['permission_denied', 'same_origin_required'].includes(error?.code);
  const title = permissionDenied ? 'product.states.permissionDenied' : offline ? 'product.states.offline' : 'product.states.failed';
  const summaryKey = `product.errors.${error?.code}`;
  const summary = t(summaryKey);
  const recovery = (error?.recovery || []).map(action => recoveryLabel(action, t)).filter(Boolean);
  return (
    <section className="product-error-state" role="alert">
      <div>
        <strong>{t(title)}</strong>
        <p>{offline ? t('product.errors.offline') : permissionDenied ? t('product.errors.permissionDenied')
          : summary !== summaryKey ? summary : t('product.errors.generic')}</p>
        {recovery.length ? <ul>{recovery.map((text) => <li key={text}>{text}</li>)}</ul> : null}
      </div>
      {onRetry ? <Button variant="secondary" onClick={onRetry}>{t('product.actions.retry')}</Button> : null}
    </section>
  );
}
