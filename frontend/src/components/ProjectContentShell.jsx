import ApprovalBanner from './ApprovalBanner';
import GitCreationNotice from './GitCreationNotice';
import GlobalToast from './GlobalToast';
import MemoryEventDetailModal from './MemoryEventDetailModal';
import ProjectMainPanel from './ProjectMainPanel';

export default function ProjectContentShell({
  actionableCount,
  approvalBannerQuestions,
  directoryPickerModal,
  error,
  gitRemoteCreated,
  gitWarnings,
  hasMemorySupersedeInfo,
  loading,
  loadingLabel,
  mainPanelChildren,
  memoryEventDetail,
  memorySupersededBy,
  memorySupersedes,
  message,
  newProjectModal,
  renderMemoryEventLink,
  setActiveTab,
  setMemoryEventDetail,
  setShowGitNotice,
  showGitNotice,
  t,
}) {
  return (
    <div className="flex-grow flex flex-col min-w-0 min-h-screen">
      <ApprovalBanner
        actionableCount={actionableCount}
        approvalBannerQuestions={approvalBannerQuestions}
        setActiveTab={setActiveTab}
        t={t}
      />

      <GitCreationNotice
        gitRemoteCreated={gitRemoteCreated}
        gitWarnings={gitWarnings}
        setShowGitNotice={setShowGitNotice}
        showGitNotice={showGitNotice}
      />

      <GlobalToast message={message} />

      <MemoryEventDetailModal
        hasMemorySupersedeInfo={hasMemorySupersedeInfo}
        memoryEventDetail={memoryEventDetail}
        memorySupersededBy={memorySupersededBy}
        memorySupersedes={memorySupersedes}
        renderMemoryEventLink={renderMemoryEventLink}
        setMemoryEventDetail={setMemoryEventDetail}
      />

      {newProjectModal}
      {directoryPickerModal}

      <ProjectMainPanel loading={loading} error={error} loadingLabel={loadingLabel}>
        {mainPanelChildren}
      </ProjectMainPanel>
    </div>
  );
}
