import base64, csv, hashlib, io, os, secrets, sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo
import qrcode
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from network import phone_base_url
from passwords import hash_password
from database import connect
from localization import english_label

attendance_bp = Blueprint('attendance', __name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'students.db')

def attendance_now():
    # datetime-local values are wall-clock times in the school's timezone.
    return datetime.now(ZoneInfo(current_app.config['ATTENDANCE_TIMEZONE'])).replace(tzinfo=None)

@contextmanager
def db():
    conn = connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_attendance_db():
    """Add attendance tables without dropping or rebuilding existing site data."""
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS attendance_groups(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,teacher_id INTEGER NOT NULL REFERENCES users(id),group_id INTEGER NOT NULL REFERENCES attendance_groups(id),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(name,teacher_id,group_id));
        CREATE TABLE IF NOT EXISTS class_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,course_id INTEGER NOT NULL REFERENCES courses(id),teacher_id INTEGER NOT NULL REFERENCES users(id),starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,token TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS attendance_records(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES class_sessions(id) ON DELETE CASCADE,student_id INTEGER NOT NULL REFERENCES users(id),marked_at TEXT NOT NULL,UNIQUE(session_id,student_id));
        ''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'attendance_group_id' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN attendance_group_id INTEGER REFERENCES attendance_groups(id)')
        c.execute("INSERT OR IGNORE INTO attendance_groups(name) VALUES ('1-топ')")
        gid=c.execute("SELECT id FROM attendance_groups WHERE name='1-топ'").fetchone()['id']
        c.execute("UPDATE users SET attendance_group_id=? WHERE role='user' AND attendance_group_id IS NULL",(gid,))
        teacher=c.execute("SELECT id FROM users WHERE role='teacher' ORDER BY id LIMIT 1").fetchone()
        if teacher:
            c.execute("INSERT OR IGNORE INTO courses(name,teacher_id,group_id) VALUES('Демо курс',?,?)",(teacher['id'],gid))

def logged(view):
    @wraps(view)
    def wrapped(*a,**kw):
        if not session.get('user_id'):
            session['next_after_login'] = request.path
            flash('Please sign in first. QR check-in will continue after sign-in.','error')
            return redirect(url_for('login'))
        return view(*a,**kw)
    return wrapped

def allow(*roles):
    def deco(view):
        @wraps(view)
        @logged
        def wrapped(*a,**kw):
            if session.get('role') not in roles: abort(403)
            return view(*a,**kw)
        return wrapped
    return deco

@attendance_bp.route('/attendance')
@logged
def dashboard():
    role=session.get('role')
    with db() as c:
        if role=='user':
            student=c.execute('SELECT attendance_group_id FROM users WHERE id=?',(session['user_id'],)).fetchone()
            rows=[]
            if student and student['attendance_group_id']:
                rows=c.execute('''SELECT s.id,s.starts_at,s.ends_at,c.name course,g.name group_name,t.username teacher,a.marked_at
                    FROM class_sessions s JOIN courses c ON c.id=s.course_id JOIN attendance_groups g ON g.id=c.group_id
                    JOIN users t ON t.id=s.teacher_id LEFT JOIN attendance_records a ON a.session_id=s.id AND a.student_id=?
                    WHERE c.group_id=? ORDER BY s.starts_at DESC''',(session['user_id'],student['attendance_group_id'])).fetchall()
            return render_template('attendance_dashboard.html',is_student=True,sessions=rows,courses=[])
        query='''SELECT s.id,s.starts_at,s.ends_at,c.name course,g.name group_name,t.username teacher,
            (SELECT COUNT(*) FROM attendance_records a WHERE a.session_id=s.id) present_count
            FROM class_sessions s JOIN courses c ON c.id=s.course_id JOIN attendance_groups g ON g.id=c.group_id
            JOIN users t ON t.id=s.teacher_id'''
        args=()
        if role=='teacher': query+=' WHERE s.teacher_id=?'; args=(session['user_id'],)
        rows=c.execute(query+' ORDER BY s.starts_at DESC',args).fetchall()
        cq='SELECT c.id,c.name,g.name group_name FROM courses c JOIN attendance_groups g ON g.id=c.group_id'
        ca=()
        if role=='teacher': cq+=' WHERE c.teacher_id=?'; ca=(session['user_id'],)
        courses=c.execute(cq+' ORDER BY c.name',ca).fetchall()
    return render_template('attendance_dashboard.html',is_student=False,sessions=rows,courses=courses)

