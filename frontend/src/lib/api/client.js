export class ApiError extends Error {
  constructor(payload = {}, status = 0) {
    const normalizedPayload = payload && typeof payload === 'object' ? payload : {};
    super(
      normalizedPayload.message
      || normalizedPayload.error
      || `Request failed (${status})`,
    );
    this.name = 'ApiError';
    this.status = status;
    this.code = normalizedPayload.code || 'unknown_error';
    this.details = normalizedPayload.details
      && typeof normalizedPayload.details === 'object'
      && !Array.isArray(normalizedPayload.details)
      ? normalizedPayload.details
      : {};
    this.recovery = Array.isArray(normalizedPayload.recovery)
      ? normalizedPayload.recovery
      : [];
    this.requestId = normalizedPayload.request_id || '';
  }
}

async function readJson(response) {
  try {
    const payload = typeof response.text === 'function'
      ? JSON.parse(await response.text())
      : await response.json();
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      throw new TypeError('JSON response must be an object.');
    }
    return payload;
  } catch {
    throw new ApiError({
      code: 'invalid_response',
      message: 'The server returned an invalid JSON response.',
    }, response.status);
  }
}

export async function requestJson(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new ApiError({
      code: 'offline',
      message: 'GoodTeam is unreachable from this browser.',
      recovery: ['check_service', 'retry_request'],
    });
  }
  const payload = await readJson(response);
  if (!response.ok) throw new ApiError(payload, response.status);
  return payload;
}
