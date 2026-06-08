import { useState } from 'react';

export function useClipboardFeedback() {
  const [copiedText, setCopiedText] = useState(null);
  const [copyFailedText, setCopyFailedText] = useState(null);

  const markCopied = (command) => {
    setCopyFailedText((prev) => (prev === command ? null : prev));
    setCopiedText(command);
    setTimeout(() => {
      setCopiedText((prev) => (prev === command ? null : prev));
    }, 2000);
  };

  const markCopyFailed = (command) => {
    setCopiedText((prev) => (prev === command ? null : prev));
    setCopyFailedText(command);
    setTimeout(() => {
      setCopyFailedText((prev) => (prev === command ? null : prev));
    }, 2000);
  };

  const copyCommand = (command) => {
    if (!command) return;

    const clipboard = navigator.clipboard;
    if (clipboard?.writeText) {
      clipboard.writeText(command)
        .then(() => markCopied(command))
        .catch(() => markCopyFailed(command));
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = command;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();

    try {
      const copied = typeof document.execCommand === 'function' && document.execCommand('copy');
      if (copied) {
        markCopied(command);
      } else {
        markCopyFailed(command);
      }
    } catch {
      markCopyFailed(command);
    } finally {
      document.body.removeChild(textarea);
    }
  };

  return { copiedText, copyFailedText, copyCommand };
}
