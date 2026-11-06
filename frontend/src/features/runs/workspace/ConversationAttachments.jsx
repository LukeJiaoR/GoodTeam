import { useRef } from 'react';
import { FileText, ImagePlus, LoaderCircle, Plus, RotateCcw, X } from 'lucide-react';
import { FILE_ACCEPT, IMAGE_ACCEPT } from './useConversationAttachments';

export function AttachmentPicker({ onAdd, disabled, t }) {
  const input = useRef(null);
  const menu = useRef(null);
  const choose = (accept) => {
    menu.current.open = false;
    input.current.accept = accept;
    input.current.click();
  };
  return <>
    <input ref={input} hidden type="file" multiple accept={FILE_ACCEPT} disabled={disabled} aria-label={t('work.attachments.add')}
      onChange={event => { onAdd(event.target.files); event.target.value = ''; }} />
    <details ref={menu} className="conversation-attachment-menu" onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.currentTarget.open = false; event.currentTarget.querySelector('summary').focus(); }
    }}>
      <summary aria-label={t('work.attachments.add')} aria-disabled={disabled} title={t('work.attachments.add')}
        onClick={event => { if (disabled) event.preventDefault(); }}><Plus size={17} aria-hidden="true" /></summary>
      <div>
        <button type="button" disabled={disabled} onClick={() => choose(IMAGE_ACCEPT)}><ImagePlus size={15} />{t('work.attachments.images')}</button>
        <button type="button" disabled={disabled} onClick={() => choose(FILE_ACCEPT)}><FileText size={15} />{t('work.attachments.files')}</button>
        <p>{t('work.attachments.hint')}</p>
      </div>
    </details>
  </>;
}

export function AttachmentList({ files, onRemove, onRetry, disabled, t }) {
  if (!files?.length) return null;
  return <ul className="conversation-attachments" aria-label={t('work.attachments.label')}>
    {files.map(item => {
      const url = /^\/api\/projects\/[^/]+\/task-assistant\/attachments\/[0-9a-f]{64}$/.test(item.url || '') ? item.url : '';
      const visual = item.media_type?.startsWith('image/') && url
        ? <img src={url} alt={item.name} loading="lazy" /> : <FileText size={20} aria-hidden="true" />;
      return <li key={item.localId || item.id} data-upload={item.upload}>
        {url ? <a href={url} target="_blank" rel="noreferrer" title={item.name}>{visual}<span>{item.name}<small>{Math.max(1, Math.round(item.size / 1024))} KB</small></span></a>
          : <span className="conversation-attachment-file">{visual}<span>{item.name}<small>{item.upload === 'uploading' ? t('work.attachments.uploading') : t('work.attachments.failed')}</small></span></span>}
        {item.upload === 'uploading' ? <LoaderCircle size={14} className="conversation-attachment-spinner" aria-hidden="true" /> : null}
        {onRetry && item.upload === 'failed' ? <button type="button" disabled={disabled} aria-label={t('work.attachments.retry', { name: item.name })} onClick={() => onRetry(item)}><RotateCcw size={14} /></button> : null}
        {onRemove ? <button type="button" disabled={disabled} aria-label={t('work.attachments.remove', { name: item.name })} onClick={() => onRemove(item.localId)}><X size={14} /></button> : null}
        {item.error ? <p role="alert">{item.error}</p> : null}
        {['partial', 'empty'].includes(item.status) ? <p>{t(`work.attachments.${item.status}`)}</p> : null}
      </li>;
    })}
  </ul>;
}
