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
      {Array.isArray(evidence.reviewer_reports) && evidence.reviewer_reports.length > 0 ? (
        <section aria-label={t('review.reports.title')}>
          <h3>{t('review.reports.title')}</h3>
          <p>{t('review.reports.boundary')}</p>
          {evidence.reviewer_reports.map((report) => (
            <article key={report.job_id}>
              <h4>{report.profile_id || report.job_id}</h4>
              <p>{t('review.reports.process')}: {report.status || 'unknown'}</p>
              <p>{t('review.reports.identity')}: {report.job_id} · {report.artifact_version}</p>
              {report.report_status === 'available' ? <>
                <p>{t('review.reports.hash')}: {report.report_sha256}</p>
                <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{report.content}</pre>
              </> : <p role="status">{t('review.reports.unavailable')}</p>}
            </article>
          ))}
        </section>
      ) : null}
    </section>
  );
}
