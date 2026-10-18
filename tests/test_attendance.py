"""Run with python -m pytest. Only temporary copies of students.db are changed."""
import base64
import hashlib
import importlib
import io
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
import zxingcpp

SOURCE = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='session')
def site(tmp_path_factory):
    runtime = tmp_path_factory.mktemp('qr_site')
    for name in ('app.py', 'attendance.py', 'network.py', 'passwords.py'):
        shutil.copy2(SOURCE / name, runtime / name)
    shutil.copytree(SOURCE / 'templates', runtime / 'templates')
    sys.path.insert(0, str(runtime))
    module = importlib.import_module('app')
    module.app.config.update(TESTING=True, SECRET_KEY='isolated-test-secret', PUBLIC_BASE_URL='')
    with sqlite3.connect(module.DB_PATH) as conn:
        conn.execute("INSERT INTO attendance_groups(name) VALUES('QR test group')")
        group = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        for name, role, gid in [('qr_teacher', 'teacher', None), ('qr_student', 'user', group), ('qr_other', 'user', None)]:
            conn.execute('INSERT INTO users(username,password,role,attendance_group_id) VALUES(?,?,?,?)', (name, hashlib.md5(b'testpass123').hexdigest(), role, gid))
        teacher = conn.execute("SELECT id FROM users WHERE username='qr_teacher'").fetchone()[0]
        conn.execute('INSERT INTO courses(name,teacher_id,group_id) VALUES(?,?,?)', ('QR test course', teacher, group))
    snapshot = runtime / 'baseline.db'
    shutil.copy2(module.DB_PATH, snapshot)
    return module, snapshot

@pytest.fixture
def app(site):
    module, snapshot = site
    shutil.copy2(snapshot, module.DB_PATH)
    module.app.config.update(PUBLIC_BASE_URL='', ATTENDANCE_TIMEZONE='Asia/Almaty')
    return module.app


def login(client, name):
    return client.post('/login', data={'username': name, 'password': 'testpass123'})


def lesson(app, start=None, end=None):
    client = app.test_client()
    login(client, 'qr_teacher')
    import attendance
    with attendance.db() as conn:
        cid = conn.execute("SELECT id FROM courses WHERE name='QR test course'").fetchone()['id']
    now = datetime.now(ZoneInfo(app.config['ATTENDANCE_TIMEZONE'])).replace(tzinfo=None)
    response = client.post('/attendance/sessions/create', data={
        'course_id': cid,
        'starts_at': (start or now - timedelta(minutes=5)).isoformat(timespec='minutes'),
        'ends_at': (end or now + timedelta(minutes=30)).isoformat(timespec='minutes'),
    })
    assert response.status_code == 302
    detail = response.headers['Location']
    with attendance.db() as conn:
        row = conn.execute('SELECT * FROM class_sessions ORDER BY id DESC LIMIT 1').fetchone()
    return client, detail, '/attendance/checkin/' + row['token']


def records():
    import attendance
    with attendance.db() as conn:
        return conn.execute('SELECT COUNT(*) FROM attendance_records').fetchone()[0]


def test_original_bug_fixed_and_actual_qr_decodes(app, monkeypatch):
    import network
    monkeypatch.setattr(network, 'lan_ip', lambda: '192.168.1.55')
    teacher, detail, checkin = lesson(app)
    teacher.post('/login', data={'username': 'qr_teacher', 'password': 'testpass123'}, base_url='http://127.0.0.1:5001')
    page = teacher.get(detail, base_url='http://127.0.0.1:5001')
    assert page.status_code == 200
    encoded = re.search(rb'base64,([^" ]+)', page.data).group(1)
    qr = Image.open(io.BytesIO(base64.b64decode(encoded)))
    decoded = zxingcpp.read_barcode(qr)
    assert decoded.text == 'http://192.168.1.55:5001' + checkin
    assert page.headers['Cache-Control'] == 'no-store'


def test_scan_login_record_teacher_list_and_csv(app):
    teacher, detail, checkin = lesson(app)
    student = app.test_client()
    response = student.get(checkin)
    assert response.headers['Location'] == '/login'
    assert login(student, 'qr_student').headers['Location'] == checkin
    page = student.get(checkin, follow_redirects=True)
    assert 'Қатысу белгіленді'.encode() in page.data
    assert records() == 1
    page = teacher.get(detail)
    assert '1 қатысты'.encode() in page.data
    assert b'qr_student' in page.data
    report = teacher.get('/attendance/report.csv')
    assert report.status_code == 200
    assert 'Қатысты'.encode() in report.data
    assert b'qr_student' in report.data
    student.get(checkin, follow_redirects=True)
    assert records() == 1


