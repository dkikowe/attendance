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
    # Never connect tests to a provisioned cloud database.
    import os
    for key in ('TURSO_DATABASE_URL', 'TURSO_AUTH_TOKEN', 'VERCEL', 'APP_ENV'):
        os.environ.pop(key, None)
    runtime = tmp_path_factory.mktemp('qr_site')
    for name in ('app.py', 'attendance.py', 'network.py', 'passwords.py', 'database.py', 'localization.py', 'ai_service.py'):
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