@attendance_bp.route('/attendance/register',methods=['GET','POST'])
def register_student():
    if request.method=='POST':
        name=request.form.get('username','').strip(); password=request.form.get('password','')
        try: gid=int(request.form.get('group_id',''))
        except ValueError: gid=0
        if len(name)<3 or len(password)<8: flash('The username must have at least 3 characters and the password at least 8 characters.','error')
        else:
            try:
                with db() as c:
                    if not c.execute('SELECT id FROM attendance_groups WHERE id=?',(gid,)).fetchone():
                        flash('Select a group.','error')
                    else:
                        c.execute('INSERT INTO users(username,password,role,attendance_group_id) VALUES(?,?,?,?)',(name,hash_password(password),'user',gid))
                        flash('Registration complete. You can now sign in.','success')
                        return redirect(url_for('login'))
            except sqlite3.IntegrityError: flash('That username is already taken.','error')
    with db() as c: groups=c.execute('SELECT id,name FROM attendance_groups ORDER BY name').fetchall()
    return render_template('attendance_register.html',groups=groups)

@attendance_bp.route('/attendance/groups-and-courses',methods=['GET','POST'])
@allow('admin')
def manage_courses():
    if request.method=='POST':
        try:
            with db() as c:
                if request.form.get('action')=='group':
                    name=request.form.get('group_name','').strip()
                    if not name: raise ValueError('Enter a group name.')
                    c.execute('INSERT INTO attendance_groups(name) VALUES(?)',(name,)); flash('Group added.','success')
                elif request.form.get('action')=='course':
                    name=request.form.get('course_name','').strip(); tid=int(request.form.get('teacher_id','')); gid=int(request.form.get('group_id',''))
                    if not name: raise ValueError('Enter a course name.')
                    if not c.execute("SELECT id FROM users WHERE id=? AND role='teacher'",(tid,)).fetchone() or not c.execute('SELECT id FROM attendance_groups WHERE id=?',(gid,)).fetchone(): raise ValueError('Select a teacher and a group.')
                    c.execute('INSERT INTO courses(name,teacher_id,group_id) VALUES(?,?,?)',(name,tid,gid)); flash('Course added.','success')
                else: raise ValueError('Select an action.')
        except (ValueError,sqlite3.IntegrityError) as e: flash('This record already exists.' if isinstance(e, sqlite3.IntegrityError) else str(e) or 'Select valid values.','error')
        return redirect(url_for('attendance.manage_courses'))
    with db() as c:
        groups=c.execute('''SELECT g.id,g.name,COUNT(u.id) student_count FROM attendance_groups g LEFT JOIN users u ON u.attendance_group_id=g.id AND u.role='user' GROUP BY g.id ORDER BY g.name''').fetchall()
        teachers=c.execute("SELECT id,username FROM users WHERE role='teacher' ORDER BY username").fetchall()
        courses=c.execute('''SELECT c.name,g.name group_name,u.username teacher FROM courses c JOIN attendance_groups g ON g.id=c.group_id JOIN users u ON u.id=c.teacher_id ORDER BY c.name''').fetchall()
    return render_template('attendance_manage.html',groups=groups,teachers=teachers,courses=courses)

@attendance_bp.route('/attendance/sessions/create',methods=['POST'])
@allow('teacher','admin')
def create_session():
    try:
        cid=int(request.form.get('course_id','')); start=datetime.fromisoformat(request.form.get('starts_at','')); end=datetime.fromisoformat(request.form.get('ends_at',''))
        if start.tzinfo is not None or end.tzinfo is not None or end<=start: raise ValueError()
        with db() as c:
            course=c.execute('SELECT * FROM courses WHERE id=?',(cid,)).fetchone()
            if not course or (session.get('role')=='teacher' and course['teacher_id']!=session['user_id']): abort(403)
            cur=c.execute('INSERT INTO class_sessions(course_id,teacher_id,starts_at,ends_at,token) VALUES(?,?,?,?,?)',(cid,course['teacher_id'],start.isoformat(timespec='minutes'),end.isoformat(timespec='minutes'),secrets.token_urlsafe(32)))
            sid=cur.lastrowid
        return redirect(url_for('attendance.session_detail',session_id=sid))
    except (ValueError,TypeError): flash('Choose a course and valid start and end times.','error'); return redirect(url_for('attendance.dashboard'))

