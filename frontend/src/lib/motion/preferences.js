export function normalizeMotionPreference(value) {
  return value === 'reduce' ? 'reduce' : 'system';
}

export function shouldReduceMotion(preference, systemMatches) {
  return normalizeMotionPreference(preference) === 'reduce' || Boolean(systemMatches);
}
