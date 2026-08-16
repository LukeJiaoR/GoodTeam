/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useMemo, useState } from 'react';

const ToastContext = createContext({
  show: () => {},
  success: () => {},
  error: () => {},
  info: () => {},
  warning: () => {},
  dismiss: () => {},
});

const DEFAULT_DURATION = 3500;

export function useToast() {
  return useContext(ToastContext);
}

const ICONS = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
};

export function ToastContainer({ onDismiss, toasts }) {
  if (!toasts.length) return null;
  return (
    <div className="product-toast-container" role="region" aria-label="Notifications" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`product-toast product-toast--${toast.type}`}
          role={toast.type === 'error' ? 'alert' : 'status'}
        >
          <span className="product-toast-icon" aria-hidden="true">
            {ICONS[toast.type] || ICONS.info}
          </span>
          <span className="product-toast-message">{toast.message}</span>
          <button
            type="button"
            className="product-toast-close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Close notification"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const show = useCallback((message, type = 'info', duration = DEFAULT_DURATION) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const toast = { id, message, type, duration };
    setToasts((current) => [...current, toast]);

    if (duration > 0) {
      setTimeout(() => {
        dismiss(id);
      }, duration);
    }
    return id;
  }, [dismiss]);

  const success = useCallback((msg, duration) => show(msg, 'success', duration), [show]);
  const error = useCallback((msg, duration) => show(msg, 'error', duration), [show]);
  const info = useCallback((msg, duration) => show(msg, 'info', duration), [show]);
  const warning = useCallback((msg, duration) => show(msg, 'warning', duration), [show]);

  const value = useMemo(() => ({
    dismiss,
    error,
    info,
    show,
    success,
    warning,
  }), [dismiss, error, info, show, success, warning]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}
