"""Recruitment state machine. Providers are deterministic educational mocks."""
import copy
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

ACTIONS = {'snapshot', 'createJob', 'updateJob', 'syncResumes', 'importCandidate',
           'verifyCandidate', 'runScreening', 'updateApplication', 'createInterview',
           'getInterview', 'saveAnswer', 'finishInterview', 'questionAudio', 'recruiterReport'}
PUBLIC = {'getInterview', 'saveAnswer', 'finishInterview', 'questionAudio'}
READS = {'snapshot', 'getInterview', 'questionAudio', 'recruiterReport'}


class DomainError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def text(value, label, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainError(f'مقدار {label} معتبر نیست')
    return value.strip()


def number(value, low, high, label):
    if isinstance(value, bool) or value is None or value == '':
        raise DomainError(f'مقدار {label} معتبر نیست')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise DomainError(f'مقدار {label} معتبر نیست') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise DomainError(f'{label} باید بین {low} و {high} باشد')
    return result


def skills(value):
    if isinstance(value, str):
        value = re.split('[,،\n]', value)
    if not isinstance(value, list) or len(value) > 30:
        raise DomainError('حداکثر ۳۰ مهارت مجاز است')
    result = [text(item, 'مهارت', 150) for item in value if item != '']
    if not result:
        raise DomainError('حداقل یک مهارت لازم است')
    return list(dict.fromkeys(result))


def execute(state, payload, now=None):
    """Return result and new state; never mutate caller state or perform I/O."""
    state = copy.deepcopy(state)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    action = payload.get('action')
    if not isinstance(action, str) or action not in ACTIONS:
        raise DomainError('عملیات ناشناخته است')

    def all_rows(kind):
        return [row['data'] for row in state.values() if row['kind'] == kind]

    def get(kind, identity):
        if not isinstance(identity, str) or f'{kind}:{identity}' not in state:
            raise DomainError('رکورد پیدا نشد', 404)
        return state[f'{kind}:{identity}']['data']

    def save(kind, data):
        data['updatedAt'] = stamp
        state[f'{kind}:{data["id"]}'] = {'kind': kind, 'data': data}
        return data

    def event(data, name, note=''):
        data.setdefault('history', []).append({'at': stamp, 'action': name, 'note': note})

    def public(app):
        session = app['session']
        return {**{k: session[k] for k in ['id', 'status', 'questions', 'answers', 'evaluation', 'expiresAt']},
                'candidateName': get('candidate', app['candidateId'])['name'],
                'jobTitle': get('job', app['jobId'])['title']}

    def authenticate():
        token = text(payload.get('token'), 'لینک مصاحبه', 128)
        if not re.fullmatch('[a-f0-9]{64}', token):
            raise DomainError('لینک مصاحبه معتبر نیست', 403)
        app = next((a for a in all_rows('application') if a.get('session') and
                    secrets.compare_digest(a['session']['token'], token)), None)
        if not app or app['status'] not in ['approved', 'interviewed', 'hired', 'rejected_after_interview']:
            raise DomainError('لینک مصاحبه معتبر نیست', 403)
        if datetime.fromisoformat(app['session']['expiresAt']) <= now:
            raise DomainError('لینک مصاحبه منقضی شده است', 403)
        return app

    def provision(app):
        if app.get('session'):
            return app
        c, j = get('candidate', app['candidateId']), get('job', app['jobId'])
        prompts = [
            ('تجربه مرتبط', f'برای نقش {j["title"]} یک پروژه مرتبط با {j["skills"][0]} و سهم خود را توضیح دهید.'),
            ('مهارت تخصصی', f'یک تصمیم فنی دشوار درباره {c["skills"][0]} را شرح دهید.'),
            ('حل مسئله', f'برای هدف {j["goal"]} چه فرضی را ابتدا آزمایش می‌کنید؟'),
            ('همکاری', 'یک اختلاف حرفه‌ای در تیم و نتیجه آن را شرح دهید.'),
            ('برنامه اجرایی', f'در ۹۰ روز اول نقش {j["title"]} چه خروجی قابل سنجشی دارید؟'),
        ]
        app['session'] = {'id': 'session_'+app['id'], 'token': secrets.token_hex(32), 'status': 'ready',
                          'createdAt': stamp, 'expiresAt': (now+timedelta(days=7)).isoformat(),
                          'questions': [{'id': f'q{i}', 'competency': c, 'text': t} for i, (c, t) in enumerate(prompts, 1)],
                          'answers': [], 'evaluation': None, 'provider': 'mock'}
        app['invitation'] = {'provider': 'mock', 'status': 'simulated', 'to': c['email'], 'at': stamp,
                             'subject': f'دعوت به مصاحبه {j["title"]}', 'message': 'ایمیل واقعی ارسال نشده؛ لینک را از سایت باز کنید.'}
        event(app, 'interview_created', 'پرسش‌های ماک؛ بدون ارسال ایمیل')
        return app

    if action == 'snapshot':
        result = {'jobs': all_rows('job'), 'candidates': all_rows('candidate'), 'applications': all_rows('application')}
    elif action == 'createJob':
        title, goal, required = text(payload.get('title'), 'عنوان', 150), text(payload.get('goal'), 'هدف', 2000), skills(payload.get('skills'))
        result = save('job', {'id': 'job_'+uuid4().hex, 'title': title, 'goal': goal, 'skills': required,
                             'department': text(payload.get('department'), 'واحد', 150),
                             'location': text(payload.get('location'), 'محل', 200),
                             'minExperience': number(payload.get('minExperience', 0), 0, 50, 'سابقه'),
                             'passScore': number(payload.get('passScore', 70), 0, 100, 'حدنصاب'),
                             'description': f'شرح شغل آزمایشی: {title}\nهدف: {goal}\nمهارت‌ها: {"، ".join(required)}\nنیازمند تأیید انسانی.',
                             'status': 'draft', 'rubricVersion': 'mock-skill-overlap-v1', 'provider': 'mock',
                             'createdAt': stamp, 'history': [{'at': stamp, 'action': 'draft_created'}]})
    elif action == 'updateJob':
        job = get('job', payload.get('jobId'))
        status = payload.get('status')
        if status not in ['active', 'closed'] or (status == 'active' and payload.get('confirmed') is not True):
            raise DomainError('وضعیت معتبر و تأیید انسانی برای فعال‌سازی لازم است')
        job['status'] = status
        event(job, 'job_status', status)
        result = save('job', job)
    elif action == 'syncResumes':
        added = []
        samples = [('demo_ml_1', 'داوطلب آزمایشی یک', 'demo.one@example.com', ['Python', 'PyTorch', 'MLOps', 'SQL', 'Docker', 'FastAPI'], 6),
                   ('demo_ml_2', 'داوطلب آزمایشی دو', 'demo.two@example.com', ['Python', 'PyTorch', 'MLOps', 'SQL', 'React', 'Figma'], 4),
                   ('demo_design', 'داوطلب آزمایشی سه', 'demo.three@example.com', ['Figma', 'UX Research', 'Design Systems'], 3)]
        for identity, name, email, found, experience in samples:
            if f'candidate:{identity}' in state or any(c['email'] == email for c in all_rows('candidate')):
                continue
            added.append(save('candidate', {'id': identity, 'name': name, 'email': email, 'skills': found,
                                           'experience': experience, 'verified': False, 'source': 'mock-drive',
                                           'resumeText': 'رزومه ساختگی برای تمرین', 'createdAt': stamp,
                                           'history': [{'at': stamp, 'action': 'mock_ingest'}]}))
        result = {'count': len(added), 'candidates': added}
    elif action == 'importCandidate':
        email = text(payload.get('email'), 'ایمیل', 254).lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise DomainError('ایمیل معتبر نیست')
        if any(c['email'] == email for c in all_rows('candidate')):
            raise DomainError('ایمیل قبلاً ثبت شده است', 409)
        result = save('candidate', {'id': 'candidate_'+uuid4().hex, 'name': text(payload.get('name'), 'نام', 150),
                                   'email': email, 'skills': skills(payload.get('skills')),
                                   'experience': number(payload.get('experience'), 0, 50, 'سابقه'),
                                   'resumeText': text(payload.get('resumeText'), 'رزومه', 30000), 'verified': False,
                                   'source': 'manual-text', 'createdAt': stamp, 'history': [{'at': stamp, 'action': 'manual_ingest'}]})
    elif action == 'verifyCandidate':
        candidate = get('candidate', payload.get('candidateId'))
        if payload.get('confirmed') is not True:
            raise DomainError('تأیید انسانی لازم است')
        candidate['verified'] = True
        event(candidate, 'human_verified')
        result = save('candidate', candidate)
    elif action == 'runScreening':
        job = get('job', payload.get('jobId'))
        if job['status'] != 'active':
            raise DomainError('شغل باید فعال باشد')
        limit = number(payload.get('limit'), 1, 40, 'تعداد')
        if not limit.is_integer():
            raise DomainError('تعداد باید صحیح باشد')
        minimum = number(payload.get('minScore'), 0, 100, 'امتیاز')
        ranked = []
        for c in all_rows('candidate'):
            identity = job['id']+'~'+c['id']
            if not c['verified'] or f'application:{identity}' in state:
                continue
            found = [s for s in job['skills'] if s.lower() in [v.lower() for v in c['skills']]]
            missing = [s for s in job['skills'] if s not in found]
            score = math.floor(80*len(found)/len(job['skills']) + 20*(min(1, c['experience']/job['minExperience']) if job['minExperience'] else 1) + 0.5)
            if score >= minimum:
                ranked.append((score, c['id'], identity, found, missing))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        applications = []
        for score, candidate_id, identity, found, missing in ranked[:int(limit)]:
            applications.append(save('application', {'id': identity, 'jobId': job['id'], 'candidateId': candidate_id,
                'status': 'suggested', 'createdAt': stamp, 'session': None, 'invitation': None,
                'history': [{'at': stamp, 'action': 'shortlisted'}], 'screening': {'score': score,
                'matchedSkills': found, 'missingSkills': missing, 'minScore': minimum, 'limit': int(limit),
                'rubricVersion': job['rubricVersion'], 'provider': 'mock', 'note': 'امتیاز تطبیق واژه‌ای؛ ارزیابی واقعی نیست.'}}))
        result = {'count': len(applications), 'applications': applications, 'note': 'نتیجه در SQLite ذخیره شد' if applications else 'داوطلب جدید واجد شرایط نیست'}
    elif action == 'updateApplication':
        app = get('application', payload.get('applicationId'))
        status = payload.get('status')
        if status not in ['approved', 'rejected', 'hired', 'rejected_after_interview']:
            raise DomainError('تصمیم نامعتبر است')
        if app['status'] != status:
            allowed = {'suggested': ['approved', 'rejected'], 'interviewed': ['hired', 'rejected_after_interview']}
            if status not in allowed.get(app['status'], []):
                raise DomainError('تغییر وضعیت مجاز نیست', 409)
            note = text(payload.get('note'), 'دلیل تصمیم', 2000)
            app['status'], app['decisionNote'] = status, note
            event(app, status, note)
            if status == 'approved':
                provision(app)
            save('application', app)
        result = app
    elif action == 'createInterview':
        app = get('application', payload.get('applicationId'))
        if app['status'] != 'approved':
            raise DomainError('تأیید انسانی پیش از مصاحبه لازم است')
        result = save('application', provision(app))
    elif action in PUBLIC:
        app = authenticate()
        session = app['session']
        if action == 'questionAudio':
            question = next((q for q in session['questions'] if q['id'] == payload.get('questionId')), None)
            if not question:
                raise DomainError('سؤال نامعتبر است')
            result = {'text': question['text'], 'language': 'fa-IR', 'provider': 'browser-speech', 'mock': True}
        else:
            if action == 'saveAnswer':
                if session['status'] == 'completed':
                    raise DomainError('پاسخ مصاحبه پایان‌یافته قابل تغییر نیست', 409)
                if not any(q['id'] == payload.get('questionId') for q in session['questions']):
                    raise DomainError('سؤال نامعتبر است')
                answer = {'questionId': payload['questionId'], 'text': text(payload.get('text'), 'پاسخ', 10000),
                          'at': stamp, 'source': 'mock-stt' if payload.get('source') == 'mock-stt' else 'typed'}
                session['answers'] = [a for a in session['answers'] if a['questionId'] != answer['questionId']] + [answer]
                session['status'] = 'in_progress'
                event(app, 'answer_saved', answer['questionId'])
                save('application', app)
            elif action == 'finishInterview' and session['status'] != 'completed':
                answers = {a['questionId']: a for a in session['answers']}
                if any(q['id'] not in answers for q in session['questions']):
                    raise DomainError('پاسخ همه سؤال‌ها لازم است')
                dimensions = [{'competency': q['competency'], 'score': min(95, 40+len(answers[q['id']]['text'].split())),
                               'evidence': answers[q['id']]['text'][:160]} for q in session['questions']]
                score = math.floor(sum(d['score'] for d in dimensions)/len(dimensions)+0.5)
                threshold = get('job', app['jobId'])['passScore']
                session['evaluation'] = {'score': score, 'dimensions': dimensions, 'provider': 'mock',
                    'rubricVersion': 'mock-answer-length-v1', 'threshold': threshold, 'aboveThreshold': score >= threshold,
                    'summary': 'امتیاز نمایشی براساس طول پاسخ؛ سنجش شایستگی نیست. تصمیم با انسان است.', 'at': stamp}
                session['status'], session['completedAt'], app['status'] = 'completed', stamp, 'interviewed'
                event(app, 'interview_completed', 'گزارش ماک؛ تصمیم انسانی لازم است')
                save('application', app)
            result = public(app)
    else:  # recruiterReport
        def view(app):
            return {**app, 'candidate': get('candidate', app['candidateId']), 'job': get('job', app['jobId'])}
        result = view(get('application', payload['applicationId'])) if payload.get('applicationId') else {'applications': [view(a) for a in all_rows('application')]}
    return result, state
