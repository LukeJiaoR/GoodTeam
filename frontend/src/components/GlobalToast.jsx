import { AlertTriangle, Check } from 'lucide-react';

export default function GlobalToast({ message }) {
  if (!message) {
    return null;
  }

  return (
    <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-xl text-xs font-bold border-2 flex items-center space-x-2 animate-fade-in-down ${message.isError ? 'bg-status-danger-bg text-status-danger-text border-status-danger' : 'bg-status-success-bg text-accent-text border-accent'}`}>
      {message.isError ? <AlertTriangle className="h-4 w-4" /> : <Check className="h-4 w-4" />}
      <span>{message.text}</span>
    </div>
  );
}
