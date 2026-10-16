"""Run a fictional lifecycle in a temporary SQLite file; no server or email."""
import json
import tempfile
from pathlib import Path
from talentflow.store import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'demo.sqlite3')
        def call(action,**values):
            return store.run({'action':action,**values})['result']
        job=call('createJob',requestId='demo-job',title='Python Engineer',department='Demo',
                 location='Remote',goal='Build a local prototype',skills=['Python','SQL'],minExperience=2)
        call('updateJob',jobId=job['id'],status='active',confirmed=True)
        for c in call('syncResumes')['candidates']:
            call('verifyCandidate',candidateId=c['id'],confirmed=True)
        shortlisted=call('runScreening',jobId=job['id'],limit=2,minScore=70)
        app=call('updateApplication',applicationId=shortlisted['applications'][0]['id'],status='approved',note='Fictional human review')
        for q in app['session']['questions']:
            call('saveAnswer',token=app['session']['token'],questionId=q['id'],text='This is a fictional answer for testing the persistence workflow.')
        completed=call('finishInterview',token=app['session']['token'])
        assert completed['status']=='completed'
        print(json.dumps({'backend':'python-sqlite','fictional':True,'shortlisted':shortlisted['count'],
                          'answers':len(completed['answers']),'interview_status':completed['status'],
                          'application_status':call('snapshot')['applications'][0]['status'],
                          'provider':completed['evaluation']['provider']},indent=2))


if __name__=='__main__':main()
