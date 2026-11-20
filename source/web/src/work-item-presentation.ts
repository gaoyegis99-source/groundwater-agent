import { ApiError } from './api';
import type { WorkItemLifecycleSnapshot } from './types';

export const workItemStateLabel: Record<WorkItemLifecycleSnapshot['state'], string> = {
  DRAFT: '草稿',
  READY: '准备就绪',
  WAITING_INPUT: '待补充资料',
  WAITING_REVIEW: '待审核',
  WAITING_CONFIRMATION: '待确认执行',
  EXECUTING: '正在执行',
  RESULT_REVIEW: '待验收成果',
  BLOCKED: '处理受阻',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  ARCHIVED: '已归档',
};

const nextSteps: Record<string, string> = {
  TASK_INPUT_REQUIRED: '在关联会话中补齐任务资料或参数',
  PREPARATION_INPUT_REQUIRED: '在关联会话中补齐任务资料或参数',
  PREPARATION_BLOCKED: '在关联会话中查看准备缺项',
  CONFIRMATION_PENDING: '在关联会话中核对执行方案',
  CONFIRMATION_DECISION_PROCESSING: '正在处理执行确认',
  TASK_REVIEW_REQUIRED: '在关联会话中审核任务',
  DELIVERY_REVIEW_REQUIRED: '核对并验收交付',
  WORKFLOW_FAILED: '在关联会话中查看执行失败原因',
  WORKFLOW_TIMED_OUT: '在关联会话中查看超时详情',
  RESULT_LINK_REQUIRED: '交付仍缺少关联成果',
  RESULT_ACCEPTANCE_REQUIRED: '核对并验收已发布成果',
  TASK_DELIVERY_EVIDENCE_REQUIRED: '在关联会话中核对交付依据',
  RESULT_UNAVAILABLE: '部分交付成果当前不可读',
  PUBLICATION_PENDING: '等待成果发布',
  PUBLICATION_FAILED: '成果发布失败',
  PUBLICATION_UNVERIFIED: '成果发布状态待核验',
  STOP_REQUESTED: '正在处理停止请求',
};

export function workItemNextStep(reason: string) {
  return Object.hasOwn(nextSteps, reason) ? nextSteps[reason] : undefined;
}

const formatTime = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });

export function workItemTime(timestamp: string) {
  const value = new Date(timestamp);
  return Number.isFinite(value.getTime()) ? formatTime.format(value) : '时间未知';
}

export function workItemErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return '暂时无法读取业务工作，请重试。';
  if (error.code === 'API_REQUEST_INVALID') return '请核对填写内容后重试。';
  if (error.code === 'SESSION_ALREADY_LINKED') return '该会话已归属其他业务工作。可选择另一会话，或为当前工作新建会话。';
  if (error.code === 'WORK_ITEM_ARCHIVED') return '这项工作已归档，关联会话仍可读取。';
  switch (error.status) {
    case 0: return '连接中断，请重试。';
    case 401: return '登录状态已失效，请重新登录。';
    case 403: return '当前身份无权访问或修改这项工作。';
    case 404: return '这项工作不存在或当前身份无法访问。';
    case 409: return '工作已更新，请核对最新状态后再操作。';
    case 422: return '请核对填写内容后重试。';
    case 429: return error.retryAfter ? `操作过于频繁，请等待 ${error.retryAfter} 秒后重试。` : '操作过于频繁，请稍后重试。';
    default: return error.status >= 500 ? '服务暂时不可用，请稍后重试。' : '暂时无法核实业务工作，请重试。';
  }
}
