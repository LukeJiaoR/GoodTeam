export function projectActivity(item) {
  if (item.attention?.counts.total > 0 || item.attention_count > 0 || item.readiness?.lifecycle === 'attention_required') return 'attention_required';
  if (item.active_run) return 'active';
  return item.readiness?.lifecycle || 'ready';
}

export function browseProjects(projects, { query, filter }) {
  const term = query.trim().toLocaleLowerCase();
  return projects.filter((item) => {
    const searchable = [item.project.display_name, item.project.project_key, item.project.root].join(' ').toLocaleLowerCase();
    return searchable.includes(term) && (filter === 'all' || (filter === 'active' ? Boolean(item.active_run) : projectActivity(item) === filter));
  });
}
