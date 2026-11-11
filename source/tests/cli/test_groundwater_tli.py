import io
import hashlib
import json

import pytest
from platform_protocol_contracts import ApiProblem
from platform_protocol_contracts.problem_v11 import build_problem_details
from pydantic import ValidationError

from groundwater_api_client import models
from groundwater_api_client.tli import Terminal, main


class FakeClient:
    def __init__(self):
        self.calls = []
        self.inputs = []

    def create_session(self, request):
        self.calls.append(('new', request.project_id))
        return {'sessionId': 'shared-session'}

    def list_sessions(self, **kwargs):
        self.calls.append(('sessions', kwargs))
        return {'items': [{'sessionId': 'shared-session'}]}

    def session(self, session_id):
        self.calls.append(('session', session_id))
        return {'sessionId': session_id, 'confirmation': {'confirmationId': 'frozen-1', 'actions': [{'arguments': {'alpha': 0.05}}]}}

    def history(self, session_id, **kwargs):
        self.calls.append(('history', session_id, kwargs))
        return {'items': [{'content': '以前的 Web 对话'}]}

    def send_message(self, session_id, request):
        self.calls.append(('send', session_id, request))
        return {'requestId': request.request_id}

    def confirm(self, session_id, confirmation_id):
        self.calls.append(('confirm', session_id, confirmation_id))
        return {'status': 'accepted'}

    def reject(self, session_id, confirmation_id, reason):
        self.calls.append(('reject', session_id, confirmation_id, reason))
        return {'status': 'rejected'}

    def events(self, session_id, **kwargs):
        self.calls.append(('events', session_id, kwargs))
        return iter([b'id: 41\nevent: workflow.running\ndata: ', '{"title":"执行中"}'.encode()[:12], '{"title":"执行中"}'.encode()[12:] + b'\n\n'])

    def list_datasets(self, **kwargs):
        self.calls.append(('datasets', kwargs))
        return {'items': [], 'nextCursor': 'next'}

    def input_reference(self, version_id, object_id=None):
        self.calls.append(('reference', version_id, object_id))
        return models.KernelMessageInputReference(datasetId='dataset', versionId=version_id, semanticId='sem', objectId=object_id or 'raw', checksum='sha256:' + hashlib.sha256(b'abcdef').hexdigest(), format='csv', mediaType='text/csv', sizeBytes=6)

    def results(self, session_id, **kwargs):
        self.calls.append(('results', session_id, kwargs))
        return {'items': []}

    def download_object(self, version_id, object_id):
        self.calls.append(('download', version_id, object_id))
        return iter([b'abc', b'def'])


def terminal():
    client = FakeClient()
    output = io.StringIO()
    return Terminal(client, project='groundwater-demo', output=output), client, output


def test_new_attach_send_and_explicit_request_identity():
    term, client, output = terminal()
    term.execute('new')
    term.execute('attach exact-version exact-object')
    term.execute('send --request-id request-123 请分析地下水趋势')
    request = client.calls[-1][2]
    assert client.calls[-1][:2] == ('send', 'shared-session')
    assert request.request_id == 'request-123'
    assert request.message == '请分析地下水趋势'
    assert request.input_references[0].version_id == 'exact-version'
    assert request.input_references[0].object_id == 'exact-object'
    assert 'shared-session' in output.getvalue()


def test_resume_web_session_show_history_frozen_confirmation_and_decide():
    term, client, output = terminal()
    term.execute('use web-session')
    term.execute('history --cursor 27')
    term.execute('status')
    assert 'frozen-1' in output.getvalue() and '0.05' in output.getvalue()
    term.execute('confirm frozen-1')
    term.execute('reject frozen-1 参数错误')
    assert ('history', 'web-session', {'cursor': 27, 'limit': 100}) in client.calls
    assert ('confirm', 'web-session', 'frozen-1') in client.calls
    assert ('reject', 'web-session', 'frozen-1', '参数错误') in client.calls


def test_stream_reassembles_utf8_and_resumes_at_last_event():
    term, client, output = terminal()
    term.execute('use existing')
    term.execute('events --cursor 7')
    assert '执行中' in output.getvalue()
    term.execute('events')
    assert ('events', 'existing', {'cursor': 7}) in client.calls
    assert ('events', 'existing', {'cursor': 41}) in client.calls


def test_datasets_results_and_download_exclusive(tmp_path):
    term, client, output = terminal()
    term.execute('use existing')
    term.execute('datasets --query "观测井" --cursor next')
    term.execute('results --cursor 20')
    target = tmp_path / 'result.bin'
    term.execute(f'download version object {target}')
    assert target.read_bytes() == b'abcdef'
    with pytest.raises(FileExistsError):
        term.execute(f'download version object {target}')
    assert target.read_bytes() == b'abcdef'
    assert ('datasets', {'query': '观测井', 'cursor': 'next', 'limit': 20}) in client.calls
    assert ('results', 'existing', {'cursor': 20}) in client.calls


def test_no_implicit_session_creation_or_confirm():
    term, client, _output = terminal()
    with pytest.raises(ValueError):
        term.execute('send 测试')
    with pytest.raises(ValueError):
        term.execute('confirm')
    assert client.calls == []


