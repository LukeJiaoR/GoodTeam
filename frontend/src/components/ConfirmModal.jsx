import { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import Button from './ui/Button';

export default function ConfirmModal({
  open,
  title,
  description,
  children,
  confirmLabel = '确认',
  cancelLabel = '取消',
  confirmVariant = 'danger',
  busy = false,
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    cancelRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) {
        onCancel?.();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div className="v2-modal-backdrop" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="v2-modal v2-modal--sm"
      >
        <div className="v2-modal__header">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="projects-icon-button"
            onClick={onCancel}
            disabled={busy}
            aria-label="关闭对话框"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="v2-modal__body">
          {description ? <p style={{ margin: 0, color: 'var(--product-text-muted)' }}>{description}</p> : null}
          {children}
        </div>
        <div className="v2-modal__footer">
          <Button ref={cancelRef} variant="secondary" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} busy={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
