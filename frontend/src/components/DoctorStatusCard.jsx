import { RefreshCw } from 'lucide-react';
import Button from './ui/Button';
import StatusBadge from './ui/StatusBadge';

const TOOLS = [
  { key: 'codex', label: 'Codex CLI' },
  { key: 'claude', label: 'Claude CLI' },
  { key: 'git', label: 'Git' },
  { key: 'gh', label: 'GitHub CLI (gh)' },
];

export default function DoctorStatusCard({ doctor, loading = false, error = '', onRefresh }) {
  return (
    <section className="v2-card" aria-label="本地 CLI 与 GitHub 环境检查">
      <div className="v2-card__header">
        <div>
          <h2 className="v2-card__title">本地环境与 CLI 状态</h2>
          <p className="v2-card__subtitle">
            {doctor?.state_dir ? `状态目录：${doctor.state_dir}` : '检测本地 Codex / Claude / Git / GitHub CLI 就绪情况'}
          </p>
        </div>
        {onRefresh ? (
          <Button variant="secondary" className="v2-btn-sm v2-btn-icon" busy={loading} onClick={onRefresh}>
            {!loading ? <RefreshCw size={14} aria-hidden="true" /> : null}
            刷新检查
          </Button>
        ) : null}
      </div>

      {error ? (
        <p style={{ margin: 0, color: 'var(--product-danger)', fontSize: '0.86rem' }}>{error}</p>
      ) : (
        <div className="v2-doctor-grid">
          {TOOLS.map(({ key, label }) => {
            const info = doctor?.[key];
            const available = Boolean(info?.available);
            const ghAuth = key === 'gh' ? Boolean(info?.authenticated) : null;
            const tone = !info
              ? 'neutral'
              : key === 'gh'
                ? available && ghAuth
                  ? 'success'
                  : available
                    ? 'warning'
                    : 'danger'
                : available
                  ? 'success'
                  : 'danger';
            const badgeText = !info
              ? '检测中'
              : key === 'gh'
                ? available
                  ? ghAuth
                    ? `已登录${info.account ? ` @${info.account}` : ''}`
                    : '未登录 gh auth'
                  : '未安装'
                : available
                  ? '就绪'
                  : '未就绪';

            return (
              <div key={key} className="v2-doctor-item">
                <div className="v2-doctor-item__head">
                  <strong style={{ fontSize: '0.88rem' }}>{label}</strong>
                  <StatusBadge tone={tone}>{badgeText}</StatusBadge>
                </div>
                <span className="v2-doctor-item__ver" title={info?.version || info?.error || ''}>
                  {info?.version || info?.error || '—'}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
