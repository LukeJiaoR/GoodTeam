import { AlertTriangle } from 'lucide-react';
import Spinner from './Spinner';

export default function ProjectMainPanel({ loading, error, loadingLabel, children }) {
  return (
    <main className="flex-grow p-8 overflow-y-auto max-w-7xl w-full mx-auto space-y-6">
      {loading ? (
        <Spinner label={loadingLabel} />
      ) : error ? (
        <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl text-status-danger-text flex items-center space-x-3 shadow-xs">
          <AlertTriangle className="h-6 w-6 shrink-0" />
          <span className="font-bold text-sm">{error}</span>
        </div>
      ) : (
        children
      )}
    </main>
  );
}
