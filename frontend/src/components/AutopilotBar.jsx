import { useState } from 'react';
import { Bot, Play, Square } from 'lucide-react';
import Button from './ui/Button';
import StatusBadge from './ui/StatusBadge';

const AUTOPILOT_LABELS = {
  idle: { label: '空闲', tone: 'neutral' },
  running: { label: '自主续跑中', tone: 'active' },
  stopping: { label: '正在停止', tone: 'warning' },
  completed: { label: '目标已达成', tone: 'success' },
  needs_human: { label: '等待人工介入', tone: 'warning' },
  max_rounds_reached: { label: '已达最大轮次', tone: 'warning' },
  stopped: { label: '已手动停止', tone: 'neutral' },
  failed: { label: '续跑异常', tone: 'danger' },
};

export default function AutopilotBar({ autopilot, busy = false, onStart, onStop }) {
  const [objective, setObjective] = useState('');
  const [maxRounds, setMaxRounds] = useState(autopilot?.max_rounds || 5);

  const isStopping = autopilot?.status === 'stopping';
  const running = Boolean(autopilot?.running || autopilot?.status === 'running' || isStopping);
  const statusInfo = AUTOPILOT_LABELS[autopilot?.status || 'idle'] || {
    label: autopilot?.status || '空闲',
    tone: 'neutral',
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const text = objective.trim();
    if (!text) return;
    onStart?.({ objective: text, max_rounds: Math.max(1, Math.min(20, Number(maxRounds) || 5)) });
  };

  return (
    <form className="v2-autopilot-bar" onSubmit={handleSubmit} aria-label="多轮自主续跑控制栏">
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', fontWeight: 660 }}>
        <Bot size={15} style={{ color: 'var(--product-accent-strong)' }} aria-hidden="true" />
        <span>多轮自主续跑</span>
        <StatusBadge tone={statusInfo.tone}>{statusInfo.label}</StatusBadge>
        {autopilot?.round > 0 ? (
          <StatusBadge tone="neutral">
            第 {autopilot.round}/{autopilot.max_rounds || maxRounds} 轮
          </StatusBadge>
        ) : null}
      </div>

      {running ? (
        <>
          <span
            style={{
              flex: 1,
              minWidth: '140px',
              fontSize: '0.82rem',
              color: 'var(--product-text-muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={autopilot?.objective}
          >
            目标：{autopilot?.objective}
          </span>
          <Button
            type="button"
            variant="danger"
            className="v2-btn-sm v2-btn-icon"
            busy={busy || isStopping}
            disabled={isStopping}
            onClick={onStop}
          >
            <Square size={12} aria-hidden="true" />
            {isStopping ? '正在停止' : '停止续跑'}
          </Button>
        </>
      ) : (
        <>
          <input
            type="text"
            className="v2-input v2-autopilot-bar__input"
            aria-label="多轮续跑目标"
            placeholder="输入多轮自主迭代目标（例如：补齐单元测试并修复 lint 警告）"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          />
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.78rem',
              color: 'var(--product-text-muted)',
            }}
          >
            轮次上限
            <input
              type="number"
              min={1}
              max={20}
              aria-label="最大轮次"
              className="v2-input v2-autopilot-bar__rounds"
              value={maxRounds}
              onChange={(e) => setMaxRounds(e.target.value)}
            />
          </label>
          <Button
            type="submit"
            variant="secondary"
            className="v2-btn-sm v2-btn-icon"
            busy={busy}
            disabled={!objective.trim()}
          >
            <Play size={13} aria-hidden="true" />
            启动续跑
          </Button>
        </>
      )}
    </form>
  );
}
