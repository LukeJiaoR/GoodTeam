export function buildApprovalMetrics({ approvalReviews, openQuestions, t }) {
  const approvedCount = approvalReviews.filter(r => r.decision === 'approved').length;
  const actionableCount = approvalReviews.filter(r => r.is_actionable).length;
  const openQuestionsGateCount = openQuestions.length;
  const totalApprovalsCount = approvalReviews.length || 6;
  const approvalProgressPercent = Math.min(100, Math.round((approvedCount / totalApprovalsCount) * 100));
  const approvalBannerQuestions = openQuestionsGateCount > 0
    ? t('approvalBanner.questions', {
      count: openQuestionsGateCount,
      plural: openQuestionsGateCount === 1 ? '' : 's',
    })
    : '';

  return {
    actionableCount,
    approvalBannerQuestions,
    approvalProgressPercent,
    approvedCount,
    totalApprovalsCount,
  };
}
