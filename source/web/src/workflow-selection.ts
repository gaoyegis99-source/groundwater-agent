import { readJsonCache } from './browser-cache';
import { scopedStorageKey } from './browser-scope';
import type { Capabilities, ResourceItem, ResourceReference } from './types';

export type WorkflowModel = Capabilities['models'][number];
const identityFields = ['modelId', 'modelVersion', 'modelChecksum', 'workflowId', 'workflowVersion', 'workflowChecksum'] as const;
export type WorkflowIdentity = Record<typeof identityFields[number], string>;
export interface WorkflowSelection {
  target: WorkflowIdentity;
  bindings: Record<string, string>;
  parameters: Record<string, string | number | boolean | null>;
  continuation?: { runId: string; outputName: string };
  casePreparation?: true;
}

export function modelIdentity(model: WorkflowModel): WorkflowIdentity | null {
  if (!identityFields.every(key => typeof model[key] === 'string' && model[key])) return null;
  return Object.fromEntries(identityFields.map(key => [key, model[key]])) as WorkflowIdentity;
}

export function matchesModel(selection: WorkflowSelection, model: WorkflowModel) {
  return identityFields.every(key => selection.target[key] === model[key]);
}

export function isWorkflowSelection(value: unknown): value is WorkflowSelection {
  if (!value || typeof value !== 'object') return false;
  const selection = value as WorkflowSelection;
  return !!selection.target && identityFields.every(key => typeof selection.target[key] === 'string' && !!selection.target[key])
    && !!selection.bindings && typeof selection.bindings === 'object' && Object.values(selection.bindings).every(ref => typeof ref === 'string')
    && !!selection.parameters && typeof selection.parameters === 'object' && Object.values(selection.parameters).every(item =>
      item === null || typeof item === 'string' || typeof item === 'boolean' || (typeof item === 'number' && Number.isFinite(item)))
    && (selection.continuation === undefined || (!!selection.continuation && typeof selection.continuation.runId === 'string'
      && typeof selection.continuation.outputName === 'string'))
    && (selection.casePreparation === undefined || selection.casePreparation === true);
}

export function storedWorkflowSelection(scope: string, session: string) {
  return readJsonCache(scopedStorageKey(scope, 'draft-workflow', session), isWorkflowSelection);
}

export function saveWorkflowSelection(scope: string, session: string, selection: WorkflowSelection | null) {
  const key = scopedStorageKey(scope, 'draft-workflow', session);
  if (selection) sessionStorage.setItem(key, JSON.stringify(selection));
  else sessionStorage.removeItem(key);
}

export function workflowFileKey(resource: ResourceReference) {
  return JSON.stringify([resource.datasetId, resource.versionId, resource.semanticId, resource.objectId, resource.checksum]);
}

export function workflowSelectionBody(workflow: WorkflowSelection, attachments: ResourceItem[]) {
  const indexes = new Map(attachments.map((item, index) => [workflowFileKey(item), index]));
  return {
    ...workflow.target,
    inputBindings: Object.fromEntries(Object.entries(workflow.bindings).flatMap(([name, key]) => {
      const index = indexes.get(key);
      return index === undefined ? [] : [[name, index]];
    })),
    parameters: workflow.parameters,
    ...(workflow.continuation && { continuation: workflow.continuation }),
  };
}
