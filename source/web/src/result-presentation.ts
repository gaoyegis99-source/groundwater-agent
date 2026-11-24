import { productUi } from './product-ui';
import type { ExecutionTask, Turn } from './types';

export function frozenRunArguments(turn: Turn, run: ExecutionTask) {
  const confirmation = turn.confirmations.find(item => item.confirmationId === run.confirmationId
    && item.agentRunId === run.agentRunId && item.goalRevision === run.goalRevision);
  return confirmation?.actions.find(action => action.toolCallId === run.toolCallId
    && action.modelId === run.modelId)?.arguments;
}

export function inactiveResultGroups(modelId: string, args?: Record<string, unknown>) {
  return productUi().models[modelId]?.resultOverview?.inactiveGroups?.filter(group => args?.[group.parameter] === false) ?? [];
}

export function isInactiveOutput(branches: ReturnType<typeof inactiveResultGroups>, name?: string) {
  return name !== undefined && branches.some(branch => branch.outputs.includes(name));
}
