export default function ApprovalBanner({
  actionableCount,
  approvalBannerQuestions,
  setActiveTab,
  t,
}) {
  if (actionableCount <= 0) {
    return null;
  }

  return (
    <div className="bg-status-warning-bg border-b-2 border-status-warning/30 px-6 py-3 flex items-center justify-between text-xs font-bold text-status-warning-text animate-fade-in shrink-0">
      <div className="flex items-center space-x-2">
        <span className="h-2 w-2 rounded-full bg-status-warning animate-pulse shrink-0" />
        <span>{t('approvalBanner.message', {
          count: actionableCount,
          plural: actionableCount > 1 ? 's' : '',
          questions: approvalBannerQuestions,
        })}</span>
      </div>
      <button
        onClick={() => setActiveTab('approvals')}
        className="bg-surface-raised hover:bg-status-warning-bg border-2 border-status-warning/30 shadow-button text-status-warning-text font-bold px-3 py-1 rounded-lg uppercase tracking-wider transition-all duo-button-physics text-2xs cursor-pointer"
      >
        {t('approvalBanner.review')}
      </button>
    </div>
  );
}
