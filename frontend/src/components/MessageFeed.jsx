import { AlertTriangle, MessageSquare, RefreshCw } from 'lucide-react';

const KIND_STYLES = {
  ask: {
    border: 'border-l-status-warning',
    badge: 'bg-status-warning-bg text-status-warning-text border-status-warning/30',
  },
  reject: {
    border: 'border-l-status-danger',
    badge: 'bg-status-danger-bg text-status-danger-text border-status-danger/30',
  },
  assert: {
    border: 'border-l-status-info',
    badge: 'bg-status-info-bg text-status-info-text border-status-info/30',
  },
  propose: {
    border: 'border-l-status-success',
    badge: 'bg-status-success-bg text-status-success-text border-status-success/30',
  },
};

function getMessageTime(message) {
  const ts = message?.ts ? Date.parse(message.ts) : NaN;
  return Number.isNaN(ts) ? 0 : ts;
}

function formatMessageTime(timestamp) {
  if (!timestamp) return '';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return String(timestamp);

  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed);
}

function normalizeGate(gate) {
  const label = String(gate || '').trim();
  return label || 'General';
}

function groupMessages(messages) {
  const sorted = [...messages].sort((a, b) => getMessageTime(a) - getMessageTime(b));
  const groups = [];
  const indexByGate = new Map();

  sorted.forEach((message) => {
    const gate = normalizeGate(message.gate);
    if (!indexByGate.has(gate)) {
      indexByGate.set(gate, groups.length);
      groups.push({ gate, messages: [] });
    }
    groups[indexByGate.get(gate)].messages.push(message);
  });

  return groups;
}

function renderRefs(refs) {
  if (!Array.isArray(refs) || refs.length === 0) return '';
  return `refs ${refs.join(', ')}`;
}

export default function MessageFeed({ projectKey, runId, messages, loading, error }) {
  const hasRun = Boolean(projectKey && runId);
  const groups = groupMessages(messages || []);

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-bold text-text-primary">Messages</h2>
        <p className="text-xs text-text-muted">Full message bus feed for the active run.</p>
      </div>

      {!hasRun ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No active run selected for this project.
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center p-12 text-text-secondary font-bold">
          <RefreshCw className="h-5 w-5 animate-spin mr-2 text-accent" />
          <span>Loading message bus...</span>
        </div>
      ) : error ? (
        <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl text-status-danger-text flex items-center space-x-3 shadow-xs">
          <AlertTriangle className="h-6 w-6 shrink-0" />
          <div>
            <h3 className="font-bold text-sm">Message feed unavailable</h3>
            <p className="text-xs font-semibold leading-relaxed">{error}</p>
          </div>
        </div>
      ) : groups.length === 0 ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No messages on the bus for this run.
        </div>
      ) : (
        <div className="space-y-5">
          {groups.map((group) => (
            <section key={group.gate} className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden">
              <div className="px-6 py-4 bg-surface-sunken border-b-2 border-border-subtle flex items-center justify-between gap-3">
                <div className="flex items-center space-x-3 min-w-0">
                  <MessageSquare className="h-5 w-5 text-status-info shrink-0" />
                  <div className="min-w-0">
                    <span className="text-2xs font-bold text-text-muted uppercase tracking-wider">Gate</span>
                    <h3 className="font-bold text-sm text-text-primary uppercase tracking-wide mt-0.5 truncate">{group.gate}</h3>
                  </div>
                </div>
                <span className="bg-surface-raised text-text-secondary border-2 border-border-subtle px-3 py-1 rounded-xl text-2xs font-bold tracking-wider uppercase shrink-0">
                  {group.messages.length} messages
                </span>
              </div>

              <div className="p-4 space-y-3">
                {group.messages.map((message, idx) => {
                  const kind = String(message.kind || '').toLowerCase();
                  const style = KIND_STYLES[kind] || {
                    border: 'border-l-border-strong',
                    badge: 'bg-surface-sunken text-text-secondary border-border-subtle',
                  };
                  const toList = Array.isArray(message.to) ? message.to.filter(Boolean).join(', ') : '';
                  const refs = renderRefs(message.refs);
                  const reply = message.in_reply_to ? `in reply to ${message.in_reply_to}` : '';
                  const hints = [toList ? `to [${toList}]` : '', refs, reply].filter(Boolean);

                  return (
                    <article key={message.id || `${group.gate}-${idx}`} className={`bg-surface-raised border border-border-subtle border-l-4 ${style.border} rounded-xl p-4`}>
                      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-2xs font-bold uppercase tracking-wider text-text-secondary truncate">{message.from_role || 'unknown'}</span>
                          <span className={`text-2xs font-bold uppercase tracking-wider border px-2 py-0.5 rounded-full leading-none ${style.badge}`}>{kind || 'message'}</span>
                        </div>
                        {message.ts && (
                          <time className="text-2xs font-mono font-bold text-text-muted" dateTime={message.ts}>
                            {formatMessageTime(message.ts)}
                          </time>
                        )}
                      </div>
                      <p className="text-xs leading-relaxed text-text-primary whitespace-pre-wrap">{message.body || ''}</p>
                      {hints.length > 0 && (
                        <div className="mt-3 text-2xs font-mono font-bold text-text-muted break-all">
                          &rarr; {hints.join(' | ')}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
