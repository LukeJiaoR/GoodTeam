export default function StatusBadge({
  children,
  className = '',
  tone = 'neutral',
  'aria-label': ariaLabel,
  ...badgeProps
}) {
  const hasVisibleText = children !== null
    && children !== undefined
    && !(typeof children === 'string' && children.trim() === '');
  const hasAccessibleLabel = typeof ariaLabel === 'string' && ariaLabel.trim() !== '';

  if (!hasVisibleText && !hasAccessibleLabel) {
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
