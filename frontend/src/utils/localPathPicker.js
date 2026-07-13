function errorMessage(error, fallback) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return fallback;
}

export async function chooseLocalPath({
  mode,
  initialPath,
  onSelected,
  openWebPicker,
  onError,
  fetchImpl = globalThis.fetch,
}) {
  const fail = (error) => {
    const message = errorMessage(error, 'Failed to open the native path chooser');
    onError?.(message);
    openWebPicker?.();
    return { status: 'error', error: message };
  };

  try {
    const response = await fetchImpl('/api/fs/choose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, initial_path: initialPath }),
    });
    const payload = await response.json();

    if (!response.ok) {
      return fail(payload?.error || `Native path chooser failed with HTTP ${response.status}`);
    }
    if (payload?.status === 'selected') {
      onSelected?.(payload.path);
      return payload;
    }
    if (payload?.status === 'cancelled') {
      return payload;
    }
    if (payload?.status === 'unsupported') {
      openWebPicker?.();
      return payload;
    }
    if (payload?.status === 'error') {
      return fail(payload.error);
    }
    return fail('Native path chooser returned an invalid response');
  } catch (error) {
    return fail(error);
  }
}
