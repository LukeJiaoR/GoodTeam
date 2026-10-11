export function isMemoryEventId(value) {
  return typeof value === 'string' && /^mem:[a-zA-Z0-9_-]{1,128}$/.test(value);
}
