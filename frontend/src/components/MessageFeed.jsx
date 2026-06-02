import { AlertTriangle, MessageSquare, RefreshCw } from 'lucide-react';

const KIND_STYLES = {
  ask: {
    border: 'border-l-[#FF9600]',
    badge: 'bg-[#FFF0D4] text-[#B36B00] border-[#FF9600]/30',
  },
  reject: {
    border: 'border-l-[#FF4B4B]',
    badge: 'bg-[#FFE2E2] text-[#EA2B2B] border-[#FF4B4B]/30',
  },
  assert: {
    border: 'border-l-[#1CB0F6]',
    badge: 'bg-[#D0F0FF] text-[#0A7BAF] border-[#1CB0F6]/30',
  },
  propose: {
    border: 'border-l-[#58CC02]',
    badge: 'bg-[#E8F9D9] text-[#3D8B00] border-[#58CC02]/30',
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
      <div className="border-b-2 border-[#E5E5E5] dark:border-zinc-800 pb-2">
        <h2 className="text-base font-extrabold text-zinc-800 dark:text-zinc-200">Messages</h2>
        <p className="text-xs text-[#AFAFAF]">Full message bus feed for the active run.</p>
      </div>

      {!hasRun ? (
        <div className="bg-white dark:bg-zinc-950 border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
          No active run selected for this project.
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center p-12 text-zinc-500 font-bold">
          <RefreshCw className="h-5 w-5 animate-spin mr-2 text-[#58CC02]" />
          <span>Loading message bus...</span>
        </div>
      ) : error ? (
        <div className="bg-[#FFE2E2] border-2 border-[#FF4B4B] p-6 rounded-2xl text-[#EA2B2B] flex items-center space-x-3 shadow-xs">
          <AlertTriangle className="h-6 w-6 shrink-0" />
          <div>
            <h3 className="font-extrabold text-sm">Message feed unavailable</h3>
            <p className="text-xs font-semibold leading-relaxed">{error}</p>
          </div>
        </div>
      ) : groups.length === 0 ? (
        <div className="bg-white dark:bg-zinc-950 border-2 border-dashed border-[#E5E5E5] dark:border-zinc-800 rounded-2xl p-12 text-center text-[#AFAFAF] italic text-xs">
          No messages on the bus for this run.
        </div>
      ) : (
        <div className="space-y-5">
          {groups.map((group) => (
            <section key={group.gate} className="bg-white dark:bg-zinc-950 border-2 border-[#E5E5E5] dark:border-zinc-800 shadow-[0_4px_0_#D1D1D1] dark:shadow-[0_4px_0_#1a1a1a] rounded-2xl overflow-hidden">
              <div className="px-6 py-4 bg-zinc-50 dark:bg-zinc-900/50 border-b-2 border-[#E5E5E5] dark:border-zinc-800 flex items-center justify-between gap-3">
                <div className="flex items-center space-x-3 min-w-0">
                  <MessageSquare className="h-5 w-5 text-[#1CB0F6] shrink-0" />
                  <div className="min-w-0">
                    <span className="text-[10px] font-bold text-[#AFAFAF] uppercase tracking-wider">Gate</span>
                    <h3 className="font-extrabold text-sm text-zinc-800 dark:text-zinc-200 uppercase tracking-wide mt-0.5 truncate">{group.gate}</h3>
                  </div>
                </div>
                <span className="bg-[#F1F1F1] dark:bg-zinc-900 text-[#777777] dark:text-zinc-400 border-2 border-[#AFAFAF]/20 px-3 py-1 rounded-xl text-[10px] font-extrabold tracking-wider uppercase shrink-0">
                  {group.messages.length} messages
                </span>
              </div>

              <div className="p-4 space-y-3">
                {group.messages.map((message, idx) => {
                  const kind = String(message.kind || '').toLowerCase();
                  const style = KIND_STYLES[kind] || {
                    border: 'border-l-[#AFAFAF]',
                    badge: 'bg-[#F1F1F1] text-[#777777] border-[#AFAFAF]/30',
                  };
                  const toList = Array.isArray(message.to) ? message.to.filter(Boolean).join(', ') : '';
                  const refs = renderRefs(message.refs);
                  const reply = message.in_reply_to ? `in reply to ${message.in_reply_to}` : '';
                  const hints = [toList ? `to [${toList}]` : '', refs, reply].filter(Boolean);

                  return (
                    <article key={message.id || `${group.gate}-${idx}`} className={`bg-white dark:bg-zinc-950 border border-[#E5E5E5] dark:border-zinc-800 border-l-4 ${style.border} rounded-xl p-4`}>
                      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-[10px] font-extrabold uppercase tracking-wider text-zinc-700 dark:text-zinc-300 truncate">{message.from_role || 'unknown'}</span>
                          <span className={`text-[8px] font-extrabold uppercase tracking-wider border px-2 py-0.5 rounded-full leading-none ${style.badge}`}>{kind || 'message'}</span>
                        </div>
                        {message.ts && (
                          <time className="text-[9px] font-mono font-bold text-[#AFAFAF]" dateTime={message.ts}>
                            {formatMessageTime(message.ts)}
                          </time>
                        )}
                      </div>
                      <p className="text-[12px] leading-relaxed text-zinc-650 dark:text-zinc-350 whitespace-pre-wrap">{message.body || ''}</p>
                      {hints.length > 0 && (
                        <div className="mt-3 text-[10px] font-mono font-bold text-[#AFAFAF] break-all">
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
