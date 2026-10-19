import re
from types import SimpleNamespace
import pytest
from test_attendance import login, lesson


def admin(client):
    import attendance
    with attendance.db() as c:
        uid = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    with client.session_transaction() as session:
        session.update(user_id=uid, username='admin', role='admin', permissions={
            'can_add_student': True, 'can_edit_student': True, 'can_delete_student': True,
            'can_add_faculty': True, 'can_delete_faculty': True, 'can_view_iin': True})


def teacher_form(client):
    page = client.get('/teachers')
    assert page.status_code == 200
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def test_admin_creates_teacher_login_course_and_session(app):
    import attendance
    from passwords import verify_password
    client = app.test_client()
    admin(client)
    token = teacher_form(client)
    result = client.post('/teachers', data={'csrf_token': token, 'username': 'new_teacher', 'password': 'TeacherPass123', 'role': 'admin'}, follow_redirects=True)
    assert b'created. They can now sign in' in result.data
    assert b'TeacherPass123' not in result.data
    with attendance.db() as c:
        user = c.execute("SELECT * FROM users WHERE username='new_teacher'").fetchone()
        gid = c.execute("SELECT id FROM attendance_groups WHERE name='QR test group'").fetchone()[0]
        assert user['role'] == 'teacher'
        assert user['password'].startswith('scrypt:')
        assert verify_password(user['password'], 'TeacherPass123')
    result = client.post('/attendance/groups-and-courses', data={'action': 'course', 'course_name': 'New course', 'teacher_id': user['id'], 'group_id': gid}, follow_redirects=True)
    assert b'Course added' in result.data
    teacher = app.test_client()
    assert teacher.post('/login', data={'username': 'new_teacher', 'password': 'TeacherPass123'}).status_code == 302
    assert b'New course' in teacher.get('/attendance').data
    with attendance.db() as c:
        cid = c.execute("SELECT id FROM courses WHERE name='New course'").fetchone()[0]
    # When the admin creates a class, the assigned teacher owns it and can open its QR.
    result = client.post('/attendance/sessions/create', data={'course_id': cid, 'starts_at': '2026-10-19T12:00', 'ends_at': '2026-10-19T13:00'})
    assert teacher.get(result.headers['Location']).status_code == 200


@pytest.mark.parametrize('username,password', [('x', 'TeacherPass123'), ('bad name', 'TeacherPass123'), ('valid_teacher', 'short')])
def test_teacher_validation(app, username, password):
    import attendance
    c = app.test_client()
    admin(c)
    c.post('/teachers', data={'csrf_token': teacher_form(c), 'username': username, 'password': password})
    with attendance.db() as db:
        assert not db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()


def test_duplicate_teacher_and_csrf(app):
    c = app.test_client()
    admin(c)
    assert c.post('/teachers', data={'username': 'teacher_x', 'password': 'TeacherPass123'}).status_code == 400
    assert c.post('/teachers', data={'csrf_token': 'Ж', 'username': 'teacher_x', 'password': 'TeacherPass123'}).status_code == 400
    r = c.post('/teachers', data={'csrf_token': teacher_form(c), 'username': 'qr_teacher', 'password': 'TeacherPass123'}, follow_redirects=True)
    assert b'already taken' in r.data
    t = app.test_client()
    assert t.post('/login', data={'username': 'qr_teacher', 'password': 'testpass123'}).status_code == 302


@pytest.mark.parametrize('name', ['qr_teacher', 'qr_student'])
def test_teacher_management_admin_only(app, name):
    c = app.test_client()
    login(c, name)
    assert c.get('/teachers').status_code == 403
    assert c.post('/teachers', data={'username': 'admin_hack', 'password': 'TeacherPass123', 'role': 'admin'}).status_code == 403


