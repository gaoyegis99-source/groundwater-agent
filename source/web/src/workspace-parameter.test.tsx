import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { capabilitiesFixture, historyFixture, sessionResponseFixture, sessionResultsFixture } from './test-fixtures';
import type { Confirmation } from './types';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
let disconnect: () => void;
beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/sessions/parameter-input');
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('EventSource', class {
    onerror?: () => void;
    constructor() { disconnect = () => this.onerror?.(); }
    addEventListener() {}
    close() {}
  });
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(max-width: 1050px)', addEventListener: vi.fn(), removeEventListener: vi.fn() })));
});

function mockPreparation(confirmation?: Confirmation, failure?: 'network after commit' | '502 after commit' | '502 before commit') {
  const history = historyFixture({
    sessionId: 'parameter-input', turns: [{ status: confirmation ? 'WAITING_CONFIRMATION' : 'COMPLETE', confirmations: confirmation ? [confirmation] : [] }],
    preparation: {
      schemaVersion: 'task-preparation.v1', preparationId: 'preparation-1', revision: 1,
      sessionId: 'parameter-input', agentRunId: 'agent-1', goalRevision: 1,
      taskId: null, baseTaskRevision: null, status: 'BLOCKED', freshForCurrentTurn: true, disposition: 'ACTIVE',
      entries: [{ targetKind: 'EXECUTABLE_MODEL', targetId: 'groundwater.summary', targetVersion: '1.0.0',
        selectionPolicy: 'PINNED', status: 'BLOCKED', inputs: {}, knowledgeRefs: [], parameters: {}, parameterSources: {},
        checks: [], blockingReasons: ['WORKFLOW_DRAFT_MISSING_PARAMETER:axis'] }],
    },
  });
  const capabilities = capabilitiesFixture();
  capabilities.models.find(item => item.modelId === 'groundwater.summary')!.inputSchema = { type: 'object', properties: { axis: { type: 'string' } }, required: [] };
  const revisions: { requestId: string; actions: unknown[] }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (failure && confirmation && url.endsWith('/revisions')) {
      const body = JSON.parse(String(request?.body));
      revisions.push(body);
      if (failure === '502 before commit' && revisions.length === 1) {
        return new Response(JSON.stringify({ code: 'BAD_GATEWAY' }), { status: 502 });
      }
      history.turns[0].confirmations = [
        { ...confirmation, status: 'SUPERSEDED' },
        { ...confirmation, confirmationId: 'revision-2', revision: 2, kind: 'EDITED',
          requestId: body.requestId, replacesConfirmationId: confirmation.confirmationId,
          actions: confirmation.actions.map((action, index) => ({ ...action, arguments: body.actions[index].arguments })) },
      ];
      history.eventCursor += 1;
      if (failure === 'network after commit') throw new TypeError('lost committed revision receipt');
      if (failure === '502 after commit') return new Response(JSON.stringify({ code: 'BAD_GATEWAY' }), { status: 502 });
      return json(sessionResponseFixture({ confirmation: history.turns[0].confirmations[1], sessionId: 'parameter-input' }));
    }
    if (url.endsWith('/capabilities')) return json(capabilities);
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(history);
    throw new Error(`unexpected endpoint ${url}`);
  }));
  return revisions;
}

it('preserves the draft and returns focus to the composer when compact parameter entry needs a new message', async () => {
  mockPreparation();
  render(<App />);
  await screen.findByTestId('recovered-active-session');
  const composer = screen.getByRole('textbox', { name: '消息输入框' });
  fireEvent.change(composer, { target: { value: '保留原来的分析条件' } });
  const open = await screen.findByRole('button', { name: '打开任务工作台' });
  open.focus(); fireEvent.click(open);
  const panel = await screen.findByRole('heading', { name: '任务', exact: true }).then(heading => heading.closest('aside')!);
  fireEvent.click(within(panel).getByRole('tab', { name: '任务' }));
  fireEvent.click(within(panel).getByRole('button', { name: '补充参数' }));
  expect(composer).toHaveValue('保留原来的分析条件\n补充参数 axis：');
  await waitFor(() => expect(composer).toBeVisible());
  await act(async () => { await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))); });
  expect(composer).toHaveFocus();
});

it('keeps pending confirmation parameter editing inside the compact workspace', async () => {
  mockPreparation({
    confirmationId: 'pending-1', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null,
    actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'summary', workflowVersion: '1.0.0', arguments: { axis: 'columns' } }],
  });
  render(<App />);
  await screen.findByTestId('recovered-active-session');
  const composer = screen.getByRole('textbox', { name: '消息输入框' });
  fireEvent.change(composer, { target: { value: '原有草稿' } });
  fireEvent.click(await screen.findByRole('button', { name: '打开任务工作台' }));
  const panel = await screen.findByRole('heading', { name: '任务', exact: true }).then(heading => heading.closest('aside')!);
  fireEvent.click(within(panel).getByRole('tab', { name: '任务' }));
  fireEvent.click(within(panel).getByRole('button', { name: '补充参数' }));
  expect(await within(panel).findByTestId('confirmation-editor')).toBeVisible();
  expect(panel).toBeVisible();
  expect(within(panel).getByRole('tab', { name: /参数/ })).toHaveAttribute('aria-selected', 'true');
  expect(composer).toHaveValue('原有草稿');
});

it('keeps edited parameters but prevents saving against an unverified snapshot', async () => {
  mockPreparation({
    confirmationId: 'pending-1', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null,
    actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'summary', workflowVersion: '1.0.0', arguments: { axis: 'columns' } }],
  });
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '编辑参数' }));
  const editor = await screen.findByTestId('confirmation-editor');
  const input = within(editor).getByRole('textbox');
  const save = within(editor).getByRole('button', { name: '保存为新修订' });
  fireEvent.change(input, { target: { value: 'flatten' } });
  expect(save).toBeEnabled();
  act(disconnect);
  expect(save).toBeDisabled();
  expect(input).toHaveValue('flatten');
});


it.each(['network after commit', '502 after commit', '502 before commit'] as const)(
  'reconciles the revision receipt before another approval (%s)', async failure => {
  const revisions = mockPreparation({
    confirmationId: 'pending-1', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1', revision: 1,
    reason: '', decision: null, decisionState: null,
    actions: [{ toolCallId: 'summary', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'summary', workflowVersion: '1.0.0', arguments: { axis: 'columns' } }],
  }, failure);
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '编辑参数' }));
  const editor = await screen.findByTestId('confirmation-editor');
  fireEvent.change(within(editor).getByRole('textbox'), { target: { value: 'flatten' } });
  fireEvent.click(within(editor).getByRole('button', { name: '保存为新修订' }));
  if (failure === '502 before commit') {
    const retry = await screen.findByRole('button', { name: '重试同一操作' });
    expect(revisions).toHaveLength(1);
    expect(within(editor).getByRole('button', { name: '保存为新修订' })).toBeDisabled();
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);
  }
  expect(await screen.findByRole('button', { name: '确认执行修订 2' })).toBeEnabled();
  expect(revisions).toHaveLength(failure === '502 before commit' ? 2 : 1);
  if (revisions.length === 2) expect(revisions[1]).toEqual(revisions[0]);
  expect(screen.queryByText('操作结果待核对')).toBeNull();
  await waitFor(() => expect(document.getElementById('current-confirmation')).toHaveFocus());
});
