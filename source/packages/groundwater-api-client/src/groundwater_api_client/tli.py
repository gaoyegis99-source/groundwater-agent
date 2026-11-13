"""Text interface to the public Groundwater SDK and Gateway."""

from __future__ import annotations

import argparse
import codecs
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
from typing import Any, TextIO
from uuid import uuid4

from platform_protocol_contracts import ApiProblem, ProtocolError, TransportError
from pydantic import ValidationError

from . import GroundwaterClient, models


HELP = """命令（交互模式下以前缀 / 调用；普通文字直接发送）：
  /new [项目ID]                       新建会话
  /sessions [--query 关键词] [--cursor 游标]  查看已有会话
  /use 会话ID                         打开 Web 或其他客户端创建的会话
  /history [--cursor 数字]             查看对话历史
  /status                            查看任务和待确认的冻结参数
  /send [--request-id ID] 消息         发送消息；重试时复用同一请求 ID
  /datasets [--query 关键词] [--cursor 游标] 查找数据集
  /attach 版本ID [对象ID]              附加精确数据版本；需要时指定对象
  /attachments                       查看已附加对象
  /clear                             清空待发送附件
  /confirm 确认ID                    显式批准该批次
  /reject 确认ID 原因                  显式拒绝该批次
  /retry                             重试当前会话中回执未确认的消息
  /events [--cursor 事件ID] [--follow] 查看事件；--follow 自动续接至待确认或终态，Ctrl-C 停止观察
  /results [--cursor 数字]             查看成果及精确版本
  /download 版本ID 对象ID 目标路径      下载到新文件，不覆盖已有文件
  /help                              显示帮助
  /exit                              退出
"""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _field(value: Any, snake: str, camel: str) -> Any:
    if isinstance(value, dict):
        return value.get(camel, value.get(snake))
    return getattr(value, snake, None)


def _options(arguments: list[str], *names: str) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    options: dict[str, str] = {}
    i = 0
    while i < len(arguments):
        item = arguments[i]
        if item.startswith('--'):
            name = item[2:]
            if name not in names or name in options or i + 1 >= len(arguments):
                raise ValueError(f"参数无效: {item}")
            options[name] = arguments[i + 1]
            i += 2
        else:
            positional.append(item)
            i += 1
    return positional, options


def _integer(options: dict[str, str], name: str, default: int | None = None) -> int | None:
    value = options.get(name)
    if value is None:
        return default
    number = int(value)
    if number < 0:
        raise ValueError(f"{name} 不能为负数")
    return number


def _report_error(output: TextIO, error: Exception) -> None:
    if isinstance(error, ApiProblem):
        print(f'错误: {error.problem.code}（请求 ID: {error.problem.request_id}）', file=output, flush=True)
    elif isinstance(error, ValidationError):
        # Pydantic error text may contain passwords or other original input.
        print('错误: 参数验证失败', file=output, flush=True)
    elif isinstance(error, (ValueError, OSError)):
        print(f'错误: {error}', file=output, flush=True)
    else:
        print(f'错误: {type(error).__name__}', file=output, flush=True)


