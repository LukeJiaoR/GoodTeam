import { useEffect, useState } from 'react';
import FileLocation from '../../components/ui/FileLocation';
import { documentTitle, fileName } from '../../lib/presentation/filePresentation';
import CollectionToolbar from '../../components/ui/CollectionToolbar';
import MarkdownView from '../../components/ui/MarkdownView';
import ProductError from '../../components/ui/ProductError';
import { knowledgeApi } from './knowledgeApi';

function DocumentContent({ projectKey, selectedKey, t }) {
  const [result, setResult] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    knowledgeApi.getKnowledgeArtifact(projectKey, selectedKey, { signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setResult({ content: payload.content || '' }); })
      .catch((error) => { if (!controller.signal.aborted) setResult({ error }); });
    return () => controller.abort();
  }, [projectKey, selectedKey]);
  if (!result) return <div className="knowledge-document-loading" role="status" aria-busy="true"><p>{t('knowledge.artifacts.loading')}</p><span /><span /><span /></div>;
  if (result.error) return <ProductError error={result.error} t={t} />;
  return <div className="knowledge-artifact-rendered-wrap" tabIndex={0} aria-label={documentTitle(selectedKey, t)}>{result.content ? <MarkdownView content={result.content} /> : <p className="product-empty-copy">{t('knowledge.artifacts.emptyContent')}</p>}</div>;
}

export default function KnowledgeArtifacts({ entries, projectKey, search, setSearch, t }) {
  const [query, setQuery] = useState('');
  const requestedKey = search.get('artifact');
  const selectedKey = entries.some(([key]) => key === requestedKey) ? requestedKey : entries[0]?.[0] || '';
  const visibleEntries = entries.filter(([key, path]) => `${documentTitle(key, t)} ${key} ${path}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  if (!entries.length) return <p className="product-empty-copy">{t('knowledge.artifacts.empty')}</p>;
  return (
    <div className="knowledge-artifacts">
      <aside>
        <CollectionToolbar label={t('knowledge.artifacts.search')} query={query} onQueryChange={setQuery} count={visibleEntries.length} t={t} />
        {!visibleEntries.length ? <p className="product-empty-copy">{t('collection.noResults')}</p> : null}
        <ol>{visibleEntries.map(([key, path]) => <li key={key}><button aria-pressed={key === selectedKey} className={key === selectedKey ? 'is-active' : ''} onClick={() => { const next = new URLSearchParams(search); next.set('view', 'artifacts'); next.set('artifact', key); setSearch(next); }}><strong>{documentTitle(key, t)}</strong><small>{fileName(path)}</small></button></li>)}</ol>
      </aside>
      <article className="knowledge-document">
        <header><h2>{documentTitle(selectedKey, t)}</h2><FileLocation key={selectedKey} path={entries.find(([key]) => key === selectedKey)?.[1]} t={t} /></header>
        <DocumentContent key={`${projectKey}:${selectedKey}`} projectKey={projectKey} selectedKey={selectedKey} t={t} />
      </article>
    </div>
  );
}
