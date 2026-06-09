import { AlertTriangle } from 'lucide-react';

export default function DevLoopWarningsPanel({ warnings }) {
  if (!Array.isArray(warnings) || warnings.length === 0) return null;

  return (
    <div className="bg-status-warning-bg border-2 border-status-warning/30 p-4 rounded-xl text-status-warning-text text-xs font-semibold space-y-1">
      <div className="flex items-center space-x-1.5">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span className="font-extrabold uppercase text-2xs tracking-wider">System Warnings</span>
      </div>
      <ul className="list-disc pl-5 font-medium space-y-1 mt-1 text-status-warning-text">
        {warnings.map((warning, idx) => (
          <li key={idx}>{warning}</li>
        ))}
      </ul>
    </div>
  );
}
