export function formatDeliveryContractFatalPatterns(fatalOutputPatterns) {
  return Object.values(fatalOutputPatterns).join('\n');
}

export function formatDeliveryContractAcceptance(stagingAcceptance) {
  return Object.entries(stagingAcceptance)
    .map(([id, item]) => `${id}|${item.type}|${item.command}`)
    .join('\n');
}
