const ALERT_TONES = new Set(['danger', 'error']);

export default function InlineNotice({
  action,
  children,
  className = '',
  role,
  title,
  tone = 'info',
  ...noticeProps
}) {
  const resolvedRole = role || (ALERT_TONES.has(tone) ? 'alert' : 'status');

  return (
    <div
      {...noticeProps}
      className={['product-inline-notice', className].filter(Boolean).join(' ')}
      role={resolvedRole}
      data-tone={tone}
    >
      <div className="product-inline-notice__content">
        {title ? <strong className="product-inline-notice__title">{title}</strong> : null}
        {children ? <div className="product-inline-notice__description">{children}</div> : null}
      </div>
      {action ? <div className="product-inline-notice__action">{action}</div> : null}
    </div>
  );
}
