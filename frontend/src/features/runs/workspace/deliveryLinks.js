export function isBoundReportUrl(artifact, summary) {
  if (!artifact.href || !summary.project_key || !summary.run_id || !artifact.version) return false;
  try {
    const url = new URL(artifact.href, 'https://goodteam.invalid');
    const expected = { project: summary.project_key, run: summary.run_id, key: artifact.key, version: artifact.version };
    return artifact.href.startsWith('/api/delivery-artifact?')
      && url.origin === 'https://goodteam.invalid' && !url.hash
      && [...url.searchParams.keys()].length === 4
      && Object.entries(expected).every(([key, value]) => (
        url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === value
      ));
  } catch { return false; }
}

export function deliveryLinkResolver(summary) {
  return (url) => {
    const path = url.replace(/(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)$/, '');
    const item = (summary?.artifacts || []).find(artifact => isBoundReportUrl(artifact, summary)
      && (artifact.href === url || `${artifact.href}&view=text` === url || artifact.aliases?.includes(path)));
    if (item) return `${item.href}&view=text`;
    // Unregistered disk/relative paths are not HTTP routes. The retained source
    // and patch entries remain independently available in the delivery panel.
    return /^https?:\/\//i.test(url) || url.startsWith('#') ? url : null;
  };
}
