const ERROR_MESSAGE_KEYS = Object.freeze(Object.fromEntries([
  'invalid_request',
  'invalid_project_root',
  'project_root_not_absolute',
  'invalid_project_key',
  'invalid_project_mode',
  'project_not_found',
  'project_root_not_found',
  'project_root_not_directory',
  'project_root_not_readable',
  'project_root_unavailable',
  'project_workspace_not_isolated',
  'project_readiness_mismatch',
  'project_readiness_missing',
  'same_origin_required',
  'internal_error',
  'invalid_response',
].map((code) => [code, `projects.errors.${code}`])));

const RECOVERY_PRESENTATIONS = Object.freeze({
  choose_another_directory: 'choose',
  retry: 'retry',
  retry_request: 'retry',
  retry_from_goodteam_origin: 'reload_page',
  reload_project: 'retry',
  return_to_projects: 'return',
  fix_permissions: 'guidance',
  report_request_id: 'guidance',
  edit_project_settings: 'ignore',
  choose_project_mode: 'ignore',
  choose_project: 'ignore',
});

export function projectErrorMessageKey(code) {
  return ERROR_MESSAGE_KEYS[code] || 'product.errors.generic';
}

export function projectRecoveryPresentation(recovery) {
  return RECOVERY_PRESENTATIONS[recovery] || 'ignore';
}
