import { routes } from './routes';

const STORAGE_KEY = 'goodteam.project-navigation.v1';
const PAGE_KEYS = ['work', 'review', 'knowledge', 'team'];
const MAX_PROJECTS = 20;
const MAX_URL_LENGTH = 8192;

function sessionStore() {
  try { return globalThis.sessionStorage; } catch { return null; }
}

function projectLocation(url) {
  if (typeof url !== 'string' || url.length > MAX_URL_LENGTH || url.includes('#')) return null;
  const [pathname] = url.split('?');
  try {
    // Links normalize dot segments and backslashes; validate the destination
    // the browser will actually use, rather than a misleading stored prefix.
    const parsed = new URL(url, 'https://goodteam.invalid');
    if (parsed.origin !== 'https://goodteam.invalid' || parsed.pathname !== pathname) return null;
  } catch { return null; }
  const match = pathname.match(/^\/projects\/([^/]+)\/(work(?:\/runs\/[^/]+)?|review(?:\/[^/]+)?|knowledge|team)$/);
  if (!match) return null;
  try {
    const projectKey = decodeURIComponent(match[1]);
    if (!projectKey.trim() || Array.from(projectKey).some(character => character.charCodeAt(0) < 32)) return null;
    return { projectKey, page: match[2].split('/')[0], url };
  } catch { return null; }
}

function readEntries(storage) {
  try {
    const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.filter(entry => (
      entry && typeof entry.projectKey === 'string' && entry.pages && typeof entry.pages === 'object'
    )).slice(-MAX_PROJECTS) : [];
  } catch { return []; }
}

// Store visited URLs only, never workflow state, draft content or mutation capabilities.
// Old/removed tasks and sources still resolve through each page's existing read boundary.
export function rememberProjectLocation(pathname, search = '', storage = sessionStore()) {
  const location = projectLocation(`${pathname}${search}`);
  if (!location) return;
  const entries = readEntries(storage);
  const previous = entries.find(entry => entry.projectKey === location.projectKey);
  const pages = Object.fromEntries(PAGE_KEYS.flatMap(page => {
    const saved = projectLocation(previous?.pages?.[page]);
    return saved?.projectKey === location.projectKey && saved.page === page ? [[page, saved.url]] : [];
  }));
  pages[location.page] = location.url;
  const next = entries.filter(entry => entry.projectKey !== location.projectKey);
  next.push({ projectKey: location.projectKey, pages });
  try { storage?.setItem(STORAGE_KEY, JSON.stringify(next.slice(-MAX_PROJECTS))); } catch { /* Navigation remains usable without session storage. */ }
}

export function projectNavigationDestinations(projectKey, pathname = '', search = '', storage = sessionStore()) {
  const defaults = {
    work: routes.projectWork(projectKey),
    review: routes.projectReview(projectKey),
    knowledge: routes.projectKnowledge(projectKey),
    team: routes.projectTeam(projectKey),
  };
  const savedPages = readEntries(storage).find(entry => entry.projectKey === projectKey)?.pages;
  for (const page of PAGE_KEYS) {
    const saved = projectLocation(savedPages?.[page]);
    if (saved?.projectKey === projectKey && saved.page === page) defaults[page] = saved.url;
  }
  const current = projectLocation(`${pathname}${search}`);
  if (current?.projectKey === projectKey) defaults[current.page] = current.url;
  return defaults;
}
