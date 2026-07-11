import { useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Database,
} from 'lucide-react';

export default function WorkbenchTab({
  approvalReviews = [],
  actionLoading,
  handleApproveDecision,
  changesRequestedNote = '',
  setChangesRequestedNote,
  FilePath,
  getBadgeStyle = () => ({ bg: 'bg-surface-sunken', text: 'text-text-secondary', border: 'border-border-subtle' }),
}) {
  const [selectedGate, setSelectedGate] = useState(
    approvalReviews.length > 0 ? approvalReviews[0].approval : null
  );
  const [localDecisionType, setLocalDecisionType] = useState('changes_requested');
  const [showNoteKey, setShowNoteKey] = useState(null);

  // If selected gate is no longer available, fall back to first one
  const activeGate = approvalReviews.find((r) => r.approval === selectedGate) || approvalReviews[0];

  const handleSelectGate = (gateName) => {
    setSelectedGate(gateName);
    setShowNoteKey(null);
    setChangesRequestedNote('');
    setLocalDecisionType('changes_requested');
  };

  const getVerdictBadgeStyle = (verdict) => {
    switch (verdict?.toLowerCase()) {
      case 'approve':
      case 'approved':
        return 'bg-status-success-bg text-status-success-text border-status-success/30';
      case 'caution':
      case 'changes_requested':
        return 'bg-status-warning-bg text-status-warning-text border-status-warning/30';
      case 'reject':
      case 'rejected':
        return 'bg-status-danger-bg text-status-danger-text border-status-danger/30';
      default:
        return 'bg-surface-sunken text-text-secondary border-border-subtle';
    }
  };

  const statusBadge = (status) => {
    if (status === 'success' || status === 'passed') {
      return { label: 'Passed', cls: 'bg-status-success-bg text-status-success-text border-status-success/30' };
    }
    if (status === 'failed' || status === 'failure') {
      return { label: 'Failed', cls: 'bg-status-danger-bg text-status-danger-text border-status-danger/30' };
    }
    if (status === 'pending') {
      return { label: 'Pending', cls: 'bg-status-warning-bg text-status-warning-text border-status-warning/30' };
    }
    return { label: 'Missing', cls: 'bg-surface-sunken text-text-muted border-border-subtle' };
  };

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Review & Evidence Workbench</h2>
        <p className="text-xs text-text-muted">
          First-class operational desk for reviewing task contracts, auditing structural evidence, and verifying memory provenance.
        </p>
      </div>

      {approvalReviews.length === 0 ? (
        <div className="bg-surface-raised border-2 border-border-subtle rounded-3xl p-10 text-center max-w-3xl mx-auto space-y-6 shadow-card physics-card">
          <div className="w-16 h-16 bg-surface-sunken border-2 border-border-subtle rounded-2xl flex items-center justify-center mx-auto text-text-muted">
            <CheckCircle2 className="h-8 w-8 text-status-success" />
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-black text-text-primary uppercase tracking-wider">Workstation Idle — No Pending Gates</h3>
            <p className="text-xs text-text-muted max-w-md mx-auto leading-relaxed">
              All pipeline gates are resolved, and memory logs have been safely projection-synced. Use the Dev Loop to kick off new runs or consult the Docs tab to understand boundaries.
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          {/* Left panel: gate items */}
          <div className="lg:col-span-1 space-y-3">
            <div className="text-2xs font-extrabold uppercase tracking-widest text-text-muted px-1">
              Active Project Gates ({approvalReviews.length})
            </div>
            <div className="space-y-2">
              {approvalReviews.map((review) => {
                const isActive = activeGate?.approval === review.approval;
                const badge = getBadgeStyle(review.decision);
                return (
                  <button
                    key={review.approval}
                    onClick={() => handleSelectGate(review.approval)}
                    className={`w-full text-left p-4 rounded-2xl border-2 transition-all flex flex-col space-y-2 select-none cursor-pointer ${
                      isActive
                        ? 'bg-accent-bg border-accent shadow-sm'
                        : 'bg-surface-raised border-border-subtle hover:border-border-strong hover:bg-surface-sunken'
                    }`}
                  >
                    <div className="flex justify-between items-center w-full">
                      <span className="text-xs font-black uppercase tracking-wider text-text-primary">
                        {review.approval}
                      </span>
                      <span className={`px-2 py-0.5 rounded-full text-3xs font-extrabold border uppercase tracking-widest leading-none ${badge.bg} ${badge.text} ${badge.border}`}>
                        {review.decision?.replace('_', ' ') || 'pending'}
                      </span>
                    </div>
                    <div className="text-3xs text-text-muted font-mono truncate w-full">
                      ID: {review.execution_id}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right panel: detail workbench view */}
          {activeGate && (
            <div className="lg:col-span-2 space-y-6">
              {/* 1. Review Decision Card */}
              <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden flex flex-col p-6 space-y-6">
                <div className="border-b-2 border-border-subtle pb-3 flex justify-between items-center">
                  <div>
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">Gate Review</span>
                    <h3 className="font-extrabold text-sm text-text-secondary mt-0.5">
                      Decision Rationale
                    </h3>
                  </div>
                  <span className={`inline-block px-2.5 py-0.5 rounded-full text-2xs font-extrabold border uppercase tracking-wider leading-none ${getVerdictBadgeStyle(activeGate.reviewer_decision?.decision || 'pending')}`}>
                    {activeGate.reviewer_decision?.decision || 'Pending'}
                  </span>
                </div>

                <div className="space-y-4">
                  {/* Current decision status details */}
                  <div className="grid grid-cols-2 gap-4 bg-surface-sunken border border-border-subtle p-4 rounded-xl text-xs font-semibold text-text-secondary">
                    <div>
                      <div className="text-3xs text-text-muted uppercase tracking-wider mb-1">Current decision</div>
                      <div className="text-text-primary font-bold">
                        {activeGate.reviewer_decision?.decision
                          ? `${activeGate.reviewer_decision.decision.toUpperCase()} by ${activeGate.reviewer_decision.reviewer || 'unknown'}`
                          : 'No Decision Recorded'}
                      </div>
                    </div>
                    <div>
                      <div className="text-3xs text-text-muted uppercase tracking-wider mb-1">Reason / Note</div>
                      <div className="text-text-primary truncate">
                        {activeGate.reviewer_decision?.reason || 'n/a'}
                      </div>
                    </div>
                  </div>

                  {/* Decision Form if actionable */}
                  {activeGate.is_actionable && (
                    <div className="space-y-4 border-t border-border-subtle pt-4">
                      {showNoteKey === activeGate.approval && (
                        <div className="flex flex-col space-y-2">
                          <label className="text-xs font-bold text-text-secondary">
                            {localDecisionType === 'rejected'
                              ? 'Why is this rejected? Reject reason note is required.'
                              : 'What needs to change? The PRD will be revised to address this.'}
                          </label>
                          <textarea
                            value={changesRequestedNote}
                            onChange={(e) => setChangesRequestedNote(e.target.value)}
                            placeholder={localDecisionType === 'rejected' ? "Type reject reason here..." : "Type feedback here..."}
                            rows={3}
                            className="w-full p-3 border border-border-subtle bg-surface-raised text-text-secondary rounded-xl text-xs focus:ring-2 focus:ring-status-warning outline-none resize-none font-sans"
                          />
                          {changesRequestedNote.trim() === '' && (
                            <div className="text-status-warning-text text-2xs font-bold flex items-center space-x-2">
                              <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
                              <span>
                                {localDecisionType === 'rejected'
                                  ? 'Reject reason is empty. Please provide detail.'
                                  : 'Feedback note is empty. Please provide detail so the model knows what to revise.'}
                              </span>
                            </div>
                          )}
                        </div>
                      )}

                      <div className="flex justify-end space-x-2">
                        {showNoteKey === activeGate.approval && (
                          <button
                            type="button"
                            onClick={() => {
                              setShowNoteKey(null);
                              setChangesRequestedNote('');
                              setLocalDecisionType('changes_requested');
                            }}
                            disabled={actionLoading}
                            className="bg-surface-raised border-2 border-border-subtle shadow-button text-text-secondary font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer"
                          >
                            Cancel
                          </button>
                        )}

                        {localDecisionType === 'rejected' && showNoteKey === activeGate.approval ? (
                          <button
                            type="button"
                            onClick={() => handleApproveDecision(activeGate.approval, 'rejected', '', changesRequestedNote)}
                            disabled={actionLoading || changesRequestedNote.trim() === ''}
                            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle shadow-button text-status-danger font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5 cursor-pointer"
                          >
                            {actionLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                            <span>Submit Reject</span>
                          </button>
                        ) : showNoteKey !== activeGate.approval ? (
                          <button
                            type="button"
                            onClick={() => {
                              setLocalDecisionType('rejected');
                              setShowNoteKey(activeGate.approval);
                            }}
                            disabled={actionLoading}
                            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle shadow-button text-status-danger font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5 cursor-pointer"
                          >
                            <span>Reject</span>
                          </button>
                        ) : null}

                        {localDecisionType === 'changes_requested' && showNoteKey === activeGate.approval ? (
                          <button
                            type="button"
                            onClick={() => handleApproveDecision(activeGate.approval, 'changes_requested', '', changesRequestedNote)}
                            disabled={actionLoading || changesRequestedNote.trim() === ''}
                            className="bg-surface-raised hover:bg-status-warning-bg border-2 border-border-subtle shadow-button text-status-warning font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5 cursor-pointer"
                          >
                            {actionLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                            <span>Submit Request</span>
                          </button>
                        ) : showNoteKey !== activeGate.approval ? (
                          <button
                            type="button"
                            onClick={() => {
                              setLocalDecisionType('changes_requested');
                              setShowNoteKey(activeGate.approval);
                            }}
                            disabled={actionLoading}
                            className="bg-surface-raised hover:bg-status-warning-bg border-2 border-border-subtle shadow-button text-status-warning font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5 cursor-pointer"
                          >
                            <span>Request Changes</span>
                          </button>
                        ) : null}

                        {showNoteKey !== activeGate.approval && (
                          <button
                            type="button"
                            onClick={() => handleApproveDecision(activeGate.approval, 'approved')}
                            disabled={actionLoading}
                            className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5 cursor-pointer"
                          >
                            {actionLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                            <span>Approve Gate</span>
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Decision Timeline History */}
                  {(() => {
                    const logs = (activeGate.evidence?.ledger_events || []).filter(
                      (e) => e.payload?.transition === 'review_decision_recorded'
                    );
                    if (logs.length > 0) {
                      return (
                        <div className="border-t border-border-subtle pt-4 space-y-3">
                          <h4 className="text-3xs font-extrabold uppercase tracking-widest text-text-muted">
                            Recorded Decisions Log
                          </h4>
                          <div className="space-y-2">
                            {logs.map((item, idx) => {
                              const badge = getVerdictBadgeStyle(item.payload?.decision);
                              return (
                                <div
                                  key={idx}
                                  className="flex items-start justify-between bg-surface-sunken p-3 rounded-xl border border-border-subtle text-xs"
                                >
                                  <div className="space-y-1">
                                    <div className="flex items-center space-x-2">
                                      <span className={`px-2 py-0.5 rounded-full text-3xs font-extrabold border uppercase tracking-wider leading-none ${badge}`}>
                                        {item.payload?.decision}
                                      </span>
                                      <span className="font-bold text-text-secondary">
                                        by {item.payload?.reviewer || 'system'}
                                      </span>
                                    </div>
                                    {item.payload?.reason && (
                                      <p className="text-3xs text-text-secondary italic">
                                        Note: "{item.payload.reason}"
                                      </p>
                                    )}
                                    {item.payload?.decision_origin && (
                                      <div className="text-3xs text-text-muted">
                                        Origin: {item.payload.decision_origin} | Mode: {item.payload.review_mode || 'n/a'}
                                      </div>
                                    )}
                                  </div>
                                  <span className="text-3xs text-text-muted font-mono shrink-0">
                                    {new Date(item.created_at).toLocaleString()}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    }
                    return null;
                  })()}
                </div>
              </div>

              {/* 2. Evidence Audit Card */}
              <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 space-y-4">
                <div>
                  <h4 className="text-xs font-black uppercase tracking-wider text-text-primary">
                    Evidence Audit Desk
                  </h4>
                  <p className="text-3xs text-text-muted">
                    Structural verification matrix mapped to runtime execution and artifacts.
                  </p>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  {/* Artifact */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">Artifact</span>
                    {activeGate.artifact_path ? (
                      <div>
                        <span className="text-3xs font-bold text-status-success-text bg-status-success-bg px-2 py-0.5 rounded-full border border-status-success/30">
                          Available
                        </span>
                        <div className="text-3xs text-text-secondary mt-1.5 truncate">
                          <FilePath path={activeGate.artifact_path} segments={1} />
                        </div>
                      </div>
                    ) : (
                      <span className="text-3xs font-bold text-status-danger-text bg-status-danger-bg px-2 py-0.5 rounded-full border border-status-danger/30 uppercase tracking-wider self-start inline-flex items-center gap-0.5">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        Missing
                      </span>
                    )}
                  </div>

                  {/* Blockers */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">Blockers</span>
                    {typeof activeGate.evidence?.evidence_snapshot?.blocker_count === 'number' ? (
                      activeGate.evidence.evidence_snapshot.blocker_count > 0 ? (
                        <span className="text-3xs font-bold text-status-danger-text bg-status-danger-bg px-2 py-0.5 rounded-full border border-status-danger/30 self-start">
                          {activeGate.evidence.evidence_snapshot.blocker_count}
                        </span>
                      ) : (
                        <span className="text-3xs font-bold text-text-muted bg-surface-sunken px-2 py-0.5 rounded-full border border-border-subtle self-start">
                          0
                        </span>
                      )
                    ) : (
                      <span className="text-3xs font-bold text-text-muted bg-surface-sunken px-2 py-0.5 rounded-full border border-border-subtle self-start">
                        Unknown
                      </span>
                    )}
                  </div>

                  {/* Debate */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">Gate Debate</span>
                    {activeGate.evidence?.evidence_snapshot?.debate_count > 0 ? (
                      <span className="text-3xs font-bold text-status-info-text bg-status-info-bg px-2 py-0.5 rounded-full border border-status-info/30 self-start">
                        {activeGate.evidence.evidence_snapshot.debate_count} Messages
                      </span>
                    ) : (
                      <span className="text-3xs font-bold text-text-muted bg-surface-sunken px-2 py-0.5 rounded-full border border-border-subtle self-start">
                        None
                      </span>
                    )}
                  </div>

                  {/* CI */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">CI status</span>
                    {(() => {
                      const sb = statusBadge(activeGate.evidence?.evidence_snapshot?.ci_status);
                      return (
                        <span className={`text-3xs font-bold px-2 py-0.5 rounded-full border uppercase tracking-wider self-start ${sb.cls}`}>
                          {sb.label}
                        </span>
                      );
                    })()}
                  </div>

                  {/* Smoke */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">Smoke tests</span>
                    {(() => {
                      const sb = statusBadge(activeGate.evidence?.evidence_snapshot?.smoke_status);
                      return (
                        <span className={`text-3xs font-bold px-2 py-0.5 rounded-full border uppercase tracking-wider self-start ${sb.cls}`}>
                          {sb.label}
                        </span>
                      );
                    })()}
                  </div>

                  {/* QA */}
                  <div className="bg-surface-raised border border-border-subtle rounded-xl p-3 flex flex-col justify-between space-y-2">
                    <span className="text-3xs font-bold text-text-muted uppercase tracking-wider">QA Verification</span>
                    {(() => {
                      const sb = statusBadge(activeGate.evidence?.evidence_snapshot?.qa_status);
                      return (
                        <span className={`text-3xs font-bold px-2 py-0.5 rounded-full border uppercase tracking-wider self-start ${sb.cls}`}>
                          {sb.label}
                        </span>
                      );
                    })()}
                  </div>
                </div>

                {/* Warnings Section (Enforced missing warning/fallback, NEVER showing success on missing evidence) */}
                <div className="space-y-2 pt-2">
                  {!activeGate.artifact_path && (
                    <div className="bg-status-danger-bg text-status-danger-text border border-status-danger/30 rounded-xl p-3 text-3xs font-bold flex items-center space-x-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      <span>
                        MISSING-EVIDENCE: Artifact path is unavailable. Review cannot proceed without target content.
                      </span>
                    </div>
                  )}
                  {activeGate.evidence?.evidence_snapshot?.ci_status === 'failed' && (
                    <div className="bg-status-danger-bg text-status-danger-text border border-status-danger/30 rounded-xl p-3 text-3xs font-bold flex items-center space-x-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      <span>CI build failure recorded. Review requires passing integration checks.</span>
                    </div>
                  )}
                  {activeGate.evidence?.evidence_snapshot?.smoke_status === 'failed' && (
                    <div className="bg-status-danger-bg text-status-danger-text border border-status-danger/30 rounded-xl p-3 text-3xs font-bold flex items-center space-x-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      <span>Smoke verification failed. Operational preflights must pass.</span>
                    </div>
                  )}
                </div>
              </div>

              {/* 3. Memory & Provenance Card */}
              <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 space-y-4">
                <div>
                  <h4 className="text-xs font-black uppercase tracking-wider text-text-primary flex items-center gap-2">
                    <Database className="h-4 w-4 text-accent" />
                    Memory Provenance Audit
                  </h4>
                  <p className="text-3xs text-text-muted">
                    Trace log links mapping the run sequence to the multi-agent compounding memory ledger.
                  </p>
                </div>

                <div className="space-y-4 text-xs">
                  {/* Integrity Indicator */}
                  <div className="flex items-center justify-between bg-surface-sunken border border-border-subtle p-3 rounded-xl">
                    <span className="font-semibold text-text-secondary">Provenance Integrity</span>
                    {activeGate.memory_provenance_integrity?.complete === true ? (
                      <span className="px-2 py-0.5 rounded-full text-3xs font-bold bg-status-success-bg text-status-success-text border border-status-success/30 uppercase tracking-wider">
                        Complete
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full text-3xs font-bold bg-status-warning-bg text-status-warning-text border border-status-warning/30 uppercase tracking-wider">
                        Pending
                      </span>
                    )}
                  </div>

                  {/* Provenance refs */}
                  <div className="space-y-2">
                    <div className="text-3xs font-extrabold uppercase tracking-widest text-text-muted">
                      Memory Event Refs ({activeGate.memory_refs?.length || 0})
                    </div>
                    {activeGate.memory_refs && activeGate.memory_refs.length > 0 ? (
                      <div className="max-h-32 overflow-y-auto border border-border-subtle rounded-xl bg-surface-sunken p-3 font-mono text-3xs text-text-secondary space-y-1">
                        {activeGate.memory_refs.map((ref, idx) => (
                          <div key={idx} className="flex justify-between items-center py-0.5 border-b border-border-subtle last:border-0">
                            <span className="font-bold text-text-primary select-all">
                              {ref.memory_event_id || ref.ledger_event_id}
                            </span>
                            <span className="text-text-muted text-[10px]">
                              {ref.role || 'system'}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="bg-surface-sunken border border-border-subtle rounded-xl p-4 text-center text-text-muted italic text-3xs font-semibold">
                        No memory provenance refs recorded for this gate.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
