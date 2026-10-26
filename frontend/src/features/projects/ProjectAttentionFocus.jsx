import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import Button from '../../components/ui/Button';
import { routes } from '../../lib/routing/routes';
import { focusWorkContext } from '../runs/workspace/currentWorkContext';
import { projectApi } from './projectApi';
import { normalizeProjectsPayload } from './projectList';
import { resolveAttentionFocus, attentionReason, attentionTitle } from './projectAttention';
import './projectAttention.css';

export default function ProjectAttentionFocus({ selection, projectKey, runId, data, freshness, refresh, reducedMotion, surfaceReady = true, t }) {
  const [requestId, setRequestId] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const refreshOnEntry = useRef(Boolean(data));
  const notice = useRef(null);
  const current = useRef(null);
  useEffect(() => {
    if (selection.invalid) return undefined;
    const controller = new AbortController();
    // Reuse Work's initial state read. Starting another refresh from a child
    // effect would race the parent's queued initial read and supersede it.
    const requiresRefresh = refreshOnEntry.current || requestId > 0;
    Promise.all([projectApi.listProjects({ signal: controller.signal }),
      requiresRefresh ? refresh({ signal: controller.signal }) : null])
      .then(([payload, refreshed]) => {
        if (controller.signal.aborted) return;
        setSnapshot({ requestId, projects: requiresRefresh && !refreshed ? null : normalizeProjectsPayload(payload) });
      }).catch(() => {
        if (!controller.signal.aborted) setSnapshot({ requestId, projects: null });
      });
    return () => controller.abort();
  }, [projectKey, runId, selection, refresh, requestId]);
  const result = resolveAttentionFocus({ selection, projectKey, runId, data,
    projects: snapshot?.projects || [],
    freshness: snapshot?.requestId !== requestId ? 'loading' : !snapshot.projects ? 'unavailable' : freshness });
  const focusKey = JSON.stringify([requestId, result.status, result.item?.id, result.target, surfaceReady]);
  useLayoutEffect(() => {
    current.current = { focusKey, result };
    return () => { current.current = null; };
  });
  useEffect(() => {
    if (result.status === 'loading') return;
    const root = notice.current?.closest('.product-work-page');
    if (result.status === 'current' && result.target && surfaceReady && root) {
      focusWorkContext(root, result.target, {
        reducedMotion,
        isCurrent: () => current.current?.focusKey === focusKey,
        matches: (node) => result.item.source === 'assistant'
          ? node.dataset.proposalId === result.item.target_id
          : result.item.source === 'readiness'
            ? result.target === 'projectMode' ? node.dataset.attentionId === 'project-mode'
              : `readiness:${node.dataset.attentionId?.slice('project-preparation:'.length)}` === result.item.id
            : `workflow:${runId}:${node.dataset.attentionId}` === result.item.id,
      });
    } else {
      // Let the conversation composer finish its mount-time autofocus before
      // restoring the explicit attention destination, including changed items.
      const frame = requestAnimationFrame(() => {
        if (current.current?.focusKey === focusKey) notice.current?.focus({ preventScroll: true });
      });
      return () => cancelAnimationFrame(frame);
    }
  // Status and exact identity, not polling object references, own focus.
  }, [focusKey, reducedMotion, runId]); // eslint-disable-line react-hooks/exhaustive-deps

  return <section ref={notice} className="project-attention-focus" tabIndex={-1}
    aria-label={t('projects.attention.focus.title')} data-status={result.status}>
    <h2>{t('projects.attention.focus.title')}</h2>
    <p role="status">{t(result.status === 'current' && !result.target ? 'projects.attention.focus.reportedFailure' : `projects.attention.focus.${result.status}`)}</p>
    {result.item ? <>
      <strong>{attentionTitle(result.item, t)}</strong>
      <p>{attentionReason(result.item, t)}</p>
      <small>{t(`projects.attention.source.${result.item.source}`)} · {result.item.target_id}</small>
    </> : null}
    <div className="project-attention-focus__actions">
      <Button variant="secondary" busy={result.status === 'loading'} onClick={() => setRequestId(value => value + 1)}>{t('projects.attention.refresh')}</Button>
      <Link to={routes.projects()}>{t('projects.attention.focus.back')}</Link>
    </div>
  </section>;
}
