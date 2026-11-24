import type { CaseRequest } from './product-ui';
import type { WorkflowSelection } from './workflow-selection';

export function caseRequestParameters(request: CaseRequest, message: string): WorkflowSelection['parameters'] | null {
  const normalized = message.normalize('NFKC').trim();
  if (!new RegExp(request.pattern).test(normalized)) return null;
  return Object.fromEntries(Object.entries(request.switches).map(([name, rule]) => [name, new RegExp(rule.pattern).test(normalized)]));
}

export function caseRequestFields(selection: WorkflowSelection, request?: CaseRequest) {
  if (!selection.casePreparation || !request) return { fields: [], missing: [], hints: [] };
  const values = selection.parameters;
  const switches = Object.entries(request.switches);
  const enabled = switches.filter(([name]) => values[name] === true).map(([, rule]) => rule);
  const needed = [...new Set(enabled.flatMap(rule => rule.required))];
  const missing = needed.filter(name => values[name] == null || values[name] === '');
  const hints = [...new Set(enabled.flatMap(rule => rule.hint && rule.required.some(name => missing.includes(name)) ? [rule.hint] : []))];
  return { fields: [...switches.map(([name]) => name), ...needed], missing, hints };
}
