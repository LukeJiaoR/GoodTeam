import { useState } from 'react';

export function useNewProjectForm(setNewProjectModalOpen) {
  const [idea, setIdea] = useState('');
  const [projectName, setProjectName] = useState('');
  const [startProjectKey, setStartProjectKey] = useState('');
  const [modelProfile, setModelProfile] = useState('balanced');
  const [deliveryMode, setDeliveryMode] = useState('init_project');
  const [deliveryStandards, setDeliveryStandards] = useState('');
  const [roleModelOverrides, setRoleModelOverrides] = useState('');
  const [projectRoot, setProjectRoot] = useState('');
  const [projectConfig, setProjectConfig] = useState('');
  const [taskSync, setTaskSync] = useState('');
  const [workerBackendMode, setWorkerBackendMode] = useState('codex');
  const [initWorkerBucketSelections, setInitWorkerBucketSelections] = useState({});
  const [deploymentBackendMode, setDeploymentBackendMode] = useState('');
  const [startMode, setStartMode] = useState('existing_project');
  const [dryRun, setDryRun] = useState(false);
  const isNewProjectMode = startMode === 'new_project' || startMode === 'new';

  const [initGit, setInitGit] = useState(true);
  const [createRemote, setCreateRemote] = useState(false);
  const [remotePrivate, setRemotePrivate] = useState(true);
  const [gitWarnings, setGitWarnings] = useState([]);
  const [gitRemoteCreated, setGitRemoteCreated] = useState(false);
  const [showGitNotice, setShowGitNotice] = useState(false);

  const [planeApiHost, setPlaneApiHost] = useState('https://api.plane.so/');
  const [planeWorkspaceSlug, setPlaneWorkspaceSlug] = useState('');
  const [planeProjectId, setPlaneProjectId] = useState('');
  const [planeApiKeyEnv, setPlaneApiKeyEnv] = useState('PLANE_API_KEY');
  const [planeMcpServer, setPlaneMcpServer] = useState('plane');

  const [stagingSshTarget, setStagingSshTarget] = useState('');
  const [stagingRemoteDir, setStagingRemoteDir] = useState('');
  const [stagingGitRef, setStagingGitRef] = useState('');
  const [stagingPublicUrl, setStagingPublicUrl] = useState('');
  const [stagingDeployCommand, setStagingDeployCommand] = useState('');
  const [stagingHealthcheckCommand, setStagingHealthcheckCommand] = useState('');
  const [stagingSmokeCommand, setStagingSmokeCommand] = useState('');
  const [deliveryContractRequired, setDeliveryContractRequired] = useState(false);
  const [deliveryContractMinEvidence, setDeliveryContractMinEvidence] = useState('infrastructure');
  const [deliveryContractFatalPatterns, setDeliveryContractFatalPatterns] = useState('');
  const [deliveryContractAcceptance, setDeliveryContractAcceptance] = useState('');
  const [contextSources, setContextSources] = useState([{ kind: 'dir', value: '' }]);
  const [contextEstimate, setContextEstimate] = useState(null);
  const [previewingContext, setPreviewingContext] = useState(false);
  const [generatingContext, setGeneratingContext] = useState(false);

  const openNewProjectModal = () => {
    setInitGit(true);
    setCreateRemote(false);
    setRemotePrivate(true);
    setGitWarnings([]);
    setGitRemoteCreated(false);
    setShowGitNotice(false);
    setContextEstimate(null);
    setInitWorkerBucketSelections({});
    setNewProjectModalOpen(true);
  };

  const closeNewProjectModal = () => {
    setNewProjectModalOpen(false);
  };

  return {
    openNewProjectModal,
    closeNewProjectModal,
    idea,
    setIdea,
    projectName,
    setProjectName,
    startProjectKey,
    setStartProjectKey,
    modelProfile,
    setModelProfile,
    deliveryMode,
    setDeliveryMode,
    deliveryStandards,
    setDeliveryStandards,
    roleModelOverrides,
    setRoleModelOverrides,
    projectRoot,
    setProjectRoot,
    projectConfig,
    setProjectConfig,
    taskSync,
    setTaskSync,
    workerBackendMode,
    setWorkerBackendMode,
    initWorkerBucketSelections,
    setInitWorkerBucketSelections,
    deploymentBackendMode,
    setDeploymentBackendMode,
    startMode,
    setStartMode,
    dryRun,
    setDryRun,
    isNewProjectMode,
    initGit,
    setInitGit,
    createRemote,
    setCreateRemote,
    remotePrivate,
    setRemotePrivate,
    gitWarnings,
    setGitWarnings,
    gitRemoteCreated,
    setGitRemoteCreated,
    showGitNotice,
    setShowGitNotice,
    planeApiHost,
    setPlaneApiHost,
    planeWorkspaceSlug,
    setPlaneWorkspaceSlug,
    planeProjectId,
    setPlaneProjectId,
    planeApiKeyEnv,
    setPlaneApiKeyEnv,
    planeMcpServer,
    setPlaneMcpServer,
    stagingSshTarget,
    setStagingSshTarget,
    stagingRemoteDir,
    setStagingRemoteDir,
    stagingGitRef,
    setStagingGitRef,
    stagingPublicUrl,
    setStagingPublicUrl,
    stagingDeployCommand,
    setStagingDeployCommand,
    stagingHealthcheckCommand,
    setStagingHealthcheckCommand,
    stagingSmokeCommand,
    setStagingSmokeCommand,
    deliveryContractRequired,
    setDeliveryContractRequired,
    deliveryContractMinEvidence,
    setDeliveryContractMinEvidence,
    deliveryContractFatalPatterns,
    setDeliveryContractFatalPatterns,
    deliveryContractAcceptance,
    setDeliveryContractAcceptance,
    contextSources,
    setContextSources,
    contextEstimate,
    setContextEstimate,
    previewingContext,
    setPreviewingContext,
    generatingContext,
    setGeneratingContext,
  };
}
