/* eslint-disable react-refresh/only-export-components -- canvas helpers are exported for contract tests. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useOnViewportChange,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Link } from 'react-router-dom';
import Button from './ui/Button';
import DecisionActions from './DecisionActions';
import { approvalConfirmationKey, canConfirmApproval, requiresInformedApproval, usableApprovalTarget } from '../lib/reviews/approvalConfirmation';
import EvidenceSummary from './EvidenceSummary';
import WorkflowPipeline from './WorkflowPipeline';
import { routes } from '../lib/routing/routes';
import { reviewApi } from '../features/reviews/reviewApi';
import { statusLabel, stepTitle } from '../utils/workflowHelpers';

const STATUS_ICONS = {
  done: '✓',
  active: '●',
  'review-needed': '▶',
  blocked: '⛔',
  ready: '▫',
  'not-started': '○',
};

export function canvasLevel(zoom) {
  if (zoom < 0.8) return 0;
  if (zoom < 1.25) return 1;
  return 2;
}

const WRAP_COLUMNS = 7;

export function buildCanvasNodes(steps, level, focusedKey = '', translators = {}) {
  const titleFor = translators.titleFor || ((step) => (step.approval === 'takeover' ? 'Takeover' : (translators.t ? stepTitle(step, translators.t) : step.title)));
  const labelFor = translators.labelFor || ((step) => (translators.t ? statusLabel(step, translators.t) : (step.status_label || step.status)));
  return steps.map((step, index) => ({
    id: step.key || `step-${index}`,
    position: {
      x: (index % WRAP_COLUMNS) * 250,
      y: Math.floor(index / WRAP_COLUMNS) * 130,
    },
    data: { focused: step.key === focusedKey, level, step, label: labelFor(step), title: titleFor(step) },
    type: 'stage',
    draggable: false,
    focusable: true,
  }));
}

function StageNode({ data }) {
  const { focused, level, step } = data;
  const isTakeover = step.approval === 'takeover';
  return (
    <div
      className={`workflow-canvas__node workflow-canvas__node--${step.status}${isTakeover ? ' workflow-canvas__node--takeover' : ''}${focused ? ' workflow-canvas__node--focused' : ''}`}
      data-status={step.status}
      data-level={level}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none' }} isConnectable={false} />
      <span className="workflow-canvas__node-icon" aria-hidden="true">{STATUS_ICONS[step.status] || '·'}</span>
      <span className="workflow-canvas__node-copy">
        <strong>{data.title}</strong>
        <small>{data.label}</small>
        {level >= 1 && step.feedback ? <em>{step.feedback}</em> : null}
        {level >= 2 && step.artifact_path ? <code>{step.artifact_path}</code> : null}
      </span>
      <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none' }} isConnectable={false} />
    </div>
  );
}

function CanvasInner({ fallback, onDecided, onRequestChanges, projectKey, reviews, runId = '', steps, t }) {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const [level, setLevel] = useState(0);
  const [expanded, setExpanded] = useState(null);
  const [focusedKey, setFocusedKey] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const nodeTypes = useMemo(() => ({ stage: StageNode }), []);

  useEffect(() => {
    setNodes(buildCanvasNodes(steps, level, focusedKey, {
      t,
      titleFor: (step) => stepTitle(step, t),
      labelFor: (step) => statusLabel(step, t),
    }));
  }, [focusedKey, level, setNodes, steps, t]);

  useOnViewportChange({
    onChange: ({ zoom }) => setLevel(canvasLevel(zoom)),
  });

  const handleNodeClick = useCallback((_, node) => {
    setExpanded((current) => (current === node.id ? null : node.id));
  }, []);

  const handleKeyDown = useCallback((event) => {
    if (event.defaultPrevented || event.target?.closest?.('input, textarea, select, [contenteditable="true"]')) return;
    const index = steps.findIndex((step) => (step.key || `step-${steps.indexOf(step)}`) === focusedKey);
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      const next = index < 0 ? 0 : Math.min(Math.max(index + (event.key === 'ArrowRight' ? 1 : -1), 0), steps.length - 1);
      setFocusedKey(steps[next].key || `step-${next}`);
    } else if (event.key === 'Enter' && focusedKey) {
      event.preventDefault();
      setExpanded(focusedKey);
    } else if (event.key === 'Escape') {
      setExpanded(null);
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      void zoomIn();
    } else if (event.key === '-') {
      event.preventDefault();
      void zoomOut();
    } else if (event.key.toLowerCase() === 'f') {
      event.preventDefault();
      void fitView();
    }
  }, [fitView, focusedKey, steps, zoomIn, zoomOut]);

  const edges = useMemo(() => steps.slice(0, -1).map((step, index) => ({
    id: `edge-${index}`,
    source: step.key || `step-${index}`,
    target: steps[index + 1].key || `step-${index + 1}`,
    type: 'smoothstep',
    animated: step.status === 'active',
  })), [steps]);

  if (fallback) {
    return (
      <>
        <WorkflowPipeline projectKey={projectKey} steps={steps} t={t} />
        <p className="workflow-canvas-fallback-note">{t('work.canvas.fallbackNote')}</p>
      </>
    );
  }
  if (!steps.length) return null;

  const expandedStep = expanded ? steps.find((step) => (step.key || `step-${steps.indexOf(step)}`) === expanded) : null;
  const actionableReview = expanded && expandedStep
    ? reviews.find((review) => review.approval === expandedStep.approval && review.is_actionable)
    : null;
  const evidence = expandedStep && actionableReview
    ? (typeof actionableReview.evidence_snapshot === 'object' && actionableReview.evidence_snapshot
      ? actionableReview.evidence_snapshot
      : {})
    : {};

  const expectedApproval = {
    run_id: runId, target_type: 'review', target_id: actionableReview?.approval || '',
    artifact_key: actionableReview?.artifact || '', artifact_version: actionableReview?.artifact_version || '',
  };
  const informedApproval = requiresInformedApproval(actionableReview?.recommendation);
  const approveDisabled = Boolean(busy) || !usableApprovalTarget(expectedApproval);

  const decide = async (decision, note) => {
    if (!expandedStep || !actionableReview || busy) return;
    if (decision === 'approved' && !canConfirmApproval({ disabled: approveDisabled, requiresReason: informedApproval, note })) return;
    setBusy(decision);
    setError(null);
    try {
      if (decision === 'approved') {
        await reviewApi.decideReview(
          projectKey,
          actionableReview.approval,
          decision,
          note,
          { expectedApproval },
        );
      } else {
        await reviewApi.decideReview(projectKey, actionableReview.approval, decision, note);
      }
      setExpanded(null);
      onDecided?.();
    } catch (caught) {
      setError(caught);
      if (['stale_approval', 'approval_reason_required'].includes(caught?.code)) await onDecided?.();
    } finally {
      setBusy('');
    }
  };

  const requestChanges = async (note) => {
    if (!actionableReview || !onRequestChanges) return;
    setBusy('changes_requested');
    setError(null);
    try {
      await onRequestChanges(actionableReview.approval, {
        run_id: runId,
        target_type: 'review',
        target_id: actionableReview.approval || '',
        artifact_key: actionableReview.artifact || '',
        artifact_version: actionableReview.artifact_version || '',
      }, note);
      setExpanded(null);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy('');
    }
  };

  const doneCount = steps.filter((s) => s.status === 'done').length;
  const activeCount = steps.filter((s) => s.status === 'active').length;
  const reviewCount = steps.filter((s) => s.status === 'review-needed').length;
  const blockedCount = steps.filter((s) => s.status === 'blocked').length;
  const progressPercent = Math.round((doneCount / (steps.length || 1)) * 100);

  return (
    <div
      className="workflow-canvas"
      role="region"
      aria-label={t('work.canvas.label')}
      onKeyDown={handleKeyDown}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={handleNodeClick}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.4}
        maxZoom={2.2}
        proOptions={{ hideAttribution: true }}
        fitView
      >
        <Background gap={28} />
        <Panel position="top-left" className="workflow-canvas-stats">
          <div className="workflow-canvas-progress">
            <span className="workflow-canvas-progress__label">
              {t('work.canvas.progress', { done: doneCount, total: steps.length, percent: progressPercent })}
            </span>
            <div
              className="workflow-canvas-progress__track"
              role="progressbar"
              aria-valuenow={progressPercent}
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <div className="workflow-canvas-progress__bar" style={{ width: `${progressPercent}%` }} />
            </div>
          </div>
          <div className="workflow-canvas-stats__badges">
            {doneCount > 0 ? <span className="workflow-canvas-badge workflow-canvas-badge--done">✓ {doneCount}</span> : null}
            {activeCount > 0 ? <span className="workflow-canvas-badge workflow-canvas-badge--active">● {activeCount}</span> : null}
            {reviewCount > 0 ? <span className="workflow-canvas-badge workflow-canvas-badge--review">▶ {reviewCount}</span> : null}
            {blockedCount > 0 ? <span className="workflow-canvas-badge workflow-canvas-badge--blocked">⛔ {blockedCount}</span> : null}
          </div>
        </Panel>
        <Panel position="top-right" className="workflow-canvas-controls">
          <Button variant="secondary" onClick={() => void zoomIn()} aria-label={t('work.canvas.zoomIn')}>+</Button>
          <Button variant="secondary" onClick={() => void zoomOut()} aria-label={t('work.canvas.zoomOut')}>−</Button>
          <Button variant="secondary" onClick={() => void fitView()} aria-label={t('work.canvas.fit')}>⌂</Button>
          <span className="workflow-canvas-level" role="status">{t(`work.canvas.level${level}`)}</span>
        </Panel>
      </ReactFlow>
      {expandedStep ? (
        <div className="workflow-canvas-detail" role="dialog" aria-label={t('work.canvas.detail')}>
          <header>
            <p className="product-eyebrow">{t('work.canvas.detail')}</p>
            <h3>{stepTitle(expandedStep, t)}</h3>
          </header>
          <p>{expandedStep.feedback || expandedStep.summary || ''}</p>
          {expandedStep.approval ? (
            <Link to={routes.projectReviewItem(projectKey, expandedStep.approval)}>
              {t('work.canvas.openReview')}
            </Link>
          ) : null}
          {expandedStep.artifact_path ? (
            <Link to={`${routes.projectKnowledge(projectKey)}?view=artifacts&artifact=${encodeURIComponent(expandedStep.artifact || '')}`}>
              {t('work.canvas.viewArtifact')}
            </Link>
          ) : null}
          {actionableReview ? (
            <>
              <section aria-label={t('review.recommendation.title')}>
                <p>{actionableReview.recommendation?.verdict
                  ? t(`review.recommendation.verdict.${actionableReview.recommendation.verdict}`)
                  : t('review.recommendation.none')}</p>
                {(Array.isArray(actionableReview.recommendation?.reasons)
                  ? actionableReview.recommendation.reasons : []).map((reason, index) => (
                  <p key={index}>{typeof reason === 'string' ? reason : reason?.text || ''}</p>
                ))}
              </section>
              <EvidenceSummary evidence={evidence} t={t} />
              <DecisionActions
                decisionContextKey={approvalConfirmationKey([projectKey, expectedApproval, actionableReview])}
                informedApproval={informedApproval}
                approveDisabled={approveDisabled}
                busy={busy}
                error={error}
                onDecision={decide}
                onRequestChanges={onRequestChanges ? requestChanges : undefined}
                t={t}
              />
            </>
          ) : null}
          <Button variant="secondary" onClick={() => setExpanded(null)}>{t('work.canvas.close')}</Button>
        </div>
      ) : null}
    </div>
  );
}

export default function WorkflowCanvas(props) {
  if (!props.steps?.length) return null;
  return (
    <ReactFlowProvider>
      <CanvasInner key={`${props.projectKey}:${props.runId}`} {...props} />
    </ReactFlowProvider>
  );
}
