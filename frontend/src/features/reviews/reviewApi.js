import { requestJson } from '../../lib/api/client';
import { getProjectState } from '../runs/runApi';

const post = (path, body, { signal } = {}) => requestJson(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body), signal,
});

function expectedArtifactFields(options = {}) {
  const expected = options.expectedArtifact || {};
  const runId = expected.runId || options.expectedRunId || '';
  const artifactVersion = expected.artifactVersion || options.expectedArtifactVersion || '';
  return {
    ...(runId ? { expected_run_id: runId } : {}),
    ...(artifactVersion ? { expected_artifact_version: artifactVersion } : {}),
  };
}

export const getReviewState = getProjectState;
export const getArtifact = (projectKey, key, { signal } = {}) => requestJson(
  `/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(key)}`,
  { signal },
);
export const getOpenQuestions = (projectKey, artifact, { signal } = {}) => requestJson(
  `/api/open-questions?project=${encodeURIComponent(projectKey)}&artifact=${encodeURIComponent(artifact)}`,
  { signal },
);
export const saveArtifact = (projectKey, artifact, content, options = {}) => {
  const { signal } = options || {};
  return post('/api/artifact', {
    project_key: projectKey,
    artifact,
    content,
    ...expectedArtifactFields(options || {}),
  }, { signal });
};
export const refineArtifact = (projectKey, artifact, content, options = {}) => {
  const { signal } = options || {};
  return post('/api/refine-artifact', {
    project_key: projectKey,
    artifact,
    content,
    ...expectedArtifactFields(options || {}),
  }, { signal });
};
export const acceptRefinement = (projectKey, artifact, options) => post('/api/accept-refinement', { project_key: projectKey, artifact }, options);
export const discardRefinement = (projectKey, artifact, options) => post('/api/discard-refinement', { project_key: projectKey, artifact }, options);
export const decideReview = (projectKey, approval, decision, note = '', options = {}) => {
  const { expectedApproval = {}, signal } = options || {};
  const binding = expectedApproval && typeof expectedApproval === 'object'
    ? {
      expected_run_id: expectedApproval.run_id,
      expected_target_type: expectedApproval.target_type,
      expected_target_id: expectedApproval.target_id,
      expected_artifact_key: expectedApproval.artifact_key,
      expected_artifact_version: expectedApproval.artifact_version,
    }
    : {};
  return post('/api/approve', {
    project_key: projectKey,
    approval,
    decision,
    ...(note ? { note } : {}),
    ...Object.fromEntries(Object.entries(binding).filter(([, value]) => value)),
  }, { signal });
};
export const recoverTakeoverBrief = (projectKey, note = '', options) => post('/api/recover-takeover-brief', { project_key: projectKey, ...(note ? { note } : {}) }, options);
export const answerOpenQuestions = (projectKey, artifact, answers, options) => post('/api/answer-open-questions', { project_key: projectKey, artifact, answers }, options);

export const reviewApi = {
  acceptRefinement, answerOpenQuestions, decideReview, discardRefinement, getArtifact, getOpenQuestions,
  getReviewState, recoverTakeoverBrief, refineArtifact, saveArtifact,
};
