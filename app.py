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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'students.db')
load_dotenv(os.path.join(BASE_DIR, '.env'))
app = Flask(__name__)
IS_PRODUCTION = os.getenv('VERCEL') == '1' or os.getenv('APP_ENV') == 'production'
if IS_PRODUCTION and not os.getenv('SECRET_KEY'):
    raise RuntimeError('Set SECRET_KEY in the production environment.')
if IS_PRODUCTION and not os.getenv('TURSO_DATABASE_URL'):
    raise RuntimeError('Connect persistent storage before deploying attendance. Local SQLite is only supported in development.')
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=IS_PRODUCTION)
app.config['PUBLIC_BASE_URL'] = os.getenv('PUBLIC_BASE_URL', '')
app.config['ATTENDANCE_TIMEZONE'] = os.getenv('ATTENDANCE_TIMEZONE', 'Asia/Almaty')
ZoneInfo(app.config['ATTENDANCE_TIMEZONE'])
app.register_blueprint(attendance_bp)

def upgrade_db():
    conn = sqlite3.connect(DB_PATH)
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
    
    conn = sqlite3.connect(DB_PATH)
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
        if password:
            cursor.execute(
                'INSERT OR IGNORE INTO users(username,password,role,faculty) VALUES(?,?,?,?)',
                (username, hash_password(password), role, faculty),
            )

    conn.commit()
    conn.close()
    
    print("🔄 Базаны жаңарту басталды...")
    upgrade_db()
    print("✅ База дайын!")

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
        
        conn = sqlite3.connect(DB_PATH)
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
            flash(f'Сәтті кірдіңіз! 👋', 'success')
            next_url = session.pop('next_after_login', '')
            if next_url.startswith('/attendance/checkin/'):
                return redirect(next_url)
            return redirect('/students')
        else:
            flash('Қате логин немесе пароль! ❌', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Жүйеден шықтыңыз! 👋', 'info')
    return redirect('/login')

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Алдымен жүйеге кіруіңіз керек! 🔐', 'error')
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
        
        conn = sqlite3.connect(DB_PATH)
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
            query += " AND (name LIKE ? OR faculty LIKE ? OR email LIKE ?)"
            params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])
        
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
        flash(f'Қате: {e}', 'error')
        return render_template('index.html', students=[], faculties=[])

