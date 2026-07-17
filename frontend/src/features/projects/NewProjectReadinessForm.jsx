import { useState } from 'react';
import Button from '../../components/ui/Button';
import { translate } from '../../i18n';

const INITIAL_VALUES = {
  goal: '',
  brief: '',
  tech_preferences: '',
  constraints: '',
  prohibited_operations: '',
};

const FIELDS = [
  ['goal', 'text'],
  ['brief', 'textarea'],
  ['tech_preferences', 'textarea'],
  ['constraints', 'textarea'],
  ['prohibited_operations', 'textarea'],
];

export default function NewProjectReadinessForm({ busy = false, language = 'en', onSubmit }) {
  const t = (key, params) => translate(language, key, params);
  const [values, setValues] = useState(INITIAL_VALUES);

  const submit = (event) => {
    event.preventDefault();
    const inputs = Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, value.trim()]),
    );
    onSubmit?.(inputs);
  };

  return (
    <form className="readiness-init-form" onSubmit={submit}>
      <div className="projects-section-heading">
        <h3>{t('readiness.init.title')}</h3>
        <p>{t('readiness.init.description')}</p>
      </div>
      <div className="readiness-form-grid">
        {FIELDS.map(([name, type]) => {
          const id = `readiness-init-${name}`;
          const shared = {
            id,
            name,
            value: values[name],
            onChange: (event) => setValues((current) => ({
              ...current,
              [name]: event.target.value,
            })),
            required: name === 'goal' || name === 'brief',
          };
          return (
            <label className={`readiness-form-field readiness-form-field--${name}`} htmlFor={id} key={name}>
              <span>{t(`readiness.init.${name}.label`)}</span>
              {type === 'textarea'
                ? <textarea {...shared} rows={name === 'brief' ? 4 : 3} placeholder={t(`readiness.init.${name}.placeholder`)} />
                : <input {...shared} type="text" placeholder={t(`readiness.init.${name}.placeholder`)} />}
            </label>
          );
        })}
      </div>
      <p className="readiness-form-boundary">{t('readiness.init.readOnlyBoundary')}</p>
      <Button busy={busy} type="submit">{t('readiness.actions.startInitialization')}</Button>
    </form>
  );
}