class Terminal:
    """One stateful presentation over the same public Gateway as the Web UI."""

    def __init__(self, client: Any, *, project: str, output: TextIO | None = None) -> None:
        self.client = client
        self.project = project
        self.output = output if output is not None else sys.stdout
        self.session_id: str | None = None
        self.references: list[Any] = []
        self.event_cursors: dict[str, int] = {}
        self.pending_sends: dict[str, models.KernelMessageCreate] = {}
        self.interactive = False

    def _print(self, value: Any) -> None:
        print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, default=str), file=self.output, flush=True)

    def _session(self) -> str:
        if not self.session_id:
            raise ValueError('请先使用 /new 或 /use 选择会话')
        return self.session_id

    def execute(self, line: str) -> bool:
        return self.execute_tokens(shlex.split(line))

    def execute_tokens(self, args: list[str]) -> bool:
        if not args:
            return True
        command, rest = args[0].removeprefix('/'), args[1:]
        if command in {'exit', 'quit'}:
            if rest:
                raise ValueError('exit 不接受参数')
            return False
        if command == 'help':
            print(HELP, file=self.output, flush=True)
        elif command == 'capabilities':
            self._print(self.client.capabilities())
        elif command == 'new':
            if len(rest) > 1:
                raise ValueError('用法: new [项目ID]')
            result = self.client.create_session(models.KernelSessionCreate(projectId=rest[0] if rest else self.project))
            session = _field(result, 'session_id', 'sessionId')
            if not isinstance(session, str) or not session:
                raise ValueError('服务未返回会话 ID')
            self.session_id = session
            self.references.clear()
            self._print(result)
        elif command == 'use':
            if len(rest) != 1:
                raise ValueError('用法: use 会话ID')
            result = self.client.session(rest[0])
            self.session_id = rest[0]
            self.references.clear()
            self._print(result)
        elif command == 'sessions':
            positional, opts = _options(rest, 'query', 'cursor', 'limit')
            if positional:
                raise ValueError('用法: sessions [--query 关键词] [--cursor 数字]')
            self._print(self.client.list_sessions(cursor=_integer(opts, 'cursor'), limit=_integer(opts, 'limit', 20), query=opts.get('query')))
        elif command == 'history':
            positional, opts = _options(rest, 'cursor', 'limit')
            if positional:
                raise ValueError('用法: history [--cursor 数字]')
            self._print(self.client.history(self._session(), cursor=_integer(opts, 'cursor'), limit=_integer(opts, 'limit', 100)))
        elif command == 'status':
            if rest:
                raise ValueError('status 不接受参数')
            self._print(self.client.session(self._session()))
        elif command == 'send':
            if rest and rest[0] == '--request-id':
                if len(rest) < 3 or not rest[1]:
                    raise ValueError('用法: send [--request-id ID] 消息')
                opts = {'request-id': rest[1]}
                positional = rest[2:]
            else:
                opts = {}
                positional = rest
            if not positional:
                raise ValueError('用法: send [--request-id ID] 消息')
            request = models.KernelMessageCreate(requestId=opts.get('request-id') or str(uuid4()), message=' '.join(positional), requestedMode='AUTO', inputReferences=self.references or None)
            self._send(self._session(), request)
        elif command == 'retry':
            if rest or not self.interactive:
                raise ValueError('用法: 在交互模式输入 /retry')
            session = self._session()
            request = self.pending_sends.get(session)
            if request is None:
                raise ValueError('当前会话没有回执待核对的消息')
            self._send(session, request)
        elif command == 'datasets':
            positional, opts = _options(rest, 'query', 'cursor', 'limit')
            if positional:
                raise ValueError('用法: datasets [--query 关键词] [--cursor 游标]')
            self._print(self.client.list_datasets(query=opts.get('query'), cursor=opts.get('cursor'), limit=_integer(opts, 'limit', 20)))
        elif command == 'attach':
            if len(rest) not in (1, 2):
                raise ValueError('用法: attach 版本ID [对象ID]')
            reference = self.client.input_reference(rest[0], rest[1] if len(rest) == 2 else None)
            if isinstance(reference, dict):
                reference = models.KernelMessageInputReference.model_validate(reference)
            if not isinstance(reference, models.KernelMessageInputReference):
                raise ValueError('服务返回的输入引用无效')
            self.references = [item for item in self.references if (item.version_id, item.object_id) != (reference.version_id, reference.object_id)]
            self.references.append(reference)
            self._print(reference)
        elif command == 'attachments':
            self._print([_jsonable(item) for item in self.references])
        elif command == 'clear':
            self.references.clear()
            print('已清空待发送附件。', file=self.output, flush=True)
        elif command in {'confirm', 'reject'}:
            if (command == 'confirm' and len(rest) != 1) or (command == 'reject' and len(rest) < 2):
                raise ValueError('用法: confirm 确认ID 或 reject 确认ID 原因')
            session = self._session()
            result = self.client.confirm(session, rest[0]) if command == 'confirm' else self.client.reject(session, rest[0], ' '.join(rest[1:]))
            self._print(result)
        elif command == 'events':
            follow = '--follow' in rest
            if rest.count('--follow') > 1:
                raise ValueError('参数无效: --follow')
            positional, opts = _options([arg for arg in rest if arg != '--follow'], 'cursor')
            if positional:
                raise ValueError('用法: events [--cursor 事件ID] [--follow]')
            session = self._session()
            cursor = _integer(opts, 'cursor', self.event_cursors.get(session))
            self._events(session, cursor, follow=follow)
        elif command == 'results':
            positional, opts = _options(rest, 'cursor')
            if positional:
                raise ValueError('用法: results [--cursor 数字]')
            self._print(self.client.results(self._session(), cursor=_integer(opts, 'cursor', 0)))
        elif command == 'download':
            if len(rest) != 3:
                raise ValueError('用法: download 版本ID 对象ID 目标路径')
            path = Path(rest[2]).expanduser()
            if path.exists() or path.is_symlink():
                raise FileExistsError(f'目标文件已存在: {path}')
            reference = self.client.input_reference(rest[0], rest[1])
            if isinstance(reference, dict):
                reference = models.KernelMessageInputReference.model_validate(reference)
            if not isinstance(reference, models.KernelMessageInputReference) or reference.version_id != rest[0] or reference.object_id != rest[1]:
                raise ValueError('DOWNLOAD_IDENTITY_MISMATCH')
            temporary: Path | None = None
            try:
                digest = hashlib.sha256()
                size = 0
                with tempfile.NamedTemporaryFile(prefix='.groundwater-download-', dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    chunks = self.client.download_object(rest[0], rest[1])
                    try:
                        for chunk in chunks:
                            stream.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    finally:
                        close = getattr(chunks, 'close', None)
                        if close is not None:
                            close()
                if size != reference.size_bytes:
                    raise ValueError('DOWNLOAD_SIZE_MISMATCH')
                if 'sha256:' + digest.hexdigest() != reference.checksum:
                    raise ValueError('DOWNLOAD_CHECKSUM_MISMATCH')
                # Hard-link publication is atomic and fails if a target appeared.
                os.link(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            print(f'已保存: {path}', file=self.output, flush=True)
        else:
            raise ValueError(f'未知命令: {command}；输入 /help 查看帮助')
        return True

    def _send(self, session: str, request: models.KernelMessageCreate) -> None:
        print(f'请求 ID: {request.request_id}', file=self.output, flush=True)
        try:
            result = self.client.send_message(session, request)
        except (TransportError, ProtocolError):
            if self.interactive:
                self.pending_sends[session] = request
                print('消息回执待核对，可输入 /retry 重试同一请求。', file=self.output, flush=True)
            raise
        except ApiProblem as error:
            if self.interactive and (error.problem.status >= 500 or error.problem.status == 429):
                self.pending_sends[session] = request
            elif self.pending_sends.get(session) is request:
                self.pending_sends.pop(session)
            raise
        self.pending_sends.pop(session, None)
        if self.references == list(request.input_references or ()):
            self.references.clear()
        self._print(result)
        if self.interactive:
            print('已受理 · 输入 /events --follow 查看进度。', file=self.output, flush=True)

    def _events(self, session: str, cursor: int | None, *, follow: bool = False) -> None:
        delay = 0.25
        last_phase = "尚未读取"
        reconnecting = False
        if follow and cursor is not None:
            self.event_cursors[session] = cursor
        while True:
            before = cursor
            try:
                if follow:
                    history = _jsonable(self.client.history(session, cursor=None, limit=100))
                    turn = next((item for item in reversed(history.get('turns', []))
                                 if item.get('goalRevision') == history.get('goalRevision')), None)
                    if turn:
                        last_phase = {
                            'ROUTING': '正在判断需求', 'QUEUED': '排队', 'RUNNING': '正在分析',
                            'WAITING_CONFIRMATION': '待确认', 'WAITING_EXECUTION': '正在执行',
                            'COMPLETE': '本轮已完成', 'FAILED': '本轮未完成', 'CANCELLED': '本轮已停止',
                        }.get(turn.get('status'), '已同步会话')
                    snapshot_cursor = history.get('eventCursor')
                    if type(snapshot_cursor) is int:
                        if cursor is None:
                            cursor = snapshot_cursor
                        if cursor >= snapshot_cursor:
                            phase = self._settled_phase(history)
                            if phase:
                                if self.interactive:
                                    print(phase, file=self.output, flush=True)
                                return
                terminal, cursor = self._event_stream(session, cursor, follow=follow)
                reconnecting = False
                if terminal or not follow:
                    return
            except TransportError:
                if not follow:
                    raise
                if self.interactive and not reconnecting:
                    print(f'连接恢复中 · 最近核对状态：{last_phase}', file=self.output, flush=True)
                reconnecting = True
                cursor = self.event_cursors.get(session, cursor)
            time.sleep(delay)
            delay = 0.25 if cursor != before else min(delay * 2, 5.0)

    def _event_stream(self, session: str, cursor: int | None, *, follow: bool) -> tuple[bool, int | None]:
        decoder = codecs.getincrementaldecoder('utf-8')()
        buffer = ''
        stream = self.client.events(session, cursor=cursor)
        try:
            for chunk in stream:
                buffer += decoder.decode(chunk)
                buffer = buffer.replace('\r\n', '\n')
                while '\n\n' in buffer:
                    event, buffer = buffer.split('\n\n', 1)
                    if not event or all(line.startswith(':') for line in event.splitlines()):
                        continue
                    event_id = None
                    event_type = ''
                    data_lines = []
                    for line in event.splitlines():
                        if line.startswith('id:'):
                            value = line[3:].strip()
                            if value.isdecimal():
                                event_id = int(value)
                        elif line.startswith('event:'):
                            event_type = line[6:].strip()
                        elif line.startswith('data:'):
                            data_lines.append(line[5:].lstrip(' '))
                    if follow and event_id is not None and cursor is not None and event_id <= cursor:
                        continue
                    stop = follow and self._current_terminal(session, event_type, data_lines)
                    if event_id is not None:
                        cursor = event_id
                        self.event_cursors[session] = cursor
                    print(event, file=self.output, flush=True)
                    if stop:
                        if self.interactive:
                            if event_type in {'agent.waiting_confirmation', 'confirmation.recovery_created'}:
                                print('待确认 · 输入 /status 核对参数。', file=self.output, flush=True)
                            else:
                                print('本轮等待结束 · 输入 /status 查看状态，或 /results 查看成果。', file=self.output, flush=True)
                        return True, cursor
        finally:
            close = getattr(stream, 'close', None)
            if close is not None:
                close()
        return False, cursor

    @staticmethod
    def _settled_phase(history: dict[str, Any]) -> str | None:
        revision = history.get('goalRevision')
        turn = next((item for item in reversed(history.get('turns', [])) if item.get('goalRevision') == revision), None)
        if turn is None:
            return None
        confirmations = turn.get('confirmations', [])
        latest = confirmations[-1] if confirmations else None
        if any(task.get('isLatestAttempt', True) and task.get('status') in {'QUEUED', 'RUNNING'}
               for task in turn.get('executionTasks', [])):
            return None
        if latest and latest.get('status') == 'PENDING':
            if latest.get('decisionState'):
                return None
            return f"待确认 · 修订 {latest.get('revision', 1)} · 输入 /status 核对参数"
        return {
            'COMPLETE': '本轮已完成 · 输入 /results 查看成果',
            'FAILED': '本轮未完成 · 输入 /status 查看详情',
            'CANCELLED': '本轮已停止 · 输入 /status 查看详情',
            'SUPERSEDED': '本轮已被替代 · 输入 /status 查看当前状态',
        }.get(turn.get('status'))

    def _current_terminal(self, session: str, event_type: str, data_lines: list[str]) -> bool:
        statuses = {
            'agent.waiting_confirmation': 'WAITING_CONFIRMATION', 'agent.complete': 'COMPLETE',
            'agent.failed': 'FAILED', 'agent.cancelled': 'CANCELLED', 'agent.superseded': 'SUPERSEDED',
        }
        recovery = {'confirmation.recovery_created', 'workflow.recovery_completed', 'workflow.recovery_failed'}
        if event_type not in statuses and event_type not in recovery:
            return False
        try:
            payload = json.loads('\n'.join(data_lines))
            data = payload['data']
            if not isinstance(data, dict):
                return False
        except (ValueError, TypeError, KeyError):
            return False
        history = _jsonable(self.client.history(session, cursor=None, limit=100))
        turns = history.get('turns', [])
        revision = history.get('goalRevision')
        turn = next((item for item in reversed(turns) if item.get('goalRevision') == revision), None)
        if turn is None:
            return False
        confirmations = turn.get('confirmations', [])
        latest = confirmations[-1] if confirmations else None
        tasks = turn.get('executionTasks', [])
        active = any(task.get('isLatestAttempt', True) and task.get('status') in {'QUEUED', 'RUNNING'} for task in tasks)
        if event_type in recovery:
            if not latest or latest.get('confirmationId') != data.get('confirmationId'):
                return False
            if event_type == 'confirmation.recovery_created':
                return latest.get('status') == 'PENDING' and not latest.get('decisionState')
            return not active and latest.get('status') == 'CONFIRMED'
        if not data.get('agentRunId') or data.get('agentRunId') != turn.get('agentRunId') or turn.get('status') != statuses[event_type]:
            return False
        if event_type == 'agent.waiting_confirmation':
            return bool(latest and latest.get('status') == 'PENDING' and not latest.get('decisionState'))
        if active or (latest and latest.get('status') == 'PENDING'):
            return False
        return not latest or latest.get('kind') != 'RECOVERY' or data.get('recovered') is True

    def run_line(self, line: str) -> bool:
        try:
            if not line.strip():
                return True
            if line.startswith('/send '):
                text = line[len('/send '):]
                explicit = re.match(r'^--request-id\s+(\S+)\s+', text)
                return self.execute_tokens(['send', '--request-id', explicit.group(1), text[explicit.end():]] if explicit else ['send', text])
            return self.execute(line[1:]) if line.startswith('/') else self.execute_tokens(['send', line])
        except (ApiProblem, TransportError, ProtocolError, ValidationError, ValueError, OSError) as error:
            _report_error(self.output, error)
            return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='地下水终端文本交互；与 Web 共用 Gateway 和会话。')
    parser.add_argument('--origin', default='http://127.0.0.1:28186', help='Gateway 地址')
    parser.add_argument('--project', default='groundwater-demo', help='新建会话使用的项目 ID')
    parser.add_argument('--session', help='打开已有会话 ID')
    parser.add_argument('--username', help='使用账号登录；密码由安全输入提示读取')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='单次命令；省略进入交互模式')
    args = parser.parse_args(argv)
    token = None if args.username else os.environ.get('GROUNDWATER_API_TOKEN')
    client = None
    try:
        client = GroundwaterClient(args.origin, client_type='cli', api_token=token)
        terminal = Terminal(client, project=args.project)
        if args.username:
            client.login(args.username, getpass.getpass('密码: '))
        if args.session:
            terminal.session_id = args.session
        if args.command:
            terminal.execute_tokens(args.command)
            return 0
        terminal.interactive = True
        print('地下水终端。输入 /help 查看命令，普通文字发送到当前会话。', flush=True)
        while True:
            try:
                line = input('地下水> ')
                if not terminal.run_line(line):
                    return 0
            except KeyboardInterrupt:
                print('\n已中断当前终端操作。', flush=True)
            except EOFError:
                print()
                return 0
    except KeyboardInterrupt:
        return 130
    except (ApiProblem, TransportError, ProtocolError, ValidationError, ValueError, OSError) as error:
        _report_error(sys.stderr, error)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
