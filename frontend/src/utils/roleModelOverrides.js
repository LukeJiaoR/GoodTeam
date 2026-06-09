export function parseRoleModelOverrideLines(roleModelOverrides) {
  return roleModelOverrides
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export function parseRoleModelOverridesMap(roleModelOverrides) {
  return Object.fromEntries(
    parseRoleModelOverrideLines(roleModelOverrides)
      .map((line) => line.split('=', 2))
      .filter((parts) => parts.length === 2 && parts[0] && parts[1])
  );
}
