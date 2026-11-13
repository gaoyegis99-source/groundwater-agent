import { expect, it } from 'vitest';
import { allDetails, applySnapshot, beginSnapshotRead, connectStream, detailsAfterEvent, forceStreamRefresh, hasDetails, initialStreamProgress, isMetadataEvent, mergeDetails, noDetails, receiveStreamEvent } from './session-stream-state';

it('deduplicates events and stops reading when the snapshot covers them', () => {
  let state = connectStream(initialStreamProgress(), 10);
  state = beginSnapshotRead(state);
  expect(receiveStreamEvent(state, 10).accepted).toBe(false);
  const event = receiveStreamEvent(state, 12);
  expect(event.accepted).toBe(true);
  state = event.state;
  expect(state.dirty).toBe(true);
  state = applySnapshot(state, 12).state;
  expect(state.dirty).toBe(false);
  expect(receiveStreamEvent(state, 12).accepted).toBe(false);
});

it('retains a pending refresh when an event outpaces the snapshot', () => {
  let state = connectStream(initialStreamProgress(), 10);
  state = receiveStreamEvent(beginSnapshotRead(state), 14).state;
  state = applySnapshot(state, 11).state;
  expect(state.dirty).toBe(true);
  expect(applySnapshot(state, 10)).toEqual({ state, accepted: false });
});

it('keeps a forced refresh even when an in-flight snapshot covers the cursor', () => {
  const state = forceStreamRefresh(beginSnapshotRead(connectStream(initialStreamProgress(), 10)));
  expect(applySnapshot(state, 10).state.dirty).toBe(true);
});

it('tracks only the details affected by business events', () => {
  const empty = noDetails();
  expect(detailsAfterEvent(empty, 'agent.activity')).toBe(empty);
  expect(isMetadataEvent('agent.activity')).toBe(true);
  expect(detailsAfterEvent(empty, 'workflow.succeeded')).toEqual({ results: true, drafts: false });
  expect(detailsAfterEvent(empty, 'workflow_draft.published')).toEqual({ results: false, drafts: true });
  expect(hasDetails(mergeDetails(detailsAfterEvent(empty, 'agent.failed'), empty))).toBe(true);
  expect(mergeDetails(allDetails(), empty)).toEqual(allDetails());
});

it('ignores out-of-order and malformed event IDs without poisoning the resume cursor', () => {
  const state = receiveStreamEvent(connectStream(initialStreamProgress(), 10), 12).state;
  for (const id of [11, 12, NaN, Infinity, 12.5]) {
    expect(receiveStreamEvent(state, id)).toEqual({ state, accepted: false });
  }
  expect(receiveStreamEvent(state, 13).state.streamCursor).toBe(13);
});