@app.route('/student/<int:student_id>')
@login_required
def view_student_details(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті толық қарау"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        conn.close()
        
        if student:
            return render_template('student_detail.html',
                                 student=student,
                                 username=session.get('username'),
                                 role=session.get('role'),
                                 permissions=session.get('permissions', {}))
        else:
            flash('Студент табылмады! ❌', 'error')
            return redirect('/students')
    except Exception as e:
        flash(f'Қате: {e}', 'error')
        return redirect('/students')

@app.route('/student/edit/<int:student_id>', methods=['GET', 'POST'])
@login_required
def edit_student_page(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті өзгерту беті"""
    if not session.get('permissions', {}).get('can_edit_student', False):
        flash('Студентті өзгертуге рұқсат жоқ! ⚠️', 'error')
        return redirect('/students')
    
    try:
        conn = sqlite3.connect(DB_PATH)
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
            flash('Студент сәтті өзгертілді! ✅', 'success')
            return redirect(f'/student/{student_id}')
        else:
            # Өзгерту бетін көрсету
            cursor.execute("SELECT * FROM students WHERE id = ?", (student_id,))
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
                flash('Студент табылмады! ❌', 'error')
                return redirect('/students')
                
    except Exception as e:
        flash(f'Қате: {e}', 'error')
        return redirect('/students')

@app.route('/student/delete/<int:student_id>', methods=['POST'])
@login_required
def delete_student_action(student_id):  # ИЗМЕНЕНО ИМЯ
    """Студентті жою"""
    if not session.get('permissions', {}).get('can_delete_student', False):
        flash('Студентті жоюға рұқсат жоқ! ⚠️', 'error')
        return redirect('/students')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        
        if student:
            cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            conn.close()
            flash(f'"{student[0]}" студенті жойылды! 🗑️', 'success')
        else:
            flash('Студент табылмады! ❌', 'error')
            
    except Exception as e:
        flash(f'Қате: {e}', 'error')
    
    return redirect('/students')

# API маршруты
@app.route('/add_student', methods=['POST'])
@login_required
def add_student():
    if not session.get('permissions', {}).get('can_add_student', False):
        return jsonify({'success': False, 'message': 'Студент қосуға рұқсат жоқ! ⚠️'})
    
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
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO students (name, gender, birthdate, age, faculty, phone, email, iin, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, gender, birthdate, age, faculty, phone, email, iin, session.get('user_id')))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Студент сәтті қосылды! 🎉'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Қате: {e}'})

@app.route('/api/edit_student/<int:student_id>', methods=['POST'])
@login_required
def api_edit_student(student_id):
    if not session.get('permissions', {}).get('can_edit_student', False):
        return jsonify({'success': False, 'message': 'Студентті өзгертуге рұқсат жоқ! ⚠️'})
    
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
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE students 
            SET name=?, gender=?, birthdate=?, age=?, faculty=?, phone=?, email=?, iin=?
            WHERE id=?
        ''', (name, gender, birthdate, age, faculty, phone, email, iin, student_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Студент сәтті өзгертілді! ✅'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Қате: {e}'})

@app.route('/delete_student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    """Студентті жою"""
    if not session.get('permissions', {}).get('can_delete_student', False):
        return jsonify({'success': False, 'message': 'Студентті жоюға рұқсат жоқ! ⚠️'})
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        
        if student:
            cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': f'"{student[0]}" студенті жойылды! 🗑️'})
        else:
            return jsonify({'success': False, 'message': 'Студент табылмады! ❌'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Қате: {e}'})

# Факультеттер
@app.route('/faculties')
@login_required
def manage_faculties():
    if not session.get('permissions', {}).get('can_add_faculty', False):
        flash('Факультеттерді басқаруға рұқсат жоқ! ⚠️', 'error')
        return redirect('/students')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM faculties ORDER BY name")
        faculties = cursor.fetchall()
        conn.close()
        
        return render_template('faculties.html', 
                             faculties=faculties,
                             username=session.get('username'),
                             role=session.get('role'),
                             permissions=session.get('permissions', {}))
    except Exception as e:
        flash(f'Қате: {e}', 'error')
        return redirect('/students')

@app.route('/add_faculty', methods=['POST'])
@login_required
def add_faculty():
    if not session.get('permissions', {}).get('can_add_faculty', False):
        return jsonify({'success': False, 'message': 'Факультет қосуға рұқсат жоқ! ⚠️'})
    
    try:
        name = request.form['name']
        description = request.form.get('description', '')
        
        if not name.strip():
            return jsonify({'success': False, 'message': 'Факультет атын енгізіңіз! 📝'})
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO faculties (name, description, created_by) VALUES (?, ?, ?)",
            (name.strip(), description.strip(), session.get('user_id'))
        )
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Факультет сәтті қосылды! 🎓'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Бұл атаумен факультет бар! ❌'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Қате: {e}'})

@app.route('/delete_faculty/<int:faculty_id>', methods=['DELETE'])
@login_required
def delete_faculty(faculty_id):
    if not session.get('permissions', {}).get('can_delete_faculty', False):
        return jsonify({'success': False, 'message': 'Факультет жоюға рұқсат жоқ! ⚠️'})
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM faculties WHERE id = ?", (faculty_id,))
        faculty = cursor.fetchone()
        
        if faculty:
            cursor.execute("DELETE FROM faculties WHERE id = ?", (faculty_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': f'"{faculty[0]}" факультеті жойылды! 🗑️'})
        else:
            return jsonify({'success': False, 'message': 'Факультет табылмады! ❌'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Қате: {e}'})


#статистика
@app.route('/statistics')
@login_required
def statistics():
    """Статистика беті с фильтрами"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        faculty_filter = request.args.get('faculty', '')
        chart_type = request.args.get('chart_type', 'bar')
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
        
        cursor.execute(f'''
            SELECT 
                CASE 
                    WHEN age < 20 THEN '18-19 жас'
                    WHEN age BETWEEN 20 AND 22 THEN '20-22 жас'
                    WHEN age BETWEEN 23 AND 25 THEN '23-25 жас'
                    ELSE '26+ жас'
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
        flash(f'Статистиканы жүктеу кезінде қате: {e}', 'error')
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
@app.route('/api/ask_ai', methods=['POST'])
@login_required
def ask_ai():
    import sqlite3
    from google import genai

    data = request.get_json()
    question = data.get('question', '').strip()

    if not question:
        return jsonify({
            'success': False,
            'answer': 'Сұрақты енгізіңіз!'
        }), 400

    try:
        # Gemini API
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        # =========================
        # 1. DATABASE
        # =========================

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, name, gender, birthdate, age,
                   faculty, phone, email, iin
            FROM students
        """)
        students = cursor.fetchall()

        cursor.execute("""
            SELECT id, name, description
            FROM faculties
        """)
        faculties = cursor.fetchall()

        cursor.execute("""
            SELECT COUNT(*), AVG(age)
            FROM students
        """)
        total_st, avg_age = cursor.fetchone()

        avg_age = round(avg_age or 0, 1)

        conn.close()

        # =========================
        # 2. COMPACT DATABASE DATA
        # =========================

        compact_students = []

        for s in students:
            compact_students.append({
                "id": s[0],
                "name": s[1],
                "gender": s[2],
                "age": s[4],
                "faculty": s[5],
                "phone": s[6],
                "email": s[7],
                "iin": s[8]
            })

        compact_faculties = [
            {
                "id": f[0],
                "name": f[1],
                "desc": f[2]
            }
            for f in faculties
        ]

        database_context = {
            "total_students": total_st,
            "avg_age": avg_age,
            "students": compact_students,
            "faculties": compact_faculties
        }

        # =========================
        # 3. SYSTEM INSTRUCTION
        # =========================

        system_prompt = """
Сіз студенттер базасының интеллектуалды AI көмекшісісіз.

Талаптар:

1. Жауапты тек қазақ тілінде беріңіз.
2. Тек DATABASE CONTEXT ішіндегі нақты деректерді пайдаланыңыз.
3. Деректерді ойдан шығармаңыз.
4. Студент туралы сұрақ қойылса, DATABASE CONTEXT ішінен іздеңіз.
5. Факультет туралы сұрақ қойылса, DATABASE CONTEXT ішінен іздеңіз.
6. Статистика сұралса, нақты мәліметтерді пайдаланыңыз.
7. Егер ақпарат DATABASE CONTEXT ішінде болмаса:
   "Бұл ақпарат база ішінде жоқ" деп жауап беріңіз.
8. Жауапты қысқа, түсінікті және нақты беріңіз.
"""

        # =========================
        # 4. GEMINI REQUEST
        # =========================

        prompt = f"""
{system_prompt}

USER QUESTION:
{question}

DATABASE CONTEXT:
{database_context}
"""

        response = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents=prompt
)

        

        answer = response.text

        return jsonify({
            'success': True,
            'answer': answer
        })

    except Exception as e:
        print("AI ERROR:", e)

        return jsonify({
            'success': False,
            'answer': f'AI сервисінде қате пайда болды: {str(e)}'
        }), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    print(f"🚀 Компьютер: http://127.0.0.1:{port}")
    try:
        print('📱 Телефон (бір Wi-Fi): ' + phone_base_url(f'http://127.0.0.1:{port}', app.config['PUBLIC_BASE_URL']))
    except ValueError as error:
        print(f'⚠️ QR: {error}')
    print("🤖 AI Assistant қосылды")
    app.run(host='0.0.0.0', port=port, debug=False)

