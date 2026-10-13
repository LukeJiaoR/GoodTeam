import { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import './workspacePanel.css';

export default function WorkspacePanel({ title, closeLabel, onClose, busy = false, compact = false, children }) {
  const ref = useRef(null);
  const closeRef = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const trigger = document.activeElement;
    const dialog = ref.current;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute('open', '');
    closeRef.current?.focus();
    return () => {
      dialog.close?.();
      document.body.style.overflow = overflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);
  const handleKeyDown = (event) => {
    if (event.key !== 'Tab' || event.target.closest('dialog') !== ref.current) return;
    const controls = [...ref.current.querySelectorAll('button, a[href], input, select, textarea, summary, [tabindex]')]
      .filter((node) => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length && !node.closest('[hidden], [inert]'));
    const first = controls[0];
    const last = controls.at(-1);
    if (!first) { event.preventDefault(); ref.current.focus(); return; }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  return <dialog ref={ref} className={`workspace-panel${compact ? ' workspace-panel--compact' : ''}`}
    aria-labelledby={titleId} aria-modal="true" tabIndex={-1} onKeyDown={handleKeyDown}
    onCancel={(event) => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }}>
    <header className="workspace-panel__header">
      <h2 id={titleId}>{title}</h2>
      <button ref={closeRef} type="button" className="product-button product-button--secondary" disabled={busy} onClick={onClose}>
        <X size={16} aria-hidden="true" />{closeLabel}
      </button>
    </header>
    <div className="workspace-panel__body">{children}</div>
  </dialog>;
}
