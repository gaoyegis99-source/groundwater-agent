export type StreamProgress = {
  streamCursor: number;
  snapshotCursor: number;
  dirty: boolean;
  forced: boolean;
};

export const initialStreamProgress = (): StreamProgress => ({
  streamCursor: 0, snapshotCursor: -1, dirty: true, forced: false,
});

export const connectStream = (state: StreamProgress, cursor: number): StreamProgress => ({
  ...state, streamCursor: cursor,
});

export const receiveStreamEvent = (state: StreamProgress, id: number): { state: StreamProgress; accepted: boolean } => {
  if (id <= state.streamCursor) return { state, accepted: false };
  return {
    state: { ...state, streamCursor: id, dirty: state.dirty || id > state.snapshotCursor },
    accepted: true,
  };
};

export const forceStreamRefresh = (state: StreamProgress): StreamProgress => ({ ...state, dirty: true, forced: true });

export const beginSnapshotRead = (state: StreamProgress): StreamProgress => ({ ...state, dirty: false, forced: false });

export const applySnapshot = (state: StreamProgress, cursor: number): { state: StreamProgress; accepted: boolean } => {
  if (cursor < state.snapshotCursor) return { state, accepted: false };
  return {
    state: {
      ...state,
      snapshotCursor: cursor,
      dirty: state.forced || state.streamCursor > cursor ? state.dirty : false,
    },
    accepted: true,
  };
};

export type DetailScope = { results: boolean; drafts: boolean };
const metadataEvents = new Set(['agent.activity', 'agent.planRecorded', 'workflow.stageChanged', 'workflow.communicationChanged', 'confirmation.processing']);

export const noDetails = (): DetailScope => ({ results: false, drafts: false });
export const allDetails = (): DetailScope => ({ results: true, drafts: true });
export const hasDetails = (scope: DetailScope): boolean => scope.results || scope.drafts;
export const isMetadataEvent = (type: string): boolean => metadataEvents.has(type);
export const mergeDetails = (left: DetailScope, right: DetailScope): DetailScope => ({
  results: left.results || right.results,
  drafts: left.drafts || right.drafts,
});

export const detailsAfterEvent = (scope: DetailScope, type: string): DetailScope => {
  if (isMetadataEvent(type)) return scope;
  return {
    results: scope.results || /^(workflow|job)\.(succeeded|failed|timedOut|cancelled)$/.test(type),
    drafts: scope.drafts || type.startsWith('workflow_draft.')
      || ['turn.accepted', 'intent_routing.resolved', 'agent.complete', 'agent.failed'].includes(type),
  };
};
