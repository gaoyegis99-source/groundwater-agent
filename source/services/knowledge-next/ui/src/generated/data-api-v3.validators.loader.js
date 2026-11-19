// Generated from OpenAPI. Do not edit.
const initial = () => import('./data-api-v3.validators.initial.js');
const version = () => import('./data-api-v3.validators.version.js');
const readLoaders = {
  "data_capabilities": initial,
  "data_discovery": initial,
  "data_inventory": initial,
  "data_list_datasets": initial,
  "data_get_dataset_governance": version,
  "data_get_semantic_revision": version,
  "data_get_version": version,
  "data_list_dataset_versions": version,
  "data_list_semantic_revisions": version,
};
export function loadDataValidators(operationId) {
  if (operationId && Object.hasOwn(readLoaders, operationId)) return readLoaders[operationId]();
  return import('./data-api-v3.validators.js');
}
