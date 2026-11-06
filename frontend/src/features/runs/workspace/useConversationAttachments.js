import { useEffect, useRef, useState } from 'react';
import { uploadConversationAttachment } from '../runApi';

export const IMAGE_ACCEPT = '.png,.jpg,.jpeg,.webp';
export const FILE_ACCEPT = `${IMAGE_ACCEPT},.pdf,.docx,.xlsx,.txt,.md,.csv,.json,.yaml,.yml,.log,.py,.js,.jsx,.ts,.tsx,.css,.html,.xml,.sql,.sh,.go,.rs,.java,.c,.cpp,.h`;
const MAX_FILES = 6;
const MAX_BYTES = 5 * 1024 * 1024;
const attachmentUrl = (projectKey, id) => `/api/projects/${encodeURIComponent(projectKey)}/task-assistant/attachments/${id}`;

export function useConversationAttachments({ projectKey, draftKey, acceptedMessage, t }) {
  const storageKey = draftKey ? `goodteam:conversation-files:${draftKey}` : '';
  const [files, setFiles] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      return Array.isArray(saved) ? saved.filter(item => /^[0-9a-f]{64}$/.test(item?.id) && typeof item.name === 'string')
        .slice(0, MAX_FILES).map(item => ({ ...item, localId: item.id, upload: 'ready', url: attachmentUrl(projectKey, item.id) })) : [];
    } catch { return []; }
  });
  const [error, setError] = useState('');
  const controllers = useRef(new Set());
  useEffect(() => {
    const active = controllers.current;
    return () => { active.forEach(controller => controller.abort()); };
  }, []);
  useEffect(() => {
    if (!storageKey) return;
    try {
      const saved = files.filter(item => item.upload === 'ready').map(item => ({ id: item.id, name: item.name, size: item.size, media_type: item.media_type, status: item.status }));
      if (saved.length) sessionStorage.setItem(storageKey, JSON.stringify(saved));
      else sessionStorage.removeItem(storageKey);
    } catch { /* In-memory drafts remain usable when tab storage is full. */ }
  }, [files, storageKey]);
  useEffect(() => {
    if (!acceptedMessage?.attachments?.length) return;
    const delivered = new Set(acceptedMessage.attachments.map(item => item.id));
    setFiles(current => current.filter(item => !delivered.has(item.id)));
  }, [acceptedMessage]);

  const upload = async (item) => {
    const controller = new AbortController();
    controllers.current.add(controller);
    setFiles(current => current.map(file => file.localId === item.localId ? { ...file, upload: 'uploading', error: '' } : file));
    try {
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = () => reject(new Error(t('work.attachments.readFailed')));
        reader.readAsDataURL(item.file);
      });
      const result = await uploadConversationAttachment(projectKey, { name: item.name, data: encoded }, { signal: controller.signal });
      if (!controller.signal.aborted) setFiles(current => {
        if (current.some(file => file.localId !== item.localId && file.id === result.attachment.id)) {
          return current.filter(file => file.localId !== item.localId);
        }
        return current.map(file => file.localId === item.localId
          ? { ...result.attachment, localId: item.localId, upload: 'ready' } : file);
      });
    } catch (caught) {
      if (!controller.signal.aborted) setFiles(current => current.map(file => file.localId === item.localId
        ? { ...file, upload: 'failed', error: caught.message || t('work.attachments.readFailed') } : file));
    } finally { controllers.current.delete(controller); }
  };
  const add = (selected) => {
    const incoming = Array.from(selected || []);
    if (!incoming.length) return;
    if (files.length + incoming.length > MAX_FILES) { setError(t('work.attachments.limit')); return; }
    if (incoming.some(file => !file.size || file.size > MAX_BYTES)) { setError(t('work.attachments.size')); return; }
    if (incoming.some(file => !FILE_ACCEPT.split(',').includes(`.${file.name.split('.').at(-1).toLowerCase()}`))) {
      setError(t('work.attachments.types')); return;
    }
    setError('');
    const added = incoming.map(file => ({ localId: crypto.randomUUID(), name: file.name, size: file.size, file, upload: 'uploading' }));
    setFiles(current => [...current, ...added]);
    added.forEach(item => { void upload(item); });
  };
  const remove = (localId) => setFiles(current => current.filter(item => item.localId !== localId));
  const clear = (sent) => {
    const ids = new Set(sent.map(item => item.id));
    setFiles(current => current.filter(item => !ids.has(item.id)));
    if (storageKey) {
      try {
        const remaining = JSON.parse(sessionStorage.getItem(storageKey) || '[]').filter(item => !ids.has(item.id));
        if (remaining.length) sessionStorage.setItem(storageKey, JSON.stringify(remaining));
        else sessionStorage.removeItem(storageKey);
      } catch { /* A late receipt must not remove a newer draft. */ }
    }
  };
  return { files, error, add, remove, retry: upload, clear, pending: files.some(item => item.upload !== 'ready') };
}
