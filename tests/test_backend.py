from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from talentflow.api import create_app
from talentflow.domain import DomainError, execute
from talentflow.store import Store

KEY = 'test-only-secret-'*4
JOB = {'action': 'createJob', 'requestId': 'job-1', 'title': 'ML Engineer', 'department': 'Tech',
       'location': 'Remote', 'goal': 'Build a demo', 'skills': ['Python', 'PyTorch', 'MLOps'],
       'minExperience': '3', 'passScore': 70}
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'test.sqlite3')


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'http.sqlite3', KEY), base_url='http://localhost') as c:
        yield c


def call(store, action, **values):
    return store.run({'action': action, **values}, now=NOW)['result']


def ready(store):
    job = store.run(JOB, now=NOW)['result']
    call(store, 'updateJob', jobId=job['id'], status='active', confirmed=True)
    seeded = call(store, 'syncResumes')
    for c in seeded['candidates']:
        call(store, 'verifyCandidate', candidateId=c['id'], confirmed=True)
    result = call(store, 'runScreening', jobId=job['id'], limit=2, minScore=70)
    assert result['count'] == 2
    return job, result['applications'][0]


def approved(store):
    job, app = ready(store)
    return job, call(store, 'updateApplication', applicationId=app['id'], status='approved', note='Reviewed demo')


def test_full_lifecycle_persists_and_requires_human_final_decision(store):
    job, app = approved(store)
    token = app['session']['token']
    assert len(token) == 64
    assert app['invitation']['status'] == 'simulated'
    public = call(store, 'getInterview', token=token)
    assert not {'token', 'email', 'history', 'invitation'} & public.keys()
    for question in public['questions']:
        call(store, 'saveAnswer', token=token, questionId=question['id'], text='A fictional demonstration answer with supporting details')
    finished = call(store, 'finishInterview', token=token)
    assert finished['status'] == 'completed'
    assert finished['evaluation']['provider'] == 'mock'
    assert call(store, 'snapshot')['applications'][0]['status'] == 'interviewed'
    assert call(store, 'finishInterview', token=token) == finished
    hired = call(store, 'updateApplication', applicationId=app['id'], status='hired', note='Human decision')
    assert hired['status'] == 'hired'
    assert call(Store(store.path), 'recruiterReport', applicationId=app['id'])['session']['evaluation'] == finished['evaluation']


def test_creation_replays_original_response_and_rejects_changed_payload(store):
    a = store.run(JOB)
    assert store.run(dict(reversed(list(JOB.items())))) == a
    with pytest.raises(DomainError) as error:
        store.run({**JOB, 'title': 'Changed'})
    assert error.value.status == 409
    assert len(call(store, 'snapshot')['jobs']) == 1


def test_concurrent_retries_create_exactly_one_record(store):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: Store(store.path).run(JOB), range(6)))
    assert all(row == results[0] for row in results)
    assert len(call(store, 'snapshot')['jobs']) == 1


def test_receipt_and_record_rollback_together_on_storage_failure(store):
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.run(JOB)
    assert call(store, 'snapshot')['jobs'] == []
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_receipt')
    assert store.run(JOB)['ok']


def test_candidate_import_retry_and_unique_email(store):
    payload = {'action':'importCandidate','requestId':'candidate-1','name':'Demo', 'email':'DEMO@example.com',
               'skills':'Python،SQL', 'experience':'2','resumeText':'Fictional resume'}
    assert store.run(payload) == store.run(payload)
    with pytest.raises(DomainError):
        store.run({**payload,'requestId':'candidate-2'})
    assert len(call(store, 'snapshot')['candidates']) == 1


def test_screening_preserves_rejection_and_does_not_duplicate_pairs(store):
    job, app = ready(store)
    call(store,'updateApplication',applicationId=app['id'],status='rejected',note='Human demo decision')
    assert call(store,'runScreening',jobId=job['id'],limit=2,minScore=70)['count'] == 0
    with pytest.raises(DomainError):
        call(store,'createInterview',applicationId=app['id'])


def test_human_gates_and_illegal_transitions(store):
    job = store.run(JOB)['result']
    with pytest.raises(DomainError):
        call(store,'runScreening',jobId=job['id'],limit=2,minScore=0)
    with pytest.raises(DomainError):
        call(store,'updateJob',jobId=job['id'],status='active',confirmed='true')
    call(store,'updateJob',jobId=job['id'],status='active',confirmed=True)
    call(store,'syncResumes')
    assert call(store,'runScreening',jobId=job['id'],limit=2,minScore=0)['count'] == 0
    call(store,'verifyCandidate',candidateId='demo_ml_1',confirmed=True)
    app=call(store,'runScreening',jobId=job['id'],limit=2,minScore=0)['applications'][0]
    for status,note in [('approved',''),('hired','Not allowed')]:
        with pytest.raises(DomainError):
            call(store,'updateApplication',applicationId=app['id'],status=status,note=note)
    assert call(store,'snapshot')['applications'][0]['status'] == 'suggested'


