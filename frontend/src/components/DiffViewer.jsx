import { useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import Button from './ui/Button';

function classifyLine(line) {
  if (line.startsWith('diff --git') || line.startsWith('--- ') || line.startsWith('+++ ')) {
    return 'file';
  }
  if (line.startsWith('@@')) return 'hunk';
  if (line.startsWith('+')) return 'add';
  if (line.startsWith('-')) return 'del';
  return 'ctx';
}

export default function DiffViewer({ diffText = '', emptyMessage = '暂无代码变更 diff。' }) {
  const [copied, setCopied] = useState(false);

  const { lines, additions, deletions } = useMemo(() => {
    if (!diffText) return { lines: [], additions: 0, deletions: 0 };
    const raw = diffText.split('\n');
    let add = 0;
    let del = 0;
    const classified = raw.map((text, idx) => {
      const kind = classifyLine(text);
      if (kind === 'add') add += 1;
      if (kind === 'del') del += 1;
      return { idx, text, kind };
    });
    return { lines: classified, additions: add, deletions: del };
  }, [diffText]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(diffText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Ignore clipboard failure in restricted environments.
    }
  };

  if (!diffText.trim()) {
    return (
      <div className="v2-card v2-card--muted" style={{ color: 'var(--product-text-muted)', fontSize: '0.88rem' }}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="v2-diff" data-testid="diff-viewer">
      <div className="v2-diff__toolbar">
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span>Unified Diff</span>
          <span style={{ color: 'var(--product-success)', fontWeight: 700 }}>+{additions}</span>
          <span style={{ color: 'var(--product-danger)', fontWeight: 700 }}>-{deletions}</span>
        </div>
        <Button variant="secondary" className="v2-btn-sm v2-btn-icon" onClick={handleCopy}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? '已复制' : '复制 Diff'}
        </Button>
      </div>
      <div className="v2-diff__body" role="region" aria-label="代码差异视图">
        {lines.map((line) => (
          <span
            key={line.idx}
            className={`v2-diff__line${line.kind !== 'ctx' ? ` v2-diff__line--${line.kind}` : ''}`}
          >
            {line.text || ' '}
          </span>
        ))}
      </div>
    </div>
  );
}
