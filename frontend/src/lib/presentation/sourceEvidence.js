/**
 * [INPUT]: 依赖批次报告、检查输出与候选审查报告
 * [OUTPUT]: 提供resultEvidenceSections
 * [POS]: 投影可读来源内容，排除请求对象和调试封装
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
// Select readable source material, never request bodies or execution envelopes.
export function resultEvidenceSections(source, t) {
  if (!source) return [];
  const sections = [];
  const add = (title, content, literal = false) => {
    if (typeof content === 'string' && content.trim()) sections.push({ title, content, literal });
  };
  for (const job of source.worker_results || source.jobs || []) {
    add([t('product.evidence.worker'), job.project_task_id].filter(Boolean).join(' · '), job.summary);
  }
  const validation = source.quality_evidence?.validation || source.quality?.validation || source.validation;
  const observation = validation?.receipt?.observation;
  if (validation?.available && observation) {
    const status = ['passed', 'failed'].includes(validation.check_status) ? validation.check_status : 'unknown';
    add(t('product.evidence.check'), t(`review.evidence.status.${status}`));
    const stdout = observation.output?.stdout;
    const stderr = observation.output?.stderr;
    add(t('product.evidence.output'), stdout, true);
    add(t('product.evidence.output'), stderr, true);
    if (typeof stdout !== 'string' && typeof stderr !== 'string') {
      add(t('product.evidence.output'), t('product.evidence.outputUnavailable'));
    }
  }
  const retained = source.candidate_review_report;
  const report = retained?.available ? retained.report : null;
  if (report) {
    add(t('product.evidence.review'), report.summary);
    add(t('product.evidence.review'), t('product.evidence.reviewBoundary'));
    for (const finding of report.findings || []) {
      add(finding.title || t('product.evidence.finding'), [finding.detail, finding.recommendation].filter(Boolean).join('\n\n'));
    }
    for (const check of report.checks || []) {
      const statusKey = `product.evidence.reviewCheck.${check.status}`;
      const status = t(statusKey);
      add(check.name || t('product.evidence.check'), [status === statusKey ? t('review.evidence.status.unknown') : status,
        check.detail].filter(Boolean).join('\n\n'));
    }
    for (const citation of report.evidence || []) {
      const location = [citation.path, citation.line_start].filter(Boolean).join(':');
      add(location || t('product.evidence.excerpt'), citation.quote, true);
      add(t('product.evidence.observation'), citation.observation);
    }
  }
  return sections;
}
