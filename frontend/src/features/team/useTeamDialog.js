import { useEffect } from 'react';

const FOCUSABLE = 'button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, [href], [tabindex="0"]';

export default function useTeamDialog(dialogRef, closeRef, onClose) {
  useEffect(() => {
    const trigger = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const background = [];
    let ancestor = dialogRef.current?.parentElement;
    while (ancestor && ancestor !== document.body) {
      for (const sibling of ancestor.parentElement?.children || []) {
        if (sibling !== ancestor) {
          background.push([sibling, sibling.hasAttribute('inert')]);
          sibling.setAttribute('inert', '');
        }
      }
      ancestor = ancestor.parentElement;
    }
    closeRef.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      for (const [element, inert] of background) element.toggleAttribute('inert', inert);
      if (trigger?.isConnected) trigger.focus();
    };
  }, [closeRef, dialogRef]);

  return (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE)].filter((element) => {
      const collapsed = element.closest('details:not([open])');
      return !collapsed || element === collapsed.querySelector('summary');
    });
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };
}
