from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import sqlite3
from datetime import datetime
import hashlib
import os
from dotenv import load_dotenv
from attendance import attendance_bp, init_attendance_db
from network import phone_base_url
from zoneinfo import ZoneInfo
from passwords import hash_password, verify_password
from database import connect
from localization import english_label, LABELS
from ai_service import generate_answer, AssistantError
import secrets
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'students.db')
load_dotenv(os.path.join(BASE_DIR, '.env.local'))
load_dotenv(os.path.join(BASE_DIR, '.env'))
app = Flask(__name__)
IS_PRODUCTION = os.getenv('VERCEL') == '1' or os.getenv('APP_ENV') == 'production'
if IS_PRODUCTION and not os.getenv('SECRET_KEY'):
    raise RuntimeError('Set SECRET_KEY in the production environment.')
if IS_PRODUCTION and not (os.getenv('TURSO_DATABASE_URL') and os.getenv('TURSO_AUTH_TOKEN')):
    raise RuntimeError('Connect persistent storage before deploying attendance. Local SQLite is only supported in development.')
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=IS_PRODUCTION)
app.config['PUBLIC_BASE_URL'] = os.getenv('PUBLIC_BASE_URL', '')
app.config['ATTENDANCE_TIMEZONE'] = os.getenv('ATTENDANCE_TIMEZONE', 'Asia/Almaty')
ZoneInfo(app.config['ATTENDANCE_TIMEZONE'])
app.register_blueprint(attendance_bp)
app.jinja_env.filters['english'] = english_label

def upgrade_db():
    conn = connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("PRAGMA table_info(students)")
        columns = [column[1] for column in cursor.fetchall()]
        
        print(f"📋 Ағымдағы бағаналар: {columns}")
        
        if 'iin' not in columns:
            print("🔧 iin бағанасын қосу...")
            cursor.execute("ALTER TABLE students ADD COLUMN iin TEXT NOT NULL DEFAULT ''")
            print("✅ iin бағанасы қосылды")
        
        if 'created_by' not in columns:
            print("🔧 created_by бағанасын қосу...")
            cursor.execute("ALTER TABLE students ADD COLUMN created_by INTEGER")
            print("✅ created_by бағанасы қосылды")
            
    except Exception as e:
        print(f"❌ Базаны жаңарту қатесі: {e}")
        pass
    
    conn.commit()
    conn.close()

def init_db():
    if not os.path.exists(os.path.join(BASE_DIR, 'templates')):
        os.makedirs(os.path.join(BASE_DIR, 'templates'))
    
    conn = connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            gender TEXT NOT NULL,
            birthdate TEXT NOT NULL,
            age INTEGER NOT NULL,
            faculty TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            iin TEXT NOT NULL,
            photo TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS faculties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            faculty TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    default_faculties = [
        'Информатика',
        'Математика', 
        'Физика',
        'Химия',
        'Биология',
        'Экономика',
        'Педагогика'
    ]
    
    for faculty in default_faculties:
        cursor.execute("INSERT OR IGNORE INTO faculties (name) VALUES (?)", (faculty,))
    
    # A public deployment never creates accounts with the local demo passwords.
    accounts = [
        ('admin', 'admin', 'BOOTSTRAP_ADMIN_PASSWORD', 'admin123', None),
        ('teacher', 'teacher', 'BOOTSTRAP_TEACHER_PASSWORD', 'teacher123', 'Информатика'),
        ('user', 'user', 'BOOTSTRAP_STUDENT_PASSWORD', 'user123', None),
    ]
    for username, role, key, demo_password, faculty in accounts:
        password = os.getenv(key) or (demo_password if not IS_PRODUCTION else None)
        if password and not cursor.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
            cursor.execute(
                'INSERT OR IGNORE INTO users(username,password,role,faculty) VALUES(?,?,?,?)',
                (username, hash_password(password), role, faculty),
            )

    conn.commit()
    conn.close()
    
    print("🔄 Базаны жаңарту басталды...")
    upgrade_db()
    print("✅ База дайын!")

