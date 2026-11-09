import type { History, TaskAction, TaskContext } from './types';
import { errorCodeMessage } from './api';

const taskActions: Record<TaskAction, string> = {
  NONE: '', CREATE: '任务已建立', REVISE: '任务要求已更新',
  CONTINUE: '任务继续推进', REVIEW: '任务进入评审',
};

export function TaskUpdateCard({ history, taskAction, onOpen }: {
  history: History; taskAction?: TaskAction | null; onOpen: () => void;
}) {
  const task = history.task;
  const turn = history.turns.find(item => item.goalRevision === history.goalRevision);
  const unresolved = task?.spec.unresolvedItems[0]
    ?? turn?.reply?.unresolvedItems[0]?.markdown
    ?? null;
  if (!task && !unresolved) return null;
  const status = unresolved ? '需要补充信息'
    : task?.progress.state === 'WAITING_REVIEW' ? '等待业务评审'
    : task?.progress.state === 'WAITING_CONFIRMATION' ? '等待确认'
    : task?.progress.lifecycle === 'COMPLETE' ? '任务已完成'
    : '任务进行中';
  return <section className="task-update-card" aria-label="任务更新">
    <div><small>任务更新</small><strong>{status}</strong></div>
    {task?.spec.objective && <p>{task.spec.objective}</p>}
    {taskAction && taskAction !== 'NONE' && <small>{taskActions[taskAction]}</small>}
    {unresolved && <p className="task-update-next"><b>下一步：</b>{unresolved}</p>}
    <button onClick={onOpen}>在任务工作台查看</button>
  </section>;
}

/** Full task facts are rendered by WorkspacePanel; retained for isolated consumers. */
export function TaskCard({ task, disabled, onReview, onResource }: { task: TaskContext; taskAction?: TaskAction | null; disabled: boolean; onReview: (text: string) => void; onResource: (id: string) => void }) {
  return <section className="task-card" aria-label="主任务"><strong>{task.spec.objective}</strong><ul>{task.spec.criteria.map(item => { const progress = task.progress.criteria.find(value => value.id === item.id); return <li key={item.id}>{progress?.satisfied ? '已满足' : '待完成'} · {item.text}{item.interpretation && <small>智能体解释：{item.interpretation}</small>}{item.kind === 'USER_REVIEW' && !progress?.satisfied && <button disabled={disabled} onClick={() => onReview(`确认完成：${item.text}`)}>确认此项满足</button>}</li>; })}</ul>{task.progress.blockingReasons?.map(code => <p key={code}>{errorCodeMessage(code)}</p>)}{task.progress.results.map(item => <button key={item.versionId} onClick={() => onResource(item.versionId)}>{item.outputName ?? '查看成果'}</button>)}</section>;
}
