import {
  Check,
  Edit,
  HelpCircle,
  Lock,
  RefreshCw,
  Save,
  Sparkles,
  Unlock,
} from 'lucide-react';

export default function ApprovalsTab({
  approvedCount,
  totalApprovalsCount,
  approvalProgressPercent,
  approvalReviews,
  approvingAction,
  actionLoading,
  generatingPrd,
  editingArtifact,
  proposedRevision,
  editorContent,
  setEditorContent,
  openQuestions,
  fetchingOpenQuestions,
  openQuestionsAnswers,
  setOpenQuestionsAnswers,
  answeringOpenQuestions,
  visibleGateDebate,
  gateDebateProjectKey,
  gateDebateRunId,
  debateRun,
  refineLoading,
  selectedReviewNoteKey,
  changesRequestedNote,
  setChangesRequestedNote,
  setShowChangesRequestedNoteKey,
  FilePath,
  getBadgeStyle,
  getStatusLabel,
  handleEditArtifact,
  handleGeneratePrd,
  handleOpenQuestionsFromApproval,
  handleApproveDecision,
  getResolvingDebateRef,
  computeLineDiff,
  handleDiscardRefinement,
  handleAcceptRefinement,
  setEditingArtifact,
  handleRefineArtifact,
  handleSaveArtifact,
  suggestedAnswersForQuestion,
  handleApplyAnswers,
  handleRunDebate,
}) {
  const showChangesRequestedNoteKey = selectedReviewNoteKey;

  return (
                <div className="space-y-6">
                  <div className="border-b-2 border-border-subtle pb-2">
                    <h2 className="text-base font-extrabold text-text-primary">Approval Workflows</h2>
                    <p className="text-xs text-text-muted">Review generated artifacts and confirm project transitions.</p>
                  </div>

                  {/* Approvals top progress bar - height: 12px, border-radius: 6px, green bar */}
                  <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-3 max-w-2xl">
                    <div className="flex justify-between items-center text-xs font-extrabold text-text-muted uppercase tracking-wider">
                      <span>Approval Pipeline Progress</span>
                      <span className="text-accent-text font-extrabold">{approvedCount} of {totalApprovalsCount} Gates</span>
                    </div>
                    <div className="w-full bg-surface-sunken h-3 rounded-full overflow-hidden">
                      <div className="bg-accent h-full rounded-full transition-all duration-500" style={{ width: `${approvalProgressPercent}%` }} />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
                    {/* Left list of approvals - lock icon on left, right badge */}
                    <div className="lg:col-span-1 space-y-4">
                      {approvalReviews.map((review) => {
                        const isActionable = review.is_actionable;
                        const isApproved = review.decision === 'approved';
                        const isPending = review.decision === 'pending';
                        const isApproveLoading = approvingAction === `${review.approval}:approved`;
                        const badge = getBadgeStyle(review.decision);

                        const cardBg = isPending
                          ? 'bg-status-warning-bg'
                          : 'bg-surface-raised';
                        const cardBorder = isPending
                          ? 'border-status-warning'
                          : isApproved
                            ? 'border-accent'
                            : 'border-border-subtle';
                        const cardShadow = isPending
                          ? 'shadow-card hover:shadow-card'
                          : isApproved
                            ? 'shadow-card hover:shadow-card'
                            : 'shadow-card hover:shadow-card';

                        return (
                          <article
                            key={review.approval}
                            onClick={() => handleEditArtifact(review)}
                            className={`${cardBg} ${cardBorder} ${cardShadow} border-2 rounded-2xl p-5 space-y-3.5 cursor-pointer hover:-translate-y-0.5 transition-all duration-200 flex flex-col justify-between`}
                          >
                            <div className="flex justify-between items-center">
                              <div className="flex items-center space-x-2">
                                {isApproved ? (
                                  <Unlock className="h-4 w-4 text-accent shrink-0" />
                                ) : (
                                  <Lock className="h-4 w-4 text-text-muted shrink-0" />
                                )}
                                <h3 className="font-extrabold text-xs uppercase tracking-wider text-text-secondary">{review.approval} Review</h3>
                              </div>
                              <span className={`inline-block px-2.5 py-0.5 rounded-full text-2xs font-extrabold border capitalize tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>
                                {getStatusLabel(review.decision)}
                              </span>
                            </div>

                            <dl className="grid grid-cols-2 gap-x-2 text-2xs text-text-secondary pt-2 border-t border-border-subtle font-bold">
                              <div>
                                <dt className="text-text-muted uppercase text-2xs mb-0.5">Artifact</dt>
                                <dd className="text-text-secondary uppercase">{review.artifact}</dd>
                              </div>
                              <div>
                                <dt className="text-text-muted uppercase text-2xs mb-0.5">File Path</dt>
                                <dd><FilePath path={review.artifact_path} segments={1} /></dd>
                              </div>
                            </dl>

                            {isActionable && (
                              <div className="bg-status-success-bg text-status-success-text border-2 border-status-success/30 text-2xs font-extrabold p-2 rounded-xl text-center uppercase tracking-wider">
                                Action Required
                              </div>
                            )}

                            <div className="flex justify-end space-x-2 pt-1">
                              {review.approval === 'prd' && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleGeneratePrd();
                                  }}
                                  disabled={generatingPrd}
                                  className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <RefreshCw className={`h-3 w-3 ${generatingPrd ? 'animate-spin' : ''}`} />
                                  <span>{generatingPrd ? 'Generating...' : 'Generate PRD'}</span>
                                </button>
                              )}
                              {review.artifact_path && (review.artifact_path.endsWith('.md') || review.artifact_path.endsWith('.markdown')) && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleEditArtifact(review);
                                  }}
                                  className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <Edit className="h-3 w-3" />
                                  <span>Edit</span>
                                </button>
                              )}
                              {review.artifact_path && (review.artifact_path.endsWith('.md') || review.artifact_path.endsWith('.markdown')) && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleOpenQuestionsFromApproval(review);
                                  }}
                                  className="bg-surface-raised hover:bg-status-info-bg border-2 border-status-info/30 shadow-button text-status-info-text font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                >
                                  <HelpCircle className="h-3 w-3" />
                                  <span>Questions</span>
                                </button>
                              )}
                              {isActionable && (
                                <div className="flex flex-col items-end">
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleApproveDecision(review.approval, 'approved', getResolvingDebateRef(review.approval));
                                    }}
                                    disabled={actionLoading}
                                    className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1"
                                  >
                                    {isApproveLoading ? (
                                      <RefreshCw className="h-3 w-3 animate-spin" />
                                    ) : (
                                      <Check className="h-3 w-3" />
                                    )}
                                    <span>{isApproveLoading ? 'Approving...' : 'Approve'}</span>
                                  </button>
                                  {isApproveLoading && (
                                    <span className="text-2xs text-text-secondary mt-1 text-right animate-pulse max-w-[150px] leading-tight">
                                      Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </article>
                        );
                      })}

                      {approvalReviews.length === 0 && (
                        <div className="bg-surface-raised border border-border-subtle p-8 text-center rounded-2xl text-text-secondary italic">
                          No approvals recorded in this workflow node state.
                        </div>
                      )}
                    </div>

                    {/* Right detail view / markdown editor & preview */}
                    <div className="lg:col-span-2 space-y-4">
                      {editingArtifact ? (
                        proposedRevision ? (
                          <div className="bg-surface-raised border-2 border-status-info shadow-card rounded-2xl overflow-hidden flex flex-col min-h-[500px]">
                            <div className="px-6 py-4 bg-status-info-bg border-b-2 border-status-info flex justify-between items-center">
                              <div>
                                <span className="text-2xs font-bold text-status-info-text uppercase tracking-wider">Refinement Review</span>
                                <h3 className="font-extrabold text-sm text-status-info-text mt-0.5">{editingArtifact.toUpperCase()}</h3>
                              </div>
                              <span className="text-2xs font-mono font-bold text-status-info-text uppercase tracking-wider">
                                +{proposedRevision.diff_summary?.added_lines || 0} / -{proposedRevision.diff_summary?.removed_lines || 0} lines
                              </span>
                            </div>

                            <div className="p-6 flex-grow grid grid-cols-1 lg:grid-cols-2 gap-6 bg-surface-raised">
                              <div className="flex flex-col h-96">
                                <span className="text-xs font-bold text-text-secondary mb-2">Line-by-line comparison</span>
                                <div className="flex-grow overflow-auto bg-surface-sunken border-2 border-border-subtle rounded-2xl p-4 font-mono text-2xs leading-relaxed text-text-secondary">
                                  {(() => {
                                    const diff = computeLineDiff(editorContent, proposedRevision.content);
                                    return diff.map((line, idx) => {
                                      let lineClass = "py-0.5 px-2 flex items-start space-x-2 ";
                                      let prefix = " ";
                                      if (line.type === 'added') {
                                        lineClass += "bg-status-success-bg text-status-success-text";
                                        prefix = "+";
                                      } else if (line.type === 'removed') {
                                        lineClass += "bg-status-danger-bg text-status-danger-text line-through";
                                        prefix = "-";
                                      } else {
                                        lineClass += "text-text-secondary";
                                      }
                                      return (
                                        <div key={idx} className={lineClass}>
                                          <span className="select-none text-text-secondary w-3 text-right">{prefix}</span>
                                          <span className="whitespace-pre-wrap flex-grow">{line.text || ' '}</span>
                                        </div>
                                      );
                                    });
                                  })()}
                                </div>
                              </div>

                              <div className="flex flex-col h-96">
                                <span className="text-xs font-bold text-text-secondary mb-2">Full Proposed Text</span>
                                <textarea
                                  readOnly
                                  value={proposedRevision.content}
                                  className="w-full flex-grow bg-surface-sunken text-text-secondary border-2 border-border-subtle rounded-2xl p-4 text-2xs font-mono focus:outline-none leading-relaxed resize-none"
                                />
                              </div>
                            </div>

                            <div className="bg-surface-base px-6 py-4 border-t-2 border-border-subtle flex justify-end space-x-2">
                              <button
                                type="button"
                                onClick={handleDiscardRefinement}
                                disabled={refineLoading}
                                className="bg-surface-raised border-2 border-border-subtle shadow-button text-text-secondary font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                              >
                                {refineLoading ? <RefreshCw className="h-4 w-4 animate-spin text-text-secondary" /> : null}
                                <span>Discard</span>
                              </button>
                              <button
                                type="button"
                                onClick={handleAcceptRefinement}
                                disabled={refineLoading}
                                className="bg-status-info hover:bg-status-info-bg border-2 border-status-info-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                              >
                                {refineLoading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                                <span>Accept Refinement</span>
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="bg-surface-raised border-2 border-accent shadow-card rounded-2xl overflow-hidden flex flex-col min-h-[500px]">
                            <div className="px-6 py-4 bg-status-success-bg border-b-2 border-accent flex justify-between items-center">
                              <div>
                                <span className="text-2xs font-bold text-accent-text uppercase tracking-wider">Markdown Editor</span>
                                <h3 className="font-extrabold text-sm text-accent-text mt-0.5">{editingArtifact.toUpperCase()}</h3>
                              </div>
                              <span className="text-2xs font-mono font-bold text-accent-text">Save changes to disk</span>
                            </div>

                            <div className="flex-grow flex flex-col lg:flex-row p-6 gap-6 min-h-0">
                              <div className="flex-grow flex flex-col space-y-2 lg:w-1/2">
                                <textarea
                                  value={editorContent}
                                  onChange={(e) => setEditorContent(e.target.value)}
                                  spellCheck="false"
                                  className="w-full flex-grow h-96 bg-surface-sunken text-text-secondary border-2 border-border-subtle rounded-2xl p-4 text-xs font-mono focus:outline-none focus:border-accent transition-colors leading-relaxed"
                                />
                                <code className="text-2xs text-text-muted font-bold leading-normal">Unsaved edits stay in this browser until saved.</code>
                              </div>

                              {(openQuestions.length > 0 || fetchingOpenQuestions) && (
                                <div className="lg:w-96 flex flex-col border-2 border-status-info/30 rounded-2xl bg-status-info-bg/20 p-4 shrink-0 max-h-[480px]">
                                  <div className="flex justify-between items-center mb-3">
                                    <div className="flex items-center space-x-2">
                                      <HelpCircle className="h-4 w-4 text-status-info-text" />
                                      <span className="font-extrabold text-xs text-status-info-text uppercase tracking-wider">Open Questions</span>
                                    </div>
                                    {fetchingOpenQuestions ? (
                                      <RefreshCw className="h-3 w-3 animate-spin text-status-info-text" />
                                    ) : (
                                      <span className="bg-status-info-bg text-status-info-text font-extrabold text-2xs uppercase tracking-wider px-2 py-0.5 rounded-full">
                                        {openQuestions.length} remaining
                                      </span>
                                    )}
                                  </div>

                                  {fetchingOpenQuestions && openQuestions.length === 0 ? (
                                    <div className="flex-grow flex items-center justify-center py-12">
                                      <RefreshCw className="h-6 w-6 animate-spin text-status-info-text" />
                                    </div>
                                  ) : (
                                    <>
                                      <div className="flex-grow overflow-y-auto space-y-3 pr-1 mb-3">
                                        {openQuestions.map((q, idx) => {
                                          const answerVal = openQuestionsAnswers[q] || '';
                                          const isThisApplying = answeringOpenQuestions === q;
                                          const isAnyApplying = answeringOpenQuestions !== null && answeringOpenQuestions !== false;
                                          const isApplyDisabled = isAnyApplying || !answerVal.trim();
                                          const suggestions = suggestedAnswersForQuestion(q);

                                          return (
                                            <div key={idx} className="bg-surface-raised border border-border-subtle rounded-xl p-3 space-y-2">
                                              <p className="text-xs font-bold text-text-secondary leading-relaxed">
                                                {q}
                                              </p>
                                              {suggestions.length > 0 && (
                                                <div className="flex flex-wrap gap-1.5">
                                                  {suggestions.map((choice) => (
                                                    <button
                                                      key={`${q}-${choice.label}-${choice.value}`}
                                                      type="button"
                                                      onClick={() => setOpenQuestionsAnswers(prev => ({ ...prev, [q]: choice.value }))}
                                                      disabled={isAnyApplying}
                                                      className="bg-status-info-bg hover:bg-status-info-bg disabled:opacity-50 border border-status-info/30 text-status-info-text font-bold text-2xs px-2 py-1 rounded-lg transition-colors"
                                                    >
                                                      {choice.label}
                                                    </button>
                                                  ))}
                                                </div>
                                              )}
                                              <textarea
                                                value={answerVal}
                                                onChange={(e) => setOpenQuestionsAnswers(prev => ({ ...prev, [q]: e.target.value }))}
                                                placeholder="Type your answer here..."
                                                className="w-full bg-surface-sunken text-text-secondary border border-border-subtle rounded-lg p-2 text-xs focus:outline-none focus:border-status-info transition-colors resize-y min-h-[60px] leading-relaxed font-sans"
                                                disabled={isAnyApplying}
                                              />
                                              <div className="flex justify-end">
                                                <button
                                                  type="button"
                                                  onClick={() => handleApplyAnswers(q)}
                                                  disabled={isApplyDisabled}
                                                  className="bg-status-info hover:bg-status-info-bg disabled:opacity-50 disabled:pointer-events-none border-2 border-status-info-text shadow-button text-text-inverse font-bold py-1 px-3 rounded-lg text-2xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1 cursor-pointer"
                                                >
                                                  {isThisApplying ? (
                                                    <RefreshCw className="h-3 w-3 animate-spin" />
                                                  ) : (
                                                    <Sparkles className="h-3 w-3" />
                                                  )}
                                                  <span>{isThisApplying ? 'Applying...' : 'Apply'}</span>
                                                </button>
                                              </div>
                                            </div>
                                          );
                                        })}
                                      </div>
                                    </>
                                  )}
                                </div>
                              )}
                            </div>

                            <div className="bg-surface-base px-6 py-4 border-t-2 border-border-subtle flex justify-end space-x-2">
                              <button
                                type="button"
                                onClick={() => setEditingArtifact(null)}
                                className="bg-surface-raised border-2 border-border-subtle shadow-button text-text-secondary font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={handleRefineArtifact}
                                disabled={actionLoading || refineLoading}
                                className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                              >
                                {refineLoading ? (
                                  <RefreshCw className="h-4 w-4 animate-spin text-text-secondary" />
                                ) : (
                                  <Sparkles className="h-4 w-4 text-status-info-text" />
                                )}
                                <span>{refineLoading ? 'Refining…' : 'Refine with AI'}</span>
                              </button>
                              <button
                                type="button"
                                onClick={handleSaveArtifact}
                                disabled={actionLoading || refineLoading}
                                className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                              >
                                <Save className="h-4 w-4" />
                                <span>Save Markdown</span>
                              </button>
                            </div>
                          </div>
                        )
                      ) : approvalReviews.length > 0 ? (
                        (() => {
                          const selectedReview = approvalReviews.find(r => r.is_actionable) || approvalReviews[0];
                          const badge = getBadgeStyle(selectedReview.decision);
                          const committee = selectedReview.committee && typeof selectedReview.committee === 'object' ? selectedReview.committee : null;
                          const committeeAdvisors = Array.isArray(committee?.advisors) ? committee.advisors : [];
                          const committeeMessages = Array.isArray(committee?.messages) ? committee.messages : [];
                          const debateMessages = visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? visibleGateDebate.messages : [];
                          const challengerMessages = debateMessages.filter((item) => ['ask', 'reject'].includes(item.kind));
                          const ownerRepliesByParent = debateMessages.reduce((acc, item) => {
                            if (['assert', 'propose'].includes(item.kind) && item.in_reply_to) {
                              acc[item.in_reply_to] = [...(acc[item.in_reply_to] || []), item];
                            }
                            return acc;
                          }, {});
                          const orphanOwnerReplies = debateMessages.filter((item) => (
                            ['assert', 'propose'].includes(item.kind) && !item.in_reply_to
                          ));
                          const debateAccent = (kind) => {
                            if (kind === 'ask') return 'border-l-status-warning';
                            if (kind === 'reject') return 'border-l-status-danger';
                            if (kind === 'challenge') return 'border-l-status-warning';
                            if (kind === 'rebuttal') return 'border-l-status-info';
                            if (kind === 'assert') return 'border-l-status-info';
                            if (kind === 'propose') return 'border-l-accent';
                            return 'border-l-border-strong';
                          };
                          const renderDebateMessage = (item, extraClass = '') => (
                            <div key={item.id || `${item.from_role || 'unknown'}:${item.kind || 'message'}:${item.body || ''}`} className={`bg-surface-raised border border-border-subtle border-l-4 ${debateAccent(item.kind)} rounded-xl p-3 ${extraClass}`}>
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-2xs font-extrabold uppercase tracking-wider text-text-secondary">{item.from_role || 'unknown'}</span>
                                {item.to && (
                                  <span className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">to {item.to}</span>
                                )}
                                <span className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">{item.kind}</span>
                              </div>
                              <p className="text-2xs leading-relaxed text-text-secondary whitespace-pre-wrap">{item.body}</p>
                            </div>
                          );
                          const committeeRepliesByParent = committeeMessages.reduce((acc, item, index) => {
                            if (item?.in_reply_to) {
                              acc[item.in_reply_to] = [...(acc[item.in_reply_to] || []), { item, index }];
                            }
                            return acc;
                          }, {});
                          const committeeRootMessages = committeeMessages
                            .map((item, index) => ({ item, index }))
                            .filter(({ item }) => !item?.in_reply_to);
                          const hasCommitteeMessages = committeeMessages.length > 0;
                          const needsCommitteeDebate = !committee?.convened || !hasCommitteeMessages;
                          const debateRef = getResolvingDebateRef(selectedReview.approval);
                          const currentDebateRunKey = `${gateDebateProjectKey}:${gateDebateRunId}:${selectedReview.approval}`;
                          const isRunningDebate = debateRun.key === currentDebateRunKey && debateRun.loading;
                          const runDebateError = debateRun.key === currentDebateRunKey ? debateRun.error : '';
                          const isRejectLoading = approvingAction === `${selectedReview.approval}:rejected`;
                          const isRequestChangesLoading = approvingAction === `${selectedReview.approval}:changes_requested`;
                          const isApproveLoading = approvingAction === `${selectedReview.approval}:approved`;
                          const isSelectedApprovalLoading = isRejectLoading || isRequestChangesLoading || isApproveLoading;
                          return (
                            <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden flex flex-col min-h-[500px]">
                              <div className="px-6 py-4 bg-surface-sunken border-b-2 border-border-subtle flex justify-between items-center">
                                <div>
                                  <span className="text-2xs font-bold text-text-muted uppercase tracking-wider">Preview Panel</span>
                                  <h3 className="font-extrabold text-sm text-text-secondary mt-0.5">{selectedReview.artifact.toUpperCase()} Review Details</h3>
                                </div>
                                <span className={`inline-block px-2.5 py-0.5 rounded-full text-2xs font-extrabold border uppercase tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>{selectedReview.decision.replace('_', ' ')}</span>
                              </div>

                              <div className="p-6 flex-grow space-y-6">
                                {/* Role reviewers info list */}
                                {selectedReview.review_roles && selectedReview.review_roles.length > 0 && (
                                  <div className="border-2 border-border-subtle rounded-2xl p-4 bg-surface-base font-bold">
                                    <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-2.5">Assigned Review Roles</h4>
                                    <div className="flex flex-wrap gap-2">
                                      {selectedReview.review_roles.map((role) => (
                                        <span key={role} className="bg-surface-raised text-text-secondary text-2xs px-2.5 py-1 rounded-lg border border-border-subtle">{role}</span>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {selectedReview.is_actionable && committee && (
                                  <div className="border-2 border-dashed border-border-subtle rounded-2xl p-4 bg-status-warning-bg space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                      <div>
                                        <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">Committee</h4>
                                        {needsCommitteeDebate && (
                                          <p className="text-2xs font-bold text-text-muted mt-1">Committee declared. Run debate to convene it.</p>
                                        )}
                                      </div>
                                      <div className="flex items-center gap-2">
                                        {committee.convened && (
                                          <span className="text-2xs font-extrabold uppercase tracking-wider text-accent-text">Convened</span>
                                        )}
                                        {needsCommitteeDebate && (
                                          <button
                                            type="button"
                                            onClick={() => handleRunDebate(selectedReview.approval)}
                                            disabled={isRunningDebate}
                                            className="bg-surface-raised hover:bg-surface-base disabled:opacity-60 disabled:cursor-not-allowed border-2 border-border-subtle shadow-button text-text-secondary font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1.5"
                                          >
                                            <RefreshCw className={`h-3 w-3 ${isRunningDebate ? 'animate-spin' : ''}`} />
                                            <span>{isRunningDebate ? 'Running debate...' : 'Run debate'}</span>
                                          </button>
                                        )}
                                      </div>
                                    </div>
                                    {runDebateError && (
                                      <div className="text-2xs font-bold text-status-danger">Debate run failed: {runDebateError}</div>
                                    )}
                                    {committeeAdvisors.length > 0 && (
                                      <div className="flex flex-wrap gap-2">
                                        {committeeAdvisors.map((role) => (
                                          <span key={role} className="bg-surface-raised text-text-secondary text-2xs px-2.5 py-1 rounded-lg border border-border-subtle">{role}</span>
                                        ))}
                                      </div>
                                    )}
                                    {hasCommitteeMessages && (
                                      <div className="space-y-2">
                                        {committeeRootMessages.map(({ item, index }) => {
                                          const parentKey = item.id || String(index);
                                          return (
                                            <div key={parentKey} className="space-y-2">
                                              {renderDebateMessage(item)}
                                              {(committeeRepliesByParent[item.id] || []).map(({ item: reply }) => renderDebateMessage(reply, 'ml-8'))}
                                            </div>
                                          );
                                        })}
                                        {committeeMessages.length > committeeRootMessages.length && committeeRootMessages.length === 0 && (
                                          committeeMessages.map((message, index) => renderDebateMessage(message, index > 0 ? 'ml-8' : ''))
                                        )}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {selectedReview.is_actionable && !committee && (
                                  <div className="border-2 border-dashed border-border-subtle rounded-2xl p-4 bg-status-warning-bg space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                      <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">Gate Debate & Consensus</h4>
                                      <div className="flex items-center gap-2">
                                        {challengerMessages.length > 0 && (
                                          <span className="text-2xs font-extrabold uppercase tracking-wider text-status-warning-text">{challengerMessages.length} Objections</span>
                                        )}
                                        <button
                                          type="button"
                                          onClick={() => handleRunDebate(selectedReview.approval)}
                                          disabled={isRunningDebate}
                                          className="bg-surface-raised hover:bg-surface-base disabled:opacity-60 disabled:cursor-not-allowed border-2 border-border-subtle shadow-button text-text-secondary font-bold text-2xs uppercase tracking-wider px-3 py-1.5 rounded-2xl transition-all duo-button-physics flex items-center space-x-1.5"
                                        >
                                          <RefreshCw className={`h-3 w-3 ${isRunningDebate ? 'animate-spin' : ''}`} />
                                          <span>{isRunningDebate ? 'Running debate...' : 'Run debate'}</span>
                                        </button>
                                      </div>
                                    </div>
                                    {runDebateError && (
                                      <div className="text-2xs font-bold text-status-danger">Debate run failed: {runDebateError}</div>
                                    )}
                                    {visibleGateDebate.loading && visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? (
                                      <div className="text-2xs font-bold text-text-muted">Loading debate...</div>
                                    ) : visibleGateDebate.error && visibleGateDebate.key.endsWith(`:${selectedReview.approval}`) ? (
                                      <div className="text-2xs font-bold text-status-danger">Debate unavailable.</div>
                                    ) : debateMessages.length === 0 ? (
                                      <div className="text-2xs font-bold text-text-muted">No debate recorded for this gate.</div>
                                    ) : (
                                      <div className="space-y-2">
                                        {challengerMessages.map((message) => (
                                          <div key={message.id} className="space-y-2">
                                            {renderDebateMessage(message)}
                                            {(ownerRepliesByParent[message.id] || []).map((reply) => renderDebateMessage(reply, 'ml-8'))}
                                          </div>
                                        ))}
                                        {orphanOwnerReplies.map((message) => renderDebateMessage(message, 'ml-8'))}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Markdown artifact preview */}
                                {selectedReview.note && (
                                  <div className="bg-status-warning-bg border-2 border-status-warning/30 p-4 rounded-2xl space-y-1">
                                    <div className="text-2xs font-extrabold uppercase tracking-wider text-status-warning-text font-sans">Reviewer Feedback Note</div>
                                    <div className="text-xs text-text-secondary whitespace-pre-wrap font-mono leading-relaxed">{selectedReview.note}</div>
                                  </div>
                                )}
                                <div className="space-y-2">
                                  <h4 className="text-2xs font-bold uppercase tracking-wider text-text-muted">Content Preview</h4>
                                  {selectedReview.content ? (
                                    <div
                                      className="prose prose-zinc max-w-none text-xs leading-relaxed font-mono bg-surface-base p-4 border border-border-subtle rounded-2xl max-h-96 overflow-y-auto whitespace-pre-wrap select-all select-text"
                                    >
                                      {selectedReview.content}
                                    </div>
                                  ) : (
                                    <div className="text-center py-12 bg-surface-sunken border border-border-subtle rounded-xl text-text-secondary italic text-xs">
                                      No artifact preview content available.
                                    </div>
                                  )}
                                </div>
                              </div>

                              {selectedReview.is_actionable && (
                                <div className="bg-surface-base px-6 py-4 border-t-2 border-border-subtle flex flex-col items-stretch space-y-4">
                                  {showChangesRequestedNoteKey === selectedReview.approval && (
                                    <div className="flex flex-col space-y-2">
                                      <label className="text-xs font-bold text-text-secondary">
                                        What needs to change? The PRD will be revised to address this.
                                      </label>
                                      <textarea
                                        value={changesRequestedNote}
                                        onChange={(e) => setChangesRequestedNote(e.target.value)}
                                        placeholder="Type feedback here..."
                                        rows={3}
                                        className="w-full p-3 border border-border-subtle bg-surface-raised text-text-secondary rounded-xl text-xs focus:ring-2 focus:ring-status-warning outline-none resize-none font-sans"
                                      />
                                      {changesRequestedNote.trim() === '' && (
                                        <div className="text-status-warning-text text-2xs font-bold">
                                          ⚠️ Feedback note is empty. Please provide detail so the model knows what to revise.
                                        </div>
                                      )}
                                    </div>
                                  )}
                                  <div className="flex flex-col items-end space-y-2">
                                    <div className="flex justify-end space-x-2">
                                      {showChangesRequestedNoteKey === selectedReview.approval && (
                                        <button
                                          type="button"
                                          onClick={() => {
                                            setShowChangesRequestedNoteKey(null);
                                            setChangesRequestedNote('');
                                          }}
                                          disabled={actionLoading}
                                          className="bg-surface-raised border-2 border-border-subtle shadow-button text-text-secondary font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics"
                                        >
                                          Cancel
                                        </button>
                                      )}
                                      {showChangesRequestedNoteKey !== selectedReview.approval && (
                                        <button
                                          type="button"
                                          onClick={() => handleApproveDecision(selectedReview.approval, 'rejected', debateRef)}
                                          disabled={actionLoading}
                                          className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle shadow-button text-status-danger font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                        >
                                          {isRejectLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                                          <span>{isRejectLoading ? 'Rejecting...' : 'Reject'}</span>
                                        </button>
                                      )}
                                      <button
                                        type="button"
                                        onClick={() => {
                                          if (showChangesRequestedNoteKey === selectedReview.approval) {
                                            handleApproveDecision(selectedReview.approval, 'changes_requested', debateRef, changesRequestedNote);
                                          } else {
                                            setShowChangesRequestedNoteKey(selectedReview.approval);
                                          }
                                        }}
                                        disabled={actionLoading}
                                        className="bg-surface-raised hover:bg-status-warning-bg border-2 border-border-subtle shadow-button text-status-warning font-bold px-4 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                      >
                                        {isRequestChangesLoading && <RefreshCw className="h-4 w-4 animate-spin" />}
                                        <span>
                                          {showChangesRequestedNoteKey === selectedReview.approval
                                            ? (isRequestChangesLoading ? 'Submitting...' : 'Submit Request')
                                            : (isRequestChangesLoading ? 'Requesting...' : 'Request Changes')
                                          }
                                        </span>
                                      </button>
                                      {showChangesRequestedNoteKey !== selectedReview.approval && (
                                        <button
                                          type="button"
                                          onClick={() => handleApproveDecision(selectedReview.approval, 'approved', debateRef)}
                                          disabled={actionLoading}
                                          className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-2xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center space-x-1.5"
                                        >
                                          {isApproveLoading ? (
                                            <RefreshCw className="h-4 w-4 animate-spin" />
                                          ) : (
                                            <Check className="h-4 w-4" />
                                          )}
                                          <span>{isApproveLoading ? 'Submitting...' : 'Approve Gate'}</span>
                                        </button>
                                      )}
                                    </div>
                                    {isSelectedApprovalLoading && (
                                      <span className="text-2xs text-text-secondary animate-pulse text-right">
                                        Generating… (PRD/RFC may be LLM-generated; this can take up to ~a minute)
                                      </span>
                                    )}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <div className="bg-surface-raised border border-border-subtle p-12 text-center text-text-secondary italic text-xs min-h-[500px] flex items-center justify-center rounded-2xl">
                          Select an approval review card to view content.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
  );
}