# Cloud schema changes run explicitly with manage.py; cold starts only serve
# requests, avoiding repeated writes and concurrent schema migrations.
if not IS_PRODUCTION:
    init_db()
    init_attendance_db()

PERMISSIONS = {
    'admin': {
        'can_add_student': True,
        'can_edit_student': True, 
        'can_delete_student': True,
        'can_add_faculty': True,
        'can_delete_faculty': True,
        'can_view_iin': True,
        'can_view_all': True
    },
    'teacher': {
        'can_add_student': True,
        'can_edit_student': True,
        'can_delete_student': True, 
        'can_add_faculty': False,
        'can_delete_faculty': False,
        'can_view_iin': True,
        'can_view_all': True
    },
    'user': {
        'can_add_student': False,        
        'can_edit_student': False,       
        'can_delete_student': False,     
        'can_add_faculty': False,
        'can_delete_faculty': False,
        'can_view_iin': False,
        'can_view_all': True
    }
}

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
    return redirect('/students')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        )
        user = cursor.fetchone()
        conn.close()
        
        if user and verify_password(user[2], password):
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3]
            session['faculty'] = user[4]
            session['permissions'] = PERMISSIONS.get(user[3], {})
            flash(f'Signed in successfully! 👋', 'success')
            next_url = session.pop('next_after_login', '')
            if next_url.startswith('/attendance/checkin/'):
                return redirect(next_url)
            return redirect('/students')
        else:
            flash('Incorrect username or password.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have signed out.', 'info')
    return redirect('/login')

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please sign in first.', 'error')
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

# ОСНОВНЫЕ МАРШРУТЫ С УНИКАЛЬНЫМИ ИМЕНАМИ
@app.route('/students')
@login_required
def students_list():
    try:
        # Фильтр параметрлерін алу
        gender = request.args.get('gender', '')
        age_min = request.args.get('age_min', '')
        age_max = request.args.get('age_max', '')
        faculty = request.args.get('faculty', '')
        search_query = request.args.get('search', '')
        
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        # Негізгі сұрау
        query = "SELECT id, name, gender, age, faculty, phone, email, iin FROM students WHERE 1=1"
        params = []
        
        # Фильтрлерді қосу
        if gender:
            query += " AND gender = ?"
            params.append(gender)
        
        if age_min:
            query += " AND age >= ?"
            params.append(int(age_min))
            
        if age_max:
            query += " AND age <= ?"
            params.append(int(age_max))
            
        if faculty:
            query += " AND faculty LIKE ?"
            params.append(f'%{faculty}%')
            
        if search_query:
            translated_faculties = [original for original, label in LABELS.items()
                                    if search_query.casefold() in label.casefold()]
            query += " AND (name LIKE ? OR faculty LIKE ? OR email LIKE ?"
            params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])
            if translated_faculties:
                query += ' OR faculty IN (' + ','.join('?' for _ in translated_faculties) + ')'
                params.extend(translated_faculties)
            query += ')'
        
        cursor.execute(query, params)
        students = cursor.fetchall()
        
        cursor.execute("SELECT name FROM faculties")
        faculties = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return render_template('index.html', 
                             students=students,
                             faculties=faculties,
                             username=session.get('username'),
                             role=session.get('role'),
                             faculty=session.get('faculty'),
                             permissions=session.get('permissions', {}),
                             # Фильтр мәндерін сақтау
                             filter_gender=gender,
                             filter_age_min=age_min,
                             filter_age_max=age_max,
                             filter_faculty=faculty,
                             search_query=search_query)
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return render_template('index.html', students=[], faculties=[])

