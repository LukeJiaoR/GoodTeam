import { RefreshCw } from 'lucide-react';

export default function Spinner({ label = 'Loading...' }) {
  return (
    <div className="flex justify-center items-center py-16">
      <RefreshCw className="h-10 w-10 text-accent animate-spin" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
