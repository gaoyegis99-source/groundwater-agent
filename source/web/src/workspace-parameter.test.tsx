import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { capabilitiesFixture, historyFixture, sessionResultsFixture } from './test-fixtures';
import type { Confirmation } from './types';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/#/sessions/parameter-input');
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(max-width: 1050px)', addEventListener: vi.fn(), removeEventListener: vi.fn() })));
});

function mockPreparation(confirmation?: Confirmation) {
  const history = historyFixture({
    sessionId: 'parameter-input', turns: [{ confirmations: confirmation ? [confirmation] : [] }],
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
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/capabilities')) return json(capabilities);
    if (url.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (url.includes('/results')) return json(sessionResultsFixture());
    if (url.endsWith('/messages')) return json(history);
    throw new Error(`unexpected endpoint ${url}`);
  }));
}

it('preserves the draft and returns focus to the composer when compact parameter entry needs a new message', async () => {
  mockPreparation();
  render(<App />);
  await screen.findByTestId('recovered-active-session');
  const composer = screen.getByRole('textbox', { name: '消息输入框' });
  fireEvent.change(composer, { target: { value: '保留原来的分析条件' } });
  const open = await screen.findByRole('button', { name: '打开任务工作台' });
  open.focus(); fireEvent.click(open);
  const panel = screen.getByRole('dialog', { name: '任务工作台' });
  fireEvent.click(within(panel).getByRole('tab', { name: '任务' }));
  fireEvent.click(within(panel).getByRole('button', { name: '补充参数' }));
  expect(composer).toHaveValue('保留原来的分析条件\n补充参数 axis：');
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '任务工作台' })).not.toBeInTheDocument());
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
  const panel = screen.getByRole('dialog', { name: '任务工作台' });
  fireEvent.click(within(panel).getByRole('tab', { name: '任务' }));
  fireEvent.click(within(panel).getByRole('button', { name: '补充参数' }));
  expect(await within(panel).findByTestId('confirmation-editor')).toBeVisible();
  expect(screen.getByRole('dialog', { name: '任务工作台' })).toBeVisible();
  expect(within(panel).getByRole('tab', { name: /参数/ })).toHaveAttribute('aria-selected', 'true');
  expect(composer).toHaveValue('原有草稿');
});