def test_scan_register_then_login_resumes_checkin(app):
    _, _, checkin = lesson(app)
    import attendance
    with attendance.db() as conn:
        gid = conn.execute("SELECT id FROM attendance_groups WHERE name='QR test group'").fetchone()['id']
    student = app.test_client()
    student.get(checkin)
    response = student.post('/attendance/register', data={'username': 'qr_new_student', 'password': 'testpass123', 'group_id': gid})
    assert response.headers['Location'] == '/login'
    assert login(student, 'qr_new_student').headers['Location'] == checkin
    student.get(checkin)
    assert records() == 1


@pytest.mark.parametrize('name,token_override,message', [
    ('qr_other', None, 'сіздің тобыңызға'),
    ('qr_student', 'invalid-token', 'QR-код табылмады'),
])
def test_invalid_checkins_do_not_write(app, name, token_override, message):
    _, _, checkin = lesson(app)
    client = app.test_client()
    login(client, name)
    page = client.get('/attendance/checkin/' + token_override if token_override else checkin, follow_redirects=True)
    assert message.encode() in page.data
    assert records() == 0


@pytest.mark.parametrize('offset', [-120, 120])
def test_expired_or_future_session(app, offset):
    now = datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
    _, _, checkin = lesson(app, now + timedelta(minutes=offset), now + timedelta(minutes=offset+30))
    client = app.test_client()
    login(client, 'qr_student')
    page = client.get(checkin, follow_redirects=True)
    assert 'QR-код уақыты'.encode() in page.data
    assert records() == 0


def test_student_cannot_create_session_or_export(app):
    client = app.test_client()
    login(client, 'qr_student')
    assert client.post('/attendance/sessions/create', data={}).status_code == 403
    assert client.get('/attendance/report.csv').status_code == 403


def test_teacher_cannot_open_someone_elses_session(app):
    _, detail, _ = lesson(app)
    import attendance
    with attendance.db() as conn:
        conn.execute("INSERT INTO users(username,password,role) VALUES('qr_second_teacher',?,'teacher')", (hashlib.md5(b'testpass123').hexdigest(),))
    client = app.test_client()
    login(client, 'qr_second_teacher')
    assert client.get(detail).status_code == 404


def test_invalid_times_do_not_crash(app):
    teacher, _, _ = lesson(app)
    import attendance
    with attendance.db() as conn:
        cid = conn.execute("SELECT id FROM courses WHERE name='QR test course'").fetchone()['id']
    response = teacher.post('/attendance/sessions/create', data={'course_id': cid, 'starts_at': '2026-10-18T12:00+05:00', 'ends_at': '2026-10-18T13:00'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/attendance'


def test_school_clock_independent_of_host_timezone(app):
    import attendance
    with app.app_context():
        now = attendance.attendance_now()
        expected = datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
        assert abs((now - expected).total_seconds()) < 2


@pytest.mark.parametrize('base,configured,expected', [
    ('http://localhost:5000', '', 'http://192.168.1.55:5000'),
    ('http://127.0.0.2:5001', '', 'http://192.168.1.55:5001'),
    ('http://[::1]:5000', '', 'http://192.168.1.55:5000'),
    ('http://192.168.1.9:5000', '', 'http://192.168.1.9:5000'),
    ('https://school.example', '', 'https://school.example'),
    ('http://localhost:5000', 'https://school.example/', 'https://school.example'),
])
def test_phone_addresses(app, monkeypatch, base, configured, expected):
    import network
    monkeypatch.setattr(network, 'lan_ip', lambda: '192.168.1.55')
    assert network.phone_base_url(base, configured) == expected


@pytest.mark.parametrize('configured', ['http://localhost:5000', 'javascript:alert(1)', 'https://school.example/path', 'https://school.example:bad', 'https://user:pass@school.example', 'https://school.example?x=1'])
def test_bad_config_explains_problem_instead_of_showing_bad_qr(app, configured):
    teacher, detail, _ = lesson(app)
    app.config['PUBLIC_BASE_URL'] = configured
    page = teacher.get(detail)
    assert page.status_code == 200
    assert b'role="alert"' in page.data
    assert b'data:image/png' not in page.data


def test_no_network_explains_problem(app, monkeypatch):
    import network
    monkeypatch.setattr(network, 'lan_ip', lambda: None)
    teacher, detail, _ = lesson(app)
    page = teacher.get(detail)
    assert b'role="alert"' in page.data
    assert b'data:image/png' not in page.data


def test_mobile_login_has_viewport(app):
    page = app.test_client().get('/login')
    assert b'name="viewport"' in page.data


def test_existing_pages_still_render(app):
    client = app.test_client()
    login(client, 'qr_teacher')
    for path in ('/students', '/statistics', '/attendance'):
        assert client.get(path).status_code == 200
    # The existing faculty management page intentionally redirects teachers.
    assert client.get('/faculties').status_code == 302