def test_main_uses_token_env_without_printing_secret(monkeypatch, capsys):
    created = []
    class Factory(FakeClient):
        def __init__(self, origin, *, client_type, api_token):
            super().__init__()
            created.append((origin, client_type, api_token))
        def close(self): pass
    monkeypatch.setattr('groundwater_api_client.tli.GroundwaterClient', Factory)
    monkeypatch.setenv('GROUNDWATER_API_TOKEN', 'secret-token')
    assert main(['--origin', 'http://127.0.0.1:28186', 'sessions']) == 0
    assert created == [('http://127.0.0.1:28186', 'cli', 'secret-token')]
    assert 'secret-token' not in capsys.readouterr().out


def test_problem_reports_code_and_request_id_without_detail():
    term, client, output = terminal()
    def fail():
        raise ApiProblem(build_problem_details('AUTH_REQUIRED', 401, 'secret detail', request_id='req-123'), {})
    client.capabilities = fail
    assert term.run_line('/capabilities') is True
    assert 'AUTH_REQUIRED' in output.getvalue()
    assert 'req-123' in output.getvalue()
    assert 'secret detail' not in output.getvalue()


def test_failed_send_keeps_exact_attachment_for_retry():
    term, client, _output = terminal()
    term.execute('use existing')
    term.execute('attach version object')
    original = client.send_message
    def fail(*_args):
        raise OSError('network failure')
    client.send_message = fail
    with pytest.raises(OSError):
        term.execute('send --request-id same-id 示例请求')
    client.send_message = original
    term.execute('send --request-id same-id 示例请求')
    request = client.calls[-1][2]
    assert request.request_id == 'same-id' and request.input_references[0].version_id == 'version'
    assert term.references == []


def test_switching_sessions_clears_pending_attachment():
    term, client, _output = terminal()
    term.execute('use first')
    term.execute('attach first-version')
    term.execute('use second')
    term.execute('send 只发当前问题')
    assert client.calls[-1][1] == 'second'
    assert not client.calls[-1][2].input_references


def test_failed_download_does_not_leave_partial_file(tmp_path):
    term, client, _output = terminal()
    def broken(_version, _object):
        yield b'partial'
        raise OSError('network interrupted')
    client.download_object = broken
    target = tmp_path / 'result.bin'
    with pytest.raises(OSError):
        term.execute(f'download version object {target}')
    assert not target.exists()


def test_username_login_reads_password_without_printing(monkeypatch, capsys):
    logins = []
    class Factory(FakeClient):
        def __init__(self, origin, *, client_type, api_token):
            self.api_token = api_token
        def login(self, username, password):
            logins.append((username, password, self.api_token))
        def list_sessions(self, **_kwargs):
            return {'items': []}
        def close(self): pass
    monkeypatch.setattr('groundwater_api_client.tli.GroundwaterClient', Factory)
    monkeypatch.setattr('groundwater_api_client.tli.getpass.getpass', lambda _prompt: 'not-for-output')
    monkeypatch.setenv('GROUNDWATER_API_TOKEN', 'other-token')
    assert main(['--username', 'reader', 'sessions']) == 0
    assert logins == [('reader', 'not-for-output', None)]
    assert 'not-for-output' not in (capsys.readouterr().out)


def test_one_shot_session_status_is_one_json_document(monkeypatch, capsys):
    class Factory(FakeClient):
        def __init__(self, origin, *, client_type, api_token):
            super().__init__()
        def close(self):
            pass
    monkeypatch.setattr('groundwater_api_client.tli.GroundwaterClient', Factory)
    assert main(['--session', 'web-session', 'status']) == 0
    assert json.loads(capsys.readouterr().out)['sessionId'] == 'web-session'


def test_download_checksum_mismatch_does_not_publish(tmp_path):
    term, client, _output = terminal()
    client.download_object = lambda _version, _object: iter([b'xxxxxx'])
    target = tmp_path / 'result.bin'
    with pytest.raises(ValueError, match='CHECKSUM'):
        term.execute(f'download version object {target}')
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_unexpected_programming_failure_propagates():
    term, client, _output = terminal()
    def fail():
        raise RuntimeError('implementation bug')
    client.capabilities = fail
    with pytest.raises(RuntimeError, match='implementation bug'):
        term.run_line('/capabilities')


def test_send_preserves_natural_text_spacing():
    term, client, _output = terminal()
    term.execute('use existing')
    assert term.run_line('/send --request-id req 文本  保留  空格')
    assert client.calls[-1][2].message == '文本  保留  空格'


def test_validation_error_never_prints_input_value():
    term, client, output = terminal()
    def fail():
        raise ValidationError.from_exception_data(
            'Credentials', [{'type': 'missing', 'loc': ('password',), 'input': 'secret-password'}]
        )
    client.capabilities = fail
    term.run_line('/capabilities')
    assert '参数验证失败' in output.getvalue()
    assert 'secret-password' not in output.getvalue()


def test_download_size_mismatch_does_not_publish(tmp_path):
    term, client, _output = terminal()
    client.download_object = lambda _version, _object: iter([b'short'])
    target = tmp_path / 'result.bin'
    with pytest.raises(ValueError, match='SIZE'):
        term.execute(f'download version object {target}')
    assert list(tmp_path.iterdir()) == []