def test_student_crud_filters_and_english_default_labels(app):
    import attendance
    c = app.test_client()
    admin(c)
    data = {'name': "Alex O'Brien", 'gender': 'Ер', 'birthdate': '2005-01-01', 'faculty': 'Информатика',
            'phone': '+70000000000', 'email': 'alex@example.test', 'iin': '123456789012'}
    assert c.post('/add_student', data=data).json['success']
    with attendance.db() as db:
        uid = db.execute('SELECT id FROM students WHERE email=?', (data['email'],)).fetchone()[0]
    page = c.get('/students?faculty=' + data['faculty'])
    assert b'Computer Science' in page.data
    assert b'Alex O&#39;Brien' in page.data
    assert b'Alex O&#39;Brien' in c.get('/students?search=Computer%20Science').data
    assert b'lang="en"' in page.data
    assert c.get('/students?search=notfound').data.count(b'Alex O&#39;Brien') == 0
    assert b'National ID' in c.get(f'/student/{uid}').data
    assert c.get(f'/student/edit/{uid}').status_code == 200
    data['name'] = 'Updated student'
    assert c.post(f'/student/edit/{uid}', data=data).status_code == 302
    assert b'Updated student' in c.get(f'/student/{uid}').data
    assert b'Updated student' in c.get('/students?gender=%D0%95%D1%80&age_min=16&age_max=30').data
    assert c.get('/statistics?chart_type=invalid').status_code == 200
    assert c.post(f'/student/delete/{uid}').status_code == 302
    with attendance.db() as db:
        assert db.execute('SELECT COUNT(*) FROM students WHERE id=?', (uid,)).fetchone()[0] == 0


def test_faculty_crud(app):
    import attendance
    c = app.test_client()
    admin(c)
    assert c.post('/add_faculty', data={'name': 'Test faculty', 'description': 'Example'}).json['success']
    with attendance.db() as db:
        fid = db.execute("SELECT id FROM faculties WHERE name='Test faculty'").fetchone()[0]
    assert c.post(f'/edit_faculty/{fid}', data={'name': 'Renamed faculty', 'description': 'Updated'}).json['success']
    assert b'Renamed faculty' in c.get('/faculties').data
    assert c.delete(f'/delete_faculty/{fid}').json['success']


@pytest.mark.parametrize('body', [None, [], {'question': 3}, {'question': ''}, {'question': 'x'*4001}])
def test_ai_input_validation(app, body):
    c = app.test_client()
    login(c, 'qr_teacher')
    assert c.post('/api/ask_ai', json=body).status_code == 400


def test_ai_route_context_and_english_answer(app, monkeypatch):
    import app as module
    calls = []
    def fake(question, context):
        calls.append(context)
        return 'There are no student records yet.'
    monkeypatch.setattr(module, 'generate_answer', fake)
    c = app.test_client()
    login(c, 'qr_student')
    r = c.post('/api/ask_ai', json={'question': 'How many students?'})
    assert r.json['success'] and 'no student records' in r.json['answer']
    assert 'students' not in calls[0]
    assert 'my_classes' in calls[0]
    assert calls[0]['faculties'][0]['name'].isascii()
    login(c, 'qr_teacher')
    assert c.post('/api/ask_ai', json={'question': 'How many students?'}).json['success']
    assert 'students' in calls[-1]
    assert all('iin' not in row and 'phone' not in row for row in calls[-1]['students'])


def test_gemini_success_and_safe_errors(monkeypatch):
    import ai_service
    from google.genai import errors
    monkeypatch.setenv('GEMINI_API_KEY', 'private-test-key')
    captured = {}
    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.models = self
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def generate_content(self, **kw):
            captured.update(kw)
            return SimpleNamespace(text='An English answer.')
    monkeypatch.setattr(ai_service.genai, 'Client', FakeClient)
    assert ai_service.generate_answer('Hello', {'total_students': 0}) == 'An English answer.'
    assert captured['model'] == 'gemini-3.5-flash-lite'
    assert 'English' in captured['config'].system_instruction
    def failure(self, **kw):
        raise errors.ClientError(429, {'error': {'message': 'private-test-key'}})
    monkeypatch.setattr(FakeClient, 'generate_content', failure)
    with pytest.raises(ai_service.AssistantError) as e:
        ai_service.generate_answer('Hello', {})
    assert e.value.status == 429 and 'private-test-key' not in str(e.value)
    monkeypatch.delenv('GEMINI_API_KEY')
    with pytest.raises(ai_service.AssistantError, match='not configured'):
        ai_service.generate_answer('Hello', {})


def test_ai_route_provider_error_is_json(app, monkeypatch):
    import app as module
    from ai_service import AssistantError
    def fail(*a): raise AssistantError('Please try again.', 429)
    monkeypatch.setattr(module, 'generate_answer', fail)
    c = app.test_client()
    login(c, 'qr_teacher')
    r = c.post('/api/ask_ai', json={'question': 'Hello'})
    assert r.status_code == 429 and r.json == {'success': False, 'answer': 'Please try again.'}
