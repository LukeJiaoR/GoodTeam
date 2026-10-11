/* eslint-disable no-control-regex -- reject URL controls before URL normalization. */
// Navigation only. The server owns execution identity and evidence validity.
// Never derive an execution ID from the workflow's run_id.
export function executionEvidenceHref(mission) {
  const href = mission?.execution_evidence_href;
  const executionId = mission?.execution_id;
  if (mission?.source !== 'operator_queue' || mission.read_only
      || typeof href !== 'string' || typeof executionId !== 'string' || !executionId
      || /[\\/\u0000-\u0020\u007f]/.test(executionId) || ['.', '..'].includes(executionId)
      || !href.startsWith('/review-workbench/') || /[\\\u0000-\u0020\u007f]/.test(href)) return '';
  try {
    const url = new URL(href, 'https://goodteam.invalid');
    const segment = url.pathname.slice('/review-workbench/'.length);
    if (url.origin !== 'https://goodteam.invalid' || url.hash
        || !segment || segment.includes('/') || decodeURIComponent(segment) !== executionId
        || url.searchParams.size !== 1 || url.searchParams.getAll('project_root').length !== 1) return '';
    const root = url.searchParams.get('project_root');
    if (!root || !root.startsWith('/') || root.startsWith('//') || /[\\\u0000-\u001f\u007f]/.test(root)) return '';
    return href;
  } catch { return ''; }
}
