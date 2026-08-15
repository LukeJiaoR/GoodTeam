import StatusBadge from './ui/StatusBadge';

function evidenceStatusLabel(value, t) {
  if (value === 'passed') return t('review.evidence.status.passed');
  if (value === 'failed') return t('review.evidence.status.failed');
  return t('review.evidence.status.unknown');
}

function evidenceTone(value) {
  if (value === 'passed') return 'success';
  if (value === 'failed') return 'danger';
  return 'pending';
}

export default function EvidenceSummary({ evidence, t }) {
  const hasEvidence = Object.keys(evidence).some((key) => ['ci_status', 'smoke_status', 'qa_status'].includes(key));
  if (!hasEvidence) return null;
  const blockerCount = Number(evidence.blocker_count) || 0;
  return (
    <section className="review-evidence-summary" aria-label={t('review.evidence.summary')}>
      <h2>{t('review.evidence.summary')}</h2>
      <div className="review-evidence-badges">
        <StatusBadge tone={evidenceTone(evidence.ci_status)}>{t('review.evidence.ci')}: {evidenceStatusLabel(evidence.ci_status, t)}</StatusBadge>
        <StatusBadge tone={evidenceTone(evidence.smoke_status)}>{t('review.evidence.smoke')}: {evidenceStatusLabel(evidence.smoke_status, t)}</StatusBadge>
        <StatusBadge tone={evidenceTone(evidence.qa_status)}>{t('review.evidence.qa')}: {evidenceStatusLabel(evidence.qa_status, t)}</StatusBadge>
        <StatusBadge tone={blockerCount > 0 ? 'danger' : 'neutral'}>{t('review.evidence.blockers', { count: blockerCount })}</StatusBadge>
      </div>
    </section>
  );
}
