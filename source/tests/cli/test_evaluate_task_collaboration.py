from __future__ import annotations

import httpx
import pytest

from scripts.evaluation import evaluate_task_collaboration as evaluation


@pytest.mark.parametrize('confirmations,expected', [([], 'MECHANICAL_PASS_REVIEW_REQUIRED'), ([{'status': 'PENDING'}], 'FAILED')])
def test_quality_evaluation_never_promotes_mechanical_success_to_business_pass(confirmations, expected):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith('/capabilities'):
            payload = {'projectId': 'p'}
        elif path.endswith('/sessions'):
            payload = {'session_id': 's'}
        elif path.endswith('/messages'):
            payload = {'agent_run_id': 'r'} if request.method == 'POST' else {
                'turns': [{'agent_run_id': 'r', 'status': 'COMPLETE', 'confirmations': confirmations}]}
        elif path.endswith('/results'):
            payload = {'items': []}
        elif path.endswith('/task'):
            payload = {'task': None}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=payload)
    cases = [{'id': 'capability', 'steps': [{'message': '只比较方法', 'expect': 'NO_EXECUTION'}], 'review': '核对适用范围'}]
    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
        result = evaluation.run_quality_suite('http://test', client=client, cases=cases)
    assert result['status'] == expected
    assert result['cases'][0]['business_review']['status'] == 'REQUIRED'
    assert result['cases'][0]['turns'] == ['r']
    assert all('confirmation' not in path for _, path in calls)
    assert '只比较方法' not in str(result)
