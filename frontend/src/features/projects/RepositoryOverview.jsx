import MarkdownView from '../../components/ui/MarkdownView';
import FileLocation from '../../components/ui/FileLocation';
import { fileName, isAbsoluteFilePath } from '../../lib/presentation/filePresentation';
import { translate } from '../../i18n';

const OVERVIEW_FIELDS = [
  ['technology_stack', 'readiness.overview.stack'],
  ['directory_structure', 'readiness.overview.structure'],
  ['entry_points', 'readiness.overview.entryPoints'],
  ['key_documents', 'readiness.overview.documents'],
  ['constraints', 'readiness.overview.constraints'],
  ['risks', 'readiness.overview.risks'],
  ['next_steps', 'readiness.overview.nextSteps'],
];

const COMMAND_GROUPS = ['run', 'test', 'lint', 'format', 'build', 'check'];

function firstValue(source, names) {
  for (const name of names) {
    if (source?.[name] !== undefined && source[name] !== null) return source[name];
  }
  return undefined;
}

function unwrapArtifact(value) {
  let artifact = value;
  for (const key of ['artifact', 'overview', 'data']) {
    if (artifact && typeof artifact === 'object' && !Array.isArray(artifact) && artifact[key]) {
      artifact = artifact[key];
    }
  }
  if (artifact && typeof artifact === 'object' && !Array.isArray(artifact) && artifact.content) {
    artifact = artifact.content;
  }
  if (typeof artifact !== 'string') return artifact || null;
  try {
    return JSON.parse(artifact);
  } catch {
    return { raw_summary: artifact };
  }
}

function normalizeOverview(value) {
  const source = unwrapArtifact(value);
  if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
  return {
    technology_stack: firstValue(source, ['technology_stack', 'tech_stack', 'stack']),
    directory_structure: firstValue(source, ['directory_structure', 'directories', 'structure', 'tree']),
    entry_points: firstValue(source, ['entry_points', 'entrypoints', 'entries']),
    commands: firstValue(source, ['commands', 'discovered_commands']),
    key_documents: firstValue(source, ['key_documents', 'documents', 'docs']),
    constraints: firstValue(source, ['constraints', 'project_constraints']),
    risks: firstValue(source, ['risks', 'potential_risks']),
    next_steps: firstValue(source, ['next_steps', 'recommendations']),
    raw_summary: source.raw_summary,
  };
}

function asItems(value) {
  if (value === undefined || value === null || value === '') return [];
  if (Array.isArray(value)) return value;
  if (typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => (
      item && typeof item === 'object' && !Array.isArray(item)
        ? { label: key, ...item }
        : { label: key, value: item }
    ));
  }
  return [value];
}

function itemText(item) {
  if (typeof item === 'string' || typeof item === 'number') return String(item);
  if (!item || typeof item !== 'object') return '';
  return String(
    item.name
    || item.path
    || item.label
    || item.title
    || item.description
    || item.value
    || '',
  );
}

function commandText(command) {
  if (typeof command === 'string') return command;
  return String(command?.command || command?.value || command?.name || '');
}

function commandGroups(commands) {
  if (!commands) return [];
  if (Array.isArray(commands)) {
    const grouped = new Map();
    commands.forEach((command) => {
      const kind = String(command?.kind || 'other').toLowerCase();
      grouped.set(kind, [...(grouped.get(kind) || []), command]);
    });
    return [...grouped.entries()];
  }
  if (typeof commands !== 'object') return [['other', [commands]]];
  const known = COMMAND_GROUPS
    .filter((kind) => asItems(commands[kind]).length > 0)
    .map((kind) => [kind, asItems(commands[kind])]);
  const remaining = Object.entries(commands)
    .filter(([kind]) => !COMMAND_GROUPS.includes(kind))
    .flatMap(([, values]) => asItems(values));
  return remaining.length > 0 ? [...known, ['other', remaining]] : known;
}

export default function RepositoryOverview({ artifact, language = 'en' }) {
  const t = (key, params) => translate(language, key, params);
  const overview = normalizeOverview(artifact);
  if (!overview) return null;

  const fields = OVERVIEW_FIELDS
    .map(([field, label]) => ({ field, label, items: asItems(overview[field]) }))
    .filter(({ items }) => items.length > 0);
  const commands = commandGroups(overview.commands);

  return (
    <section className="repository-overview" aria-labelledby="repository-overview-title">
      <div className="projects-section-heading">
        <h2 id="repository-overview-title">{t('readiness.overview.title')}</h2>
        <p>{t('readiness.overview.description')}</p>
      </div>

      {overview.raw_summary ? (
        <div className="repository-overview-raw-wrap">
          <MarkdownView content={overview.raw_summary} />
        </div>
      ) : null}

      <div className="repository-overview-grid">
        {fields.map(({ field, label, items }) => (
          <section className="repository-overview-group" key={field}>
            <h3>{t(label)}</h3>
            <ul>
              {items.map((item, index) => {
                const text = itemText(item);
                const path = typeof item === 'object' ? item?.path : text;
                if (isAbsoluteFilePath(path)) {
                  return <li key={`${path}-${index}`}><FileLocation path={path} label={item?.name || item?.title || fileName(path)} t={t} /></li>;
                }
                return text ? <li key={`${text}-${index}`}>{text}</li> : null;
              })}
            </ul>
          </section>
        ))}

        {commands.length > 0 ? (
          <section className="repository-overview-group repository-overview-commands">
            <h3>{t('readiness.overview.commands')}</h3>
            {commands.map(([kind, values]) => (
              <div className="repository-command-group" key={kind}>
                <h4>{t(`readiness.overview.command.${kind}`)}</h4>
                <ul>
                  {values.map((command, index) => {
                    const value = commandText(command);
                    return value ? <li key={`${value}-${index}`}><code>{value}</code></li> : null;
                  })}
                </ul>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </section>
  );
}