@attendance_bp.route('/attendance/session/<int:session_id>')
@allow('teacher','admin')
def session_detail(session_id):
    with db() as c:
        item=c.execute('SELECT s.*,c.name course,c.group_id,g.name group_name FROM class_sessions s JOIN courses c ON c.id=s.course_id JOIN attendance_groups g ON g.id=c.group_id WHERE s.id=?',(session_id,)).fetchone()
        if not item or (session.get('role')=='teacher' and item['teacher_id']!=session['user_id']): abort(404)
        records=c.execute('''SELECT u.username,a.marked_at FROM users u
            LEFT JOIN attendance_records a ON a.session_id=? AND a.student_id=u.id
            WHERE u.role='user' AND u.attendance_group_id=? ORDER BY u.username''',(session_id,item['group_id'])).fetchall()
    present_count=sum(1 for r in records if r['marked_at'])
    absent_count=len(records)-present_count
    target = qr_data = None
    qr_error = None
    try:
        base = phone_base_url(request.host_url, current_app.config.get('PUBLIC_BASE_URL', ''))
        target = base + url_for('attendance.checkin', token=item['token'])
        qr = qrcode.make(target)
        buf = io.BytesIO()
        qr.save(buf, format='PNG')
        qr_data = base64.b64encode(buf.getvalue()).decode('ascii')
    except ValueError as error:
        qr_error = str(error)
    response = current_app.make_response(render_template('attendance_session.html',item=item,records=records,qr_data=qr_data,qr_error=qr_error,checkin_url=target,present_count=present_count,absent_count=absent_count,server_now=attendance_now().isoformat(timespec='seconds')))
    response.headers['Cache-Control'] = 'no-store'
    return response

@attendance_bp.route('/attendance/checkin/<token>')
@allow('user')
def checkin(token):
    now=attendance_now()
    with db() as c:
        item=c.execute('SELECT s.*,c.name course,c.group_id FROM class_sessions s JOIN courses c ON c.id=s.course_id WHERE s.token=?',(token,)).fetchone()
        student=c.execute('SELECT attendance_group_id FROM users WHERE id=?',(session['user_id'],)).fetchone()
        if not item: flash('QR code not found.','error')
        elif not student or item['group_id']!=student['attendance_group_id']: flash('This QR code is not for your group.','error')
        elif now<datetime.fromisoformat(item['starts_at']) or now>datetime.fromisoformat(item['ends_at']): flash('The QR code is not active yet or has expired.','error')
        else:
            try:
                c.execute('INSERT INTO attendance_records(session_id,student_id,marked_at) VALUES(?,?,?)',(item['id'],session['user_id'],now.isoformat(timespec='seconds')))
                flash(f'Attendance recorded: {english_label(item["course"])}.','success')
            except sqlite3.IntegrityError: flash('You have already checked in for this class.','info')
    return redirect(url_for('attendance.dashboard'))

@attendance_bp.route('/attendance/report.csv')
@allow('teacher','admin')
def report_csv():
    query='''SELECT c.name,g.name,s.starts_at,s.ends_at,u.username,a.marked_at,CASE WHEN a.id IS NULL THEN 'Absent' ELSE 'Present' END
    FROM class_sessions s JOIN courses c ON c.id=s.course_id JOIN attendance_groups g ON g.id=c.group_id
    JOIN users u ON u.role='user' AND u.attendance_group_id=c.group_id
    LEFT JOIN attendance_records a ON a.session_id=s.id AND a.student_id=u.id'''
    args=()
    if session.get('role')=='teacher': query+=' WHERE s.teacher_id=?'; args=(session['user_id'],)
    with db() as c: rows=c.execute(query+' ORDER BY s.starts_at DESC,g.name,u.username',args).fetchall()
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(['Course','Group','Start','End','Student username','Check-in time','Attendance']); writer.writerows([(english_label(r[0]),english_label(r[1]),*tuple(r)[2:]) for r in rows])
    data=io.BytesIO(('\ufeff'+out.getvalue()).encode('utf-8'))
    return send_file(data,mimetype='text/csv; charset=utf-8',as_attachment=True,download_name='attendance_report.csv')

