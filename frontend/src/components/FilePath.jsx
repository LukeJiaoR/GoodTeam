export default function FilePath({ path, segments = 1 }) {
  if (!path) return <span className="text-text-muted dark:text-zinc-550 italic text-xs">not available</span>;

  const pathStr = String(path);
  const parts = pathStr.split(/[/\\]/);

  let displayName = pathStr;
  if (parts.length > 0) {
    if (segments === 1) {
      displayName = parts[parts.length - 1];
    } else if (segments === 2) {
      displayName = parts.slice(-2).join('/');
    }
  }

  return (
    <span className="group relative inline-flex max-w-full font-mono text-[11px] leading-none">
      <code className="bg-surface-sunken text-text-secondary border border-border-subtle px-2 py-0.5 rounded-lg break-all cursor-help hover:bg-zinc-200 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-700 transition-colors">
        {displayName}
      </code>
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 rounded-xl bg-zinc-950 px-3 py-2 text-2xs text-white opacity-0 transition-opacity group-hover:opacity-100 dark:bg-black border border-zinc-800 shadow-xl w-max max-w-[28rem] whitespace-normal break-words font-mono leading-relaxed">
        {pathStr}
      </span>
    </span>
  );
}
