export function suggestedAnswersForQuestion(question) {
  const text = String(question || '');
  const choices = [];
  const optionPattern = /(?:^|[\s([,;])(?:option\s*)?([A-C]|yes|no)\s*[:.)-]\s*([^?;\n]{1,60})/gi;
  let match;
  while ((match = optionPattern.exec(text)) !== null) {
    const label = match[2].trim();
    const value = match[3].trim().replace(/[),.;:]+$/, '');
    if (value && !choices.some(choice => choice.value === value)) {
      choices.push({ label: label.toUpperCase(), value });
    }
  }
  if (/\byes\b/i.test(text) && /\bno\b/i.test(text) && choices.length === 0) {
    choices.push({ label: 'Yes', value: 'Yes' }, { label: 'No', value: 'No' });
  }
  return choices.slice(0, 4);
}

export function computeLineDiff(baseText, newText) {
  const baseLines = (baseText || '').split('\n');
  const newLines = (newText || '').split('\n');

  const M = baseLines.length;
  const N = newLines.length;

  const dp = Array.from({ length: M + 1 }, () => new Int32Array(N + 1));

  for (let i = 1; i <= M; i++) {
    for (let j = 1; j <= N; j++) {
      if (baseLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  const diff = [];
  let i = M, j = N;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && baseLines[i - 1] === newLines[j - 1]) {
      diff.unshift({ type: 'unchanged', text: baseLines[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      diff.unshift({ type: 'added', text: newLines[j - 1] });
      j--;
    } else {
      diff.unshift({ type: 'removed', text: baseLines[i - 1] });
      i--;
    }
  }
  return diff;
}
