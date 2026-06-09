import { useCallback, useState } from 'react';

function defaultRemoteState(projectKey) {
  return {
    projectKey,
    createRemote: false,
    remotePrivate: true,
  };
}

export function useGitInitAction({ projectKey, fetchDevLoop, showToast }) {
  const [remoteState, setRemoteState] = useState(() => defaultRemoteState(projectKey));
  const [gitInitLoadingProjectKey, setGitInitLoadingProjectKey] = useState(null);
  const [gitInitWarningState, setGitInitWarningState] = useState({ projectKey, warnings: [] });

  const activeRemoteState = remoteState.projectKey === projectKey ? remoteState : defaultRemoteState(projectKey);
  const devLoopCreateRemote = activeRemoteState.createRemote;
  const devLoopRemotePrivate = activeRemoteState.remotePrivate;
  const gitInitLoading = gitInitLoadingProjectKey === projectKey;
  const gitInitWarnings = gitInitWarningState.projectKey === projectKey ? gitInitWarningState.warnings : [];

  const setDevLoopCreateRemote = useCallback((value) => {
    setRemoteState((previous) => {
      const current = previous.projectKey === projectKey ? previous : defaultRemoteState(projectKey);
      return {
        ...current,
        createRemote: typeof value === 'function' ? value(current.createRemote) : value,
      };
    });
  }, [projectKey]);

  const setDevLoopRemotePrivate = useCallback((value) => {
    setRemoteState((previous) => {
      const current = previous.projectKey === projectKey ? previous : defaultRemoteState(projectKey);
      return {
        ...current,
        remotePrivate: typeof value === 'function' ? value(current.remotePrivate) : value,
      };
    });
  }, [projectKey]);

  const handleGitInit = useCallback(async () => {
    if (!projectKey) return;
    setGitInitLoadingProjectKey(projectKey);
    setGitInitWarningState({ projectKey, warnings: [] });
    try {
      const response = await fetch('/api/git-init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_key: projectKey,
          create_remote: devLoopCreateRemote,
          remote_private: devLoopRemotePrivate,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || data.message || 'Failed to initialize Git repository');
      }

      showToast('Git repository initialized successfully!');
      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      setGitInitWarningState({ projectKey, warnings });
      if (warnings.length > 0) {
        showToast(`Git warnings: ${warnings.join(', ')}`, true);
      }

      fetchDevLoop(projectKey);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      setGitInitLoadingProjectKey(null);
    }
  }, [devLoopCreateRemote, devLoopRemotePrivate, fetchDevLoop, projectKey, showToast]);

  return {
    devLoopCreateRemote,
    setDevLoopCreateRemote,
    devLoopRemotePrivate,
    setDevLoopRemotePrivate,
    gitInitLoading,
    gitInitWarnings,
    handleGitInit,
  };
}