@app.route('/student/<int:student_id>')
@login_required
def view_student_details(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті толық қарау"""
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id,name,gender,birthdate,age,faculty,phone,email,iin,photo FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        conn.close()
        
        if student:
            return render_template('student_detail.html',
                                 student=student,
                                 username=session.get('username'),
                                 role=session.get('role'),
                                 permissions=session.get('permissions', {}))
        else:
            flash('Student not found.', 'error')
            return redirect('/students')
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect('/students')

@app.route('/student/edit/<int:student_id>', methods=['GET', 'POST'])
@login_required
def edit_student_page(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті өзгерту беті"""
    if not session.get('permissions', {}).get('can_edit_student', False):
        flash('You do not have permission to edit students.', 'error')
        return redirect('/students')
    
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        if request.method == 'POST':
            # Деректерді өзгерту
            name = request.form['name']
            gender = request.form['gender']
            birthdate = request.form['birthdate']
            faculty = request.form['faculty']
            phone = request.form['phone']
            email = request.form['email']
            iin = request.form['iin']
            
            birth_year = int(birthdate.split('-')[0])
            current_year = datetime.now().year
            age = current_year - birth_year
            
            cursor.execute('''
                UPDATE students 
                SET name=?, gender=?, birthdate=?, age=?, faculty=?, phone=?, email=?, iin=?
                WHERE id=?
            ''', (name, gender, birthdate, age, faculty, phone, email, iin, student_id))
            
            conn.commit()
            conn.close()
            flash('Student updated successfully.', 'success')
            return redirect(f'/student/{student_id}')
        else:
            # Өзгерту бетін көрсету
            cursor.execute("SELECT id,name,gender,birthdate,age,faculty,phone,email,iin,photo FROM students WHERE id = ?", (student_id,))
            student = cursor.fetchone()
            
            cursor.execute("SELECT name FROM faculties")
            faculties = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            if student:
                return render_template('edit_student.html',
                                     student=student,
                                     faculties=faculties,
                                     username=session.get('username'),
                                     role=session.get('role'),
                                     permissions=session.get('permissions', {}))
            else:
                flash('Student not found.', 'error')
                return redirect('/students')
                
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect('/students')

@app.route('/student/delete/<int:student_id>', methods=['POST'])
@login_required
def delete_student_action(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті жою"""
    if not session.get('permissions', {}).get('can_delete_student', False):
        flash('You do not have permission to delete students.', 'error')
        return redirect('/students')
    
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        
        if student:
            cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            conn.close()
            flash(f'Student "{student[0]}" deleted.', 'success')
        else:
            flash('Student not found.', 'error')
            
    except Exception as e:
        flash(f'Error: {e}', 'error')
    
    return redirect('/students')

# API маршруты
@app.route('/add_student', methods=['POST'])
@login_required
def add_student():
    if not session.get('permissions', {}).get('can_add_student', False):
        return jsonify({'success': False, 'message': 'You do not have permission to add students.'})
    
    try:
        name = request.form['name']
        gender = request.form['gender']
        birthdate = request.form['birthdate']
        faculty = request.form['faculty']
        phone = request.form['phone']
        email = request.form['email']
        iin = request.form['iin']
        
        birth_year = int(birthdate.split('-')[0])
        current_year = datetime.now().year
        age = current_year - birth_year
        
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO students (name, gender, birthdate, age, faculty, phone, email, iin, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, gender, birthdate, age, faculty, phone, email, iin, session.get('user_id')))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Student added successfully.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/edit_student/<int:student_id>', methods=['POST'])
@login_required
def api_edit_student(student_id):
    if not session.get('permissions', {}).get('can_edit_student', False):
        return jsonify({'success': False, 'message': 'You do not have permission to edit students.'})
    
    try:
        name = request.form['name']
        gender = request.form['gender']
        birthdate = request.form['birthdate']
        faculty = request.form['faculty']
        phone = request.form['phone']
        email = request.form['email']
        iin = request.form['iin']
        
        birth_year = int(birthdate.split('-')[0])
        current_year = datetime.now().year
        age = current_year - birth_year
        
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE students 
            SET name=?, gender=?, birthdate=?, age=?, faculty=?, phone=?, email=?, iin=?
            WHERE id=?
        ''', (name, gender, birthdate, age, faculty, phone, email, iin, student_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Student updated successfully.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/delete_student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    """Студентті жою"""
    if not session.get('permissions', {}).get('can_delete_student', False):
        return jsonify({'success': False, 'message': 'You do not have permission to delete students.'})
    
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        
        if student:
            cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': f'Student "{student[0]}" deleted.'})
        else:
            return jsonify({'success': False, 'message': 'Student not found.'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

# Факультеттер
@app.route('/faculties')
@login_required
def manage_faculties():
    if not session.get('permissions', {}).get('can_add_faculty', False):
        flash('You do not have permission to manage faculties.', 'error')
        return redirect('/students')
    
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""SELECT f.id,f.name,f.description,f.created_by,f.created_at,
            (SELECT COUNT(*) FROM students s WHERE s.faculty=f.name)
            FROM faculties f ORDER BY f.name""")
        faculties = cursor.fetchall()
        conn.close()
        
        return render_template('faculties.html', 
                             faculties=faculties,
                             username=session.get('username'),
                             role=session.get('role'),
                             permissions=session.get('permissions', {}))
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect('/students')

@app.route('/add_faculty', methods=['POST'])
@login_required
def add_faculty():
    if not session.get('permissions', {}).get('can_add_faculty', False):
        return jsonify({'success': False, 'message': 'You do not have permission to add faculties.'})
    
    try:
        name = request.form['name']
        description = request.form.get('description', '')
        
        if not name.strip():
            return jsonify({'success': False, 'message': 'Enter a faculty name.'})
        
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO faculties (name, description, created_by) VALUES (?, ?, ?)",
            (name.strip(), description.strip(), session.get('user_id'))
        )
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Faculty added successfully.'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'A faculty with this name already exists.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/delete_faculty/<int:faculty_id>', methods=['DELETE'])
@login_required
def delete_faculty(faculty_id):
    if not session.get('permissions', {}).get('can_delete_faculty', False):
        return jsonify({'success': False, 'message': 'You do not have permission to delete faculties.'})
    
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM faculties WHERE id = ?", (faculty_id,))
        faculty = cursor.fetchone()
        
        if faculty:
            cursor.execute("DELETE FROM faculties WHERE id = ?", (faculty_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': f'Faculty "{faculty[0]}" deleted.'})
        else:
            return jsonify({'success': False, 'message': 'Faculty not found.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})


@app.route('/edit_faculty/<int:faculty_id>', methods=['POST'])
@login_required
def edit_faculty(faculty_id):
    if session.get('role') != 'admin':
        return jsonify(success=False, message='Only administrators can edit faculties.'), 403
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name:
        return jsonify(success=False, message='Enter a faculty name.'), 400
    conn = connect(DB_PATH)
    try:
        row = conn.execute('SELECT name FROM faculties WHERE id=?', (faculty_id,)).fetchone()
        if not row:
            return jsonify(success=False, message='Faculty not found.'), 404
        conn.execute('UPDATE faculties SET name=?,description=? WHERE id=?', (name, description, faculty_id))
        conn.execute('UPDATE students SET faculty=? WHERE faculty=?', (name, row[0]))
        conn.execute('UPDATE users SET faculty=? WHERE faculty=?', (name, row[0]))
        conn.commit()
        return jsonify(success=True, message='Faculty updated successfully.')
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify(success=False, message='A faculty with this name already exists.'), 409
    finally:
        conn.close()


#статистика
@app.route('/statistics')
@login_required
def statistics():
    """Статистика беті с фильтрами"""
    try:
        conn = connect(DB_PATH)
        cursor = conn.cursor()
        
        faculty_filter = request.args.get('faculty', '')
        chart_type = request.args.get('chart_type', 'bar')
        if chart_type not in ('bar', 'pie', 'line', 'doughnut', 'radar', 'polarArea'):
            chart_type = 'bar'
        gender_filter = request.args.get('gender', '')
        age_range = request.args.get('age_range', '')
        
        where_conditions = []
        params = []
        
        if faculty_filter:
            where_conditions.append("faculty = ?")
            params.append(faculty_filter)
        
        if gender_filter:
            where_conditions.append("gender = ?")
            params.append(gender_filter)
            
        if age_range:
            if age_range == 'under_20':
                where_conditions.append("age < 20")
            elif age_range == '20_25':
                where_conditions.append("age BETWEEN 20 AND 25")
            elif age_range == 'over_25':
                where_conditions.append("age > 25")
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        cursor.execute(f'''
            SELECT faculty, COUNT(*) as count 
            FROM students 
            WHERE {where_clause}
            GROUP BY faculty 
            ORDER BY count DESC
        ''', params)
        faculty_stats = cursor.fetchall()
        
        cursor.execute(f'''
            SELECT gender, COUNT(*) as count 
            FROM students 
            WHERE {where_clause}
            GROUP BY gender 
            ORDER BY count DESC
        ''', params)
        gender_stats = cursor.fetchall()
        cursor.execute(f"SELECT faculty,gender,COUNT(*) FROM students WHERE {where_clause} GROUP BY faculty,gender", params)
        faculty_gender_stats = [(english_label(r[0]), english_label(r[1]), r[2]) for r in cursor.fetchall()]
        
        cursor.execute(f'''
            SELECT 
                CASE 
                    WHEN age < 20 THEN 'Under 20'
                    WHEN age BETWEEN 20 AND 22 THEN '20–22 years'
                    WHEN age BETWEEN 23 AND 25 THEN '23–25 years'
                    ELSE '26+ years'
                END as age_group,
                COUNT(*) as count
            FROM students 
            WHERE {where_clause}
            GROUP BY age_group 
            ORDER BY count DESC
        ''', params)
        age_stats = cursor.fetchall()
        
        cursor.execute(f'''
            SELECT age, COUNT(*) as count 
            FROM students 
            WHERE {where_clause}
            GROUP BY age 
            ORDER BY age
        ''', params)
        age_histogram = cursor.fetchall()
        
        cursor.execute(f"SELECT COUNT(*) FROM students WHERE {where_clause}", params)
        total_students = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT AVG(age) FROM students WHERE {where_clause}", params)
        avg_age_result = cursor.fetchone()[0]
        avg_age = round(avg_age_result, 1) if avg_age_result else 0
        

        cursor.execute("SELECT DISTINCT name FROM faculties ORDER BY name")
        faculties = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        return render_template('statistics.html',
                             faculty_stats=faculty_stats,
                             faculty_gender_stats=faculty_gender_stats,
                             gender_stats=gender_stats,
                             age_stats=age_stats,
                             age_histogram=age_histogram,
                             total_students=total_students,
                             avg_age=avg_age,
                             faculties=faculties,
                             current_faculty=faculty_filter,
                             current_chart_type=chart_type,
                             current_gender=gender_filter,
                             current_age_range=age_range,
                             username=session.get('username'),
                             role=session.get('role'),
                             permissions=session.get('permissions', {}))
                             
    except Exception as e:
        flash(f'Unable to load statistics: {e}', 'error')
        return redirect('/students')

#AI
@app.route('/ai_assistant')
@login_required
def ai_assistant():
    """Жасанды интеллект көмекшісі беті"""
    from datetime import datetime
    return render_template('ai_assistant.html',
                         username=session.get('username'),
                         role=session.get('role'),
                         permissions=session.get('permissions', {}),
                         now=datetime.now())
@app.route('/teachers', methods=['GET', 'POST'])
@login_required
def manage_teachers():
    if session.get('role') != 'admin':
        return ('Teacher management is restricted to administrators.', 403)
    csrf_token = session.setdefault('teacher_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        submitted_token = request.form.get('csrf_token', '')
        if not submitted_token.isascii() or not secrets.compare_digest(submitted_token, csrf_token):
            return ('Invalid form token. Reload the page and try again.', 400)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username):
            flash('Use 3–64 letters, numbers, dots, underscores or hyphens for the username.', 'error')
        elif not 8 <= len(password) <= 128:
            flash('The password must contain 8–128 characters.', 'error')
        else:
            conn = connect(DB_PATH)
            try:
                conn.execute("INSERT INTO users(username,password,role) VALUES(?,?,'teacher')",
                             (username, hash_password(password)))
                conn.commit()
                flash(f'Teacher "{username}" created. They can now sign in with the password you provided. Assign a course under Groups / courses.', 'success')
            except sqlite3.IntegrityError:
                conn.rollback()
                flash('That username is already taken. Choose another one.', 'error')
            finally:
                conn.close()
        return redirect(url_for('manage_teachers'))
    conn = connect(DB_PATH)
    try:
        teachers = conn.execute("""SELECT u.id,u.username,u.created_at,COUNT(c.id)
            FROM users u LEFT JOIN courses c ON c.teacher_id=u.id
            WHERE u.role='teacher' GROUP BY u.id,u.username,u.created_at ORDER BY u.username""").fetchall()
    finally:
        conn.close()
    return render_template('teachers.html', teachers=teachers, csrf_token=csrf_token)


@app.route('/api/ask_ai', methods=['POST'])
@login_required
def ask_ai():
    data = request.get_json(silent=True)
    question = data.get('question') if isinstance(data, dict) else None
    if not isinstance(question, str) or not question.strip():
        return jsonify(success=False, answer='Please enter a question.'), 400
    question = question.strip()
    if len(question) > 4000:
        return jsonify(success=False, answer='Please keep your question under 4,000 characters.'), 400
    conn = connect(DB_PATH)
    try:
        total, avg = conn.execute('SELECT COUNT(*),AVG(age) FROM students').fetchone()
        faculties = conn.execute("""SELECT f.name,COUNT(s.id) FROM faculties f
            LEFT JOIN students s ON s.faculty=f.name GROUP BY f.id,f.name ORDER BY f.name""").fetchall()
        context = {'role': session.get('role'), 'total_students': total, 'average_age': round(avg or 0, 1),
                   'faculties': [{'name': english_label(f[0]), 'students': f[1]} for f in faculties]}
        if session.get('role') in ('admin', 'teacher'):
            rows = conn.execute('SELECT name,gender,age,faculty FROM students ORDER BY id LIMIT 200').fetchall()
            context['students'] = [{'name': r[0], 'gender': english_label(r[1]), 'age': r[2],
                                    'faculty': english_label(r[3])} for r in rows]
            context['student_records_included'] = len(rows)
            context['student_list_truncated'] = total > len(rows)
        else:
            rows = conn.execute("""SELECT c.name,s.starts_at,s.ends_at,a.marked_at
                FROM users u JOIN courses c ON c.group_id=u.attendance_group_id
                JOIN class_sessions s ON s.course_id=c.id
                LEFT JOIN attendance_records a ON a.session_id=s.id AND a.student_id=u.id
                WHERE u.id=? ORDER BY s.starts_at DESC LIMIT 50""", (session['user_id'],)).fetchall()
            context['my_classes'] = [{'course': english_label(r[0]), 'starts_at': r[1], 'ends_at': r[2],
                                      'checked_in_at': r[3]} for r in rows]
    finally:
        conn.close()
    try:
        return jsonify(success=True, answer=generate_answer(question, context))
    except AssistantError as error:
        return jsonify(success=False, answer=str(error)), error.status

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    print(f"🚀 Компьютер: http://127.0.0.1:{port}")
    try:
        print('📱 Телефон (бір Wi-Fi): ' + phone_base_url(f'http://127.0.0.1:{port}', app.config['PUBLIC_BASE_URL']))
    except ValueError as error:
        print(f'⚠️ QR: {error}')
    print("🤖 AI Assistant қосылды")
    app.run(host='0.0.0.0', port=port, debug=False)

