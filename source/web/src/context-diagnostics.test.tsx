import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ActivityPanel } from './ActivityPanel';
import { activityFixture, instant, turnFixture } from './test-fixtures';
import type { ContextUsage } from './ContextUsageDetails';

it('explains a blocked request without exposing internal capacity accounting', () => {
  const usage: ContextUsage = {
    tokenizer: 'o200k_base', totalTokens: 1200, availableInputTokens: 1000, excessTokens: 200,
    breakdown: { tools: 100, instructions: 100, dialogue: 50, executionReferences: 40, observations: 900, serialization: 10 },
  };
  const turn = turnFixture({ status: 'FAILED', errorCode: 'AGENT_CONTEXT_TOO_LARGE',
    activities: [activityFixture({ kind: 'SEGMENT', purpose: 'INITIAL', status: 'FAILED', toolName: null,
      contextUsage: usage, errorCode: 'AGENT_CONTEXT_TOO_LARGE', finishedAt: instant })] });
  render(<ActivityPanel turn={turn} snapshotAt={instant} connected />);
  expect(screen.getByRole('button', { name: /^处理未完成/ })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('本轮需要处理的信息过多。请缩小资料范围，或在新对话中分步分析。')).toBeVisible();
  expect(screen.queryByText(/技术信息/)).not.toBeInTheDocument();
  expect(screen.queryByText(/上下文占用/)).not.toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});
