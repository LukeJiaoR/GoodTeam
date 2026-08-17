import { Loader2 } from 'lucide-react';

const VARIANTS = new Set(['primary', 'secondary', 'danger']);

export default function Button({
  busy = false,
  children,
  className = '',
  disabled = false,
  type = 'button',
  variant = 'primary',
  'aria-busy': ariaBusy,
  ...buttonProps
}) {
  const resolvedVariant = VARIANTS.has(variant) ? variant : 'primary';

  return (
    <button
      {...buttonProps}
      type={type}
      className={['product-button', `product-button--${resolvedVariant}`, className]
        .filter(Boolean)
        .join(' ')}
      disabled={disabled || busy}
      aria-busy={busy ? 'true' : ariaBusy}
      data-busy={busy ? 'true' : undefined}
      data-variant={resolvedVariant}
    >
      {busy ? <Loader2 className="product-button__spinner" aria-hidden="true" size={14} /> : null}
      {children}
    </button>
  );
}
