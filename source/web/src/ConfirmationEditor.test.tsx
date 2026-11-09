import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ConfirmationEditor } from './ConfirmationEditor';
import type { Capabilities, Confirmation } from './types';

it('edits the complete action and submits a new immutable revision payload', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], nextCursor: null, matchBasis: 'catalog' }), { status: 200, headers: { 'content-type': 'application/json' } })));
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-1', goalRevision: 4, status: 'PENDING',
    agentRunId: 'agent-1', reason: '', decision: null, decisionState: null,
    revision: 1, kind: 'INITIAL', actions: [{
      toolCallId: 'call-1', tool: 'run_summary', modelId: 'groundwater.summary',
      modelVersion: '1.1.0', workflowId: 'groundwater.summary', workflowVersion: '1.0.0',
      arguments: { resourceId: 'resource-1', threshold: 2 },
    }],
  };
  const capabilities: Capabilities = { productId: 'groundwater', projectId: 'p', currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] }, models: [{
    modelId: 'groundwater.summary', description: 'summary', tool: 'run_summary',
    dataInputParameters: ['resource_id'], inputSchema: {
      type: 'object', required: ['resource_id', 'threshold'], properties: {
        resourceId: { type: 'string' }, threshold: { type: 'number', minimum: 0, maximum: 10 },
      },
    },
  }] };
  const save = vi.fn();
  render(<ConfirmationEditor confirmation={confirmation} capabilities={capabilities} project="p" busy={false} onClose={vi.fn()} onSave={save} />);
  await userEvent.clear(screen.getByRole('spinbutton'));
  await userEvent.type(screen.getByRole('spinbutton'), '5');
  expect(screen.getByText('字段变化 · 1')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '保存为新修订' }));
  expect(save).toHaveBeenCalledWith([{ toolCallId: 'call-1', arguments: {
    resourceId: 'resource-1', threshold: 5,
  } }]);
});

it('applies and focuses an explicitly requested field without caller DOM lookup', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], nextCursor: null, matchBasis: 'catalog' }), { status: 200, headers: { 'content-type': 'application/json' } })));
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-focus', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null, revision: 1, kind: 'INITIAL',
    actions: [{ toolCallId: 'call-1', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '3.0.0', workflowId: 'groundwater.summary', workflowVersion: '1.0.0', arguments: { versionId: 'old-version' } }],
  };
  const capabilities: Capabilities = { productId: 'groundwater', projectId: 'p', currentUser: { subject: 'u', displayName: '用户', roles: ['researcher'] }, models: [{
    modelId: 'groundwater.summary', description: 'summary', tool: 'run_summary', inputSchema: {
      type: 'object', required: ['versionId'], properties: { versionId: { type: 'string' } },
    },
  }] };
  render(<ConfirmationEditor confirmation={confirmation} capabilities={capabilities} project="p" busy={false} focusField="versionId" suggestedValue="current-version" onClose={vi.fn()} onSave={vi.fn()} />);
  const input = screen.getByRole('textbox');
  await waitFor(() => expect(input).toHaveValue('current-version'));
  await waitFor(() => expect(input).toHaveFocus());
});
