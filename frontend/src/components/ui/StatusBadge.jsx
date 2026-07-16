export default function StatusBadge({
  children,
  className = '',
  tone = 'neutral',
  ...badgeProps
}) {
  return (
    <span
      {...badgeProps}
      className={['product-status-badge', className].filter(Boolean).join(' ')}
      data-tone={tone}
    >
      {children}
    </span>
  );
}
