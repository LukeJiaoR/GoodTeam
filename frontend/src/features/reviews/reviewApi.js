import { requestJson } from '../../lib/api/client';
import { getProjectState } from '../runs/runApi';

export const getReviewState = getProjectState;
export const getArtifact = (projectKey, key, { signal } = {}) => requestJson(
  `/api/artifact?project=${encodeURIComponent(projectKey)}&key=${encodeURIComponent(key)}`,
  { signal },
);
export const getOpenQuestions = (projectKey, artifact, { signal } = {}) => requestJson(
  `/api/open-questions?project=${encodeURIComponent(projectKey)}&artifact=${encodeURIComponent(artifact)}`,
  { signal },
);

export const reviewApi = { getReviewState, getArtifact, getOpenQuestions };
