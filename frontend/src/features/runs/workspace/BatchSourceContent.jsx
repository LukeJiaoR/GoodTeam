/**
 * [INPUT]: 依赖requestJson、来源内容投影与批次只读 API
 * [OUTPUT]: 提供BatchSourceContent
 * [POS]: 绑定候选和检查回执后展示报告，不推进执行
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { useEffect, useState } from 'react';
import { requestJson } from '../../../lib/api/client';
import { resultEvidenceSections } from '../../../lib/presentation/sourceEvidence';
import { SourceEvidenceContent } from '../../../components/ui/SourceEvidence';

// Mounted only inside an explicitly opened panel. These reads never advance work.
export default function BatchSourceContent({ projectKey, runId, expectedCandidate, t }) {
  const [result, setResult] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const batch = await requestJson(`/api/projects/${encodeURIComponent(projectKey)}/iteration-batches/${encodeURIComponent(runId)}`,
          { signal: controller.signal, cache: 'no-store' });
        if (expectedCandidate && (batch.quality?.candidate_commit || batch.integration?.candidate_commit) !== expectedCandidate) {
          if (!controller.signal.aborted) setResult({ changed: true });
          return;
        }
        let sections = resultEvidenceSections(batch, t);
        let reviewUnavailable = false;
        const quality = batch.quality;
        if (quality?.execution_id && batch.project_root && quality.review_status === 'machine_recorded') {
          try {
            const workbench = await requestJson(`/api/executions/${encodeURIComponent(quality.execution_id)}/review-workbench?project_root=${encodeURIComponent(batch.project_root)}`,
              { signal: controller.signal, cache: 'no-store' });
            const report = workbench.candidate_review_report;
            if (workbench.execution_id !== quality.execution_id || !report?.available
                || report.head_sha !== quality.candidate_commit
                || !report.validation_receipt_id
                || report.validation_receipt_id !== quality.validation?.receipt?.receipt_id) {
              throw new Error('Evidence changed');
            }
            sections = [...sections, ...resultEvidenceSections({ candidate_review_report: report }, t)];
          } catch {
            reviewUnavailable = true;
          }
        }
        if (!controller.signal.aborted) setResult({ sections, reviewUnavailable });
      } catch {
        if (!controller.signal.aborted) setResult({ unavailable: true });
      }
    };
    load();
    return () => controller.abort();
  }, [projectKey, runId, expectedCandidate, t]);
  if (!result) return <p role="status">{t('product.evidence.loading')}</p>;
  if (result.unavailable) return <p role="status">{t('product.evidence.unavailable')}</p>;
  if (result.changed) return <p role="status">{t('product.evidence.changed')}</p>;
  return <>
    {result.sections.length ? <SourceEvidenceContent sections={result.sections} />
      : <p>{t('product.evidence.empty')}</p>}
    {result.reviewUnavailable ? <p role="status">{t('product.evidence.reviewUnavailable')}</p> : null}
  </>;
}
