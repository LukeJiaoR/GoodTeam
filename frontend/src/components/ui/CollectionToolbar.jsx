import { useRef } from 'react';
import { Search, X } from 'lucide-react';

export default function CollectionToolbar({ label, query, onQueryChange, filters = [], selected, onSelect, count, t }) {
  const inputRef = useRef(null);
  return (
    <div className="collection-toolbar">
      <label className="collection-search">
        <Search aria-hidden="true" size={17} />
        <span className="sr-only">{label}</span>
        <input ref={inputRef} type="search" name="q" autoComplete="off" spellCheck={false} placeholder={label} value={query} onChange={(event) => onQueryChange(event.target.value)} />
        {query ? <button type="button" aria-label={t('collection.clearSearch')} onClick={() => { onQueryChange(''); inputRef.current?.focus(); }}><X aria-hidden="true" size={16} /></button> : null}
      </label>
      {filters.length ? (
        <div className="collection-filters" role="group" aria-label={t('collection.filter')}>
          {filters.map(([value, title, total]) => <button type="button" key={value} aria-pressed={selected === value} onClick={() => onSelect(value)}>
            {title}{Number.isInteger(total) ? <> <span className="collection-filter-count">{total}</span></> : null}
          </button>)}
        </div>
      ) : null}
      <span className="collection-count" role="status" aria-atomic="true">{t('collection.results', { count })}</span>
    </div>
  );
}