def test_interview_token_is_server_generated_and_expires(store):
    job, app = ready(store)
    app=call(store,'updateApplication',applicationId=app['id'],status='approved',note='Reviewed',sessionToken='a'*64)
    assert app['session']['token'] != 'a'*64
    with pytest.raises(DomainError):
        call(store,'getInterview',token='a'*64)
    with pytest.raises(DomainError):
        store.run({'action':'getInterview','token':app['session']['token']}, now=NOW+timedelta(days=7))


def test_answers_replace_and_completed_sessions_are_immutable(store):
    _, app = approved(store)
    token = app['session']['token']
    q=app['session']['questions'][0]['id']
    call(store,'saveAnswer',token=token,questionId=q,text='First')
    result=call(store,'saveAnswer',token=token,questionId=q,text='Revised')
    assert len(result['answers']) == 1
    assert result['answers'][0]['text'] == 'Revised'
    with pytest.raises(DomainError):
        call(store,'finishInterview',token=token)
    for question in app['session']['questions']:
        call(store,'saveAnswer',token=token,questionId=question['id'],text='Final answer')
    call(store,'finishInterview',token=token)
    with pytest.raises(DomainError):
        call(store,'saveAnswer',token=token,questionId=q,text='Too late')


@pytest.mark.parametrize('value',[True,False,float('nan'),float('inf'),'NaN',{},[],None])
def test_invalid_numeric_fields_do_not_write(store,value):
    with pytest.raises((DomainError,ValueError)):
        store.run({**JOB,'minExperience':value})
    assert call(store,'snapshot')['jobs'] == []


def test_request_id_required_and_invalid_action_rejected(store):
    with pytest.raises(DomainError):store.run({k:v for k,v in JOB.items() if k!='requestId'})
    with pytest.raises(DomainError):store.run({'action':[]})
    with pytest.raises(DomainError):store.run({'action':'__proto__'})


def test_api_requires_credential_and_separates_candidate_scope(client):
    assert client.post('/api/talentflow',json={'action':'snapshot'}).status_code == 401
    assert client.post('/api/candidate',json={'action':'snapshot'}).status_code == 403
    r=client.post('/api/talentflow',json={'action':'snapshot'},headers={'x-talentflow-secret':KEY})
    assert r.status_code == 200
    assert r.json()['result'] == {'jobs':[],'candidates':[],'applications':[]}
    assert r.headers['cache-control'] == 'no-store'


def test_api_host_origin_size_and_content_type(client):
    headers={'x-talentflow-secret':KEY}
    assert client.post('/api/talentflow',json={'action':'snapshot'},headers={**headers,'host':'evil.example'}).status_code == 400
    assert client.post('/api/talentflow',json={'action':'snapshot'},headers={**headers,'origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/talentflow',content='{}',headers=headers).status_code == 415
    assert client.post('/api/talentflow',content=b' '*150001,headers={**headers,'content-type':'application/json'}).status_code == 413


@pytest.mark.parametrize('body',[b'{',b'[]',b'{"action":[]}',b'{"action":"snapshot","action":"syncResumes"}',b'{"action":"snapshot","x":NaN}',b'\xff'])
def test_bad_json_is_not_a_server_error(client,body):
    r=client.post('/api/talentflow',content=body,headers={'x-talentflow-secret':KEY,'content-type':'application/json'})
    assert r.status_code == 422


def test_api_lifecycle_and_candidate_boundary(client):
    def api(action,**values):
        r=client.post('/api/talentflow',json={'action':action,**values},headers={'x-talentflow-secret':KEY})
        assert r.status_code==200,r.text
        return r.json()['result']
    job=api(**JOB)
    api('updateJob',jobId=job['id'],status='active',confirmed=True)
    api('syncResumes')
    api('verifyCandidate',candidateId='demo_ml_1',confirmed=True)
    app=api('runScreening',jobId=job['id'],limit=1,minScore=0)['applications'][0]
    app=api('updateApplication',applicationId=app['id'],status='approved',note='Human')
    token=app['session']['token']
    r=client.post('/api/candidate',json={'action':'getInterview','token':token})
    assert r.status_code==200
    assert 'token' not in r.json()['result']
    assert client.post('/api/candidate',json={'action':'getInterview','token':'b'*64}).status_code==403
    assert api('questionAudio',token=token,questionId='q1')['provider']=='browser-speech'


def test_no_default_credential(tmp_path):
    with pytest.raises(RuntimeError):create_app(tmp_path/'x.db', '')
