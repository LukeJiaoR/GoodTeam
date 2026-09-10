import { useRef, useState } from 'react';
import { Check, ChevronDown, Copy, FolderOpen } from 'lucide-react';
import { fileName } from '../../lib/presentation/filePresentation';
import './fileLocation.css';

export default function FileLocation({ path, label, t }) {
  const detailsRef = useRef(null);
  const [copyState, setCopyState] = useState('idle');
  if (typeof path !== 'string' || !path) return null;
  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(path);
      setCopyState('copied');
    } catch {
      setCopyState('error');
    }
  };
  const onKeyDown = (event) => {
    if (event.key !== 'Escape' || !detailsRef.current?.open) return;
    event.preventDefault();
    event.stopPropagation();
    detailsRef.current.open = false;
    detailsRef.current.querySelector('summary')?.focus();
  };
  return (
    <details ref={detailsRef} className="file-location" onKeyDown={onKeyDown} onToggle={() => setCopyState('idle')}>
      <summary><FolderOpen aria-hidden="true" size={14} /><span>{label || t('files.location')}</span><ChevronDown aria-hidden="true" size={13} /></summary>
      <div className="file-location__content">
        <span className="file-location__label">{t('files.fullPath')}</span>
        <code>{path}</code>
        <button type="button" onClick={copyPath} aria-label={t('files.copyAria', { name: fileName(path) })}>
          {copyState === 'copied' ? <Check aria-hidden="true" size={14} /> : <Copy aria-hidden="true" size={14} />}
          {t(copyState === 'copied' ? 'files.copied' : 'files.copy')}
        </button>
        {copyState === 'error' ? <p role="alert">{t('files.copyError')}</p> : null}
        {copyState === 'copied' ? <span className="sr-only" role="status">{t('files.copied')}</span> : null}
      </div>
    </details>
  );
}
