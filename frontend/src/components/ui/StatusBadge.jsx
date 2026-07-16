import { Children, isValidElement } from 'react';

function hasVisibleText(children) {
  let found = false;
  Children.forEach(children, (child) => {
    if (found) return;
    if (typeof child === 'string') {
      found = child.trim() !== '';
    } else if (typeof child === 'number') {
      found = true;
    } else if (isValidElement(child)) {
      found = hasVisibleText(child.props.children);
    }
  });
  return found;
}

export default function StatusBadge({
  children,
  className = '',
  tone = 'neutral',
  'aria-label': ariaLabel,
  ...badgeProps
}) {
  const hasText = hasVisibleText(children);
  const hasAccessibleLabel = typeof ariaLabel === 'string' && ariaLabel.trim() !== '';

  if (!hasText && !hasAccessibleLabel) {
    throw new TypeError('StatusBadge requires visible text or an accessible label.');
  }

  return (
    <span
      {...badgeProps}
      className={['product-status-badge', className].filter(Boolean).join(' ')}
      data-tone={tone}
      aria-label={ariaLabel}
    >
      {children}
    </span>
  );
}
