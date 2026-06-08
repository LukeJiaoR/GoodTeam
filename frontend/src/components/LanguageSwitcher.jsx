export default function LanguageSwitcher({ activeValue, onChange, options, size = 'compact' }) {
  const paddingClass = size === 'wide' ? 'px-3 py-1' : 'px-2 py-0.5';
  const roundedClass = size === 'wide' ? 'rounded-lg' : 'rounded';

  return (
    <div className="flex bg-surface-raised p-0.5 rounded-lg border border-border-subtle select-none" aria-label="Language">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`${paddingClass} ${roundedClass} min-w-10 text-center text-2xs font-bold transition-all cursor-pointer ${activeValue === option.value ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
          aria-label={option.ariaLabel}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
