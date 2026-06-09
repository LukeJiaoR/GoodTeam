export function getMissionSummary(text) {
  if (!text) return '';
  const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
  if (sentences.length <= 2) return text;
  return sentences.slice(0, 2).join(' ');
}
