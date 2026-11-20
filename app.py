from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'secure_session_key_2026'
socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = 'database.db'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    
    conn.execute('''CREATE TABLE IF NOT EXISTS Students (
        student_id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT NOT NULL, 
        email TEXT UNIQUE NOT NULL, 
        roll_number TEXT,
        password TEXT NOT NULL
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS Faculty (
        faculty_id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT NOT NULL, 
        email TEXT UNIQUE NOT NULL, 
        department TEXT,
        password TEXT NOT NULL
    )''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS Admins (
        admin_id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT NOT NULL, 
        email TEXT UNIQUE NOT NULL, 
        role TEXT NOT NULL, 
        password TEXT NOT NULL
    )''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS Complaints (
        complaint_id INTEGER PRIMARY KEY AUTOINCREMENT, 
        submitter_id INTEGER NOT NULL, 
        submitter_role TEXT NOT NULL,
        title TEXT,
        area TEXT NOT NULL,
        description TEXT NOT NULL, 
        date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
        status TEXT DEFAULT 'Submitted', 
        priority TEXT,
        file_path TEXT,
        assigned_faculty_id INTEGER,
        is_anonymous BOOLEAN DEFAULT 0,
        FOREIGN KEY(assigned_faculty_id) REFERENCES Faculty(faculty_id)
    )''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS Responses (
        response_id INTEGER PRIMARY KEY AUTOINCREMENT, 
        complaint_id INTEGER, 
        admin_id INTEGER, 
        sender_name TEXT,
        message TEXT NOT NULL, 
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(complaint_id) REFERENCES Complaints(complaint_id)
    )''')
    
    admin = conn.execute('SELECT * FROM Admins WHERE email="admin"').fetchone()
    if not admin:
        hashed_pw = generate_password_hash('admin')
        conn.execute('INSERT INTO Admins (name, email, role, password) VALUES (?, ?, ?, ?)',
                     ('System Admin', 'admin', 'Admin', hashed_pw))

    default_faculties = [
        ('Prof. Sharma', 'faculty', 'Computer Science', 'faculty'),
        ('Mrs. Patil (Warden)', 'warden', 'Other', 'warden'),
        ('Mr. Verma (Maintenance)', 'maintenance', 'Other', 'maintenance')
    ]
    for fname, fuser, fdept, fpw in default_faculties:
        faculty = conn.execute('SELECT * FROM Faculty WHERE email=?', (fuser,)).fetchone()
        if not faculty:
            hashed_pw = generate_password_hash(fpw)
            conn.execute('INSERT INTO Faculty (name, email, department, password) VALUES (?, ?, ?, ?)',
                         (fname, fuser, fdept, hashed_pw))
    
    student = conn.execute('SELECT * FROM Students WHERE email="user"').fetchone()
    if not student:
        hashed_pw = generate_password_hash('user')
        conn.execute('INSERT INTO Students (name, email, roll_number, password) VALUES (?, ?, ?, ?)',
                     ('Standard User', 'user', 'STU1001', hashed_pw))
        
    conn.commit()
    conn.close()

init_db()

def analyze_complaint(text):
    text = text.lower()
    priority = "Low"
    if any(word in text for word in ['ragging', 'harassment', 'fire', 'emergency', 'blood']): priority = "High"
    elif any(word in text for word in ['leak', 'broken', 'not working', 'stolen']): priority = "Medium"
    return priority

def check_escalations():
    conn = get_db()
    three_days_ago = datetime.now() - timedelta(days=3)
    conn.execute('''UPDATE Complaints SET status = 'Escalated', priority = 'High' 
                    WHERE status IN ('Submitted', 'Pending') AND date_submitted < ?''', (three_days_ago,))
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/faculty', methods=['GET'])
def get_faculty():
    conn = get_db()
    faculty = conn.execute('SELECT faculty_id, name, department FROM Faculty').fetchall()
    conn.close()
    return jsonify([dict(f) for f in faculty])

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    conn = get_db()
    hashed_password = generate_password_hash(data['password'])
    username = data.get('username')
    role = data.get('role', 'Student')
    
    try:
        if role == 'Faculty':
            conn.execute('INSERT INTO Faculty (name, email, department, password) VALUES (?, ?, ?, ?)',
                         (data['name'], username, data['department'], hashed_password))
        else:
            conn.execute('INSERT INTO Students (name, email, roll_number, password) VALUES (?, ?, ?, ?)',
                         (data['name'], username, data['student_roll'], hashed_password))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Registered successfully'})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Username already registered'}), 400

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    conn = get_db()
    
    admin = conn.execute('SELECT admin_id as id, name, email as username, role, password FROM Admins WHERE email=?', (username,)).fetchone()
    if admin and check_password_hash(admin['password'], password):
        user_data = dict(admin)
        del user_data['password']
        conn.close()
        return jsonify(user_data)
        
    faculty = conn.execute('SELECT faculty_id as id, name, email as username, department, password FROM Faculty WHERE email=?', (username,)).fetchone()
    if faculty and check_password_hash(faculty['password'], password):
        user_data = dict(faculty)
        del user_data['password']
        user_data['role'] = 'Faculty'
        conn.close()
        return jsonify(user_data)

    student = conn.execute('SELECT student_id as id, name, email as username, roll_number, password FROM Students WHERE email=?', (username,)).fetchone()
    if student and check_password_hash(student['password'], password):
        user_data = dict(student)
        del user_data['password']
        user_data['department'] = 'Student'
        user_data['role'] = 'Student'
        conn.close()
        return jsonify(user_data)
        
    conn.close()
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/complaints', methods=['GET', 'POST'])
def handle_complaints():
    conn = get_db()
    check_escalations()
    
    if request.method == 'POST':
        submitter_id = request.form.get('submitter_id')
        submitter_role = request.form.get('submitter_role')
        title = request.form.get('title')
        description = request.form.get('description')
        area = request.form.get('area')
        is_anonymous = 1 if request.form.get('is_anonymous') == 'true' else 0
        
        priority = analyze_complaint(title + " " + description)
        
        assigned_faculty_id = None
        if area in ['Hostel', 'Mess / Canteen']:
            fac = conn.execute("SELECT faculty_id FROM Faculty WHERE department='Other' LIMIT 1").fetchone()
            if fac: assigned_faculty_id = fac['faculty_id']
        elif area in ['Classroom (Indoor College)', 'Laboratory', 'Campus / Outdoor']:
            fac = conn.execute("SELECT faculty_id FROM Faculty WHERE department='Other' LIMIT 1").fetchone()
            if fac: assigned_faculty_id = fac['faculty_id']
        elif area in ['Faculty / Academic', 'Library']:
            fac = conn.execute("SELECT faculty_id FROM Faculty WHERE department='Computer Science' LIMIT 1").fetchone()
            if fac: assigned_faculty_id = fac['faculty_id']

        file_path = None
        if 'proof_file' in request.files:
            file = request.files['proof_file']
            if file.filename != '':
                file_path = file.filename
                os.makedirs('uploads', exist_ok=True)
                file.save(os.path.join('uploads', file_path))
        
        cursor = conn.execute('''INSERT INTO Complaints (submitter_id, submitter_role, title, description, area, priority, status, file_path, assigned_faculty_id, is_anonymous) 
                                 VALUES (?, ?, ?, ?, ?, ?, 'Submitted', ?, ?, ?)''', 
                              (submitter_id, submitter_role, title, description, area, priority, file_path, assigned_faculty_id, is_anonymous))
        conn.commit()
        complaint_id = cursor.lastrowid
        
        socketio.emit('new_complaint', {'complaint_id': complaint_id, 'title': title, 'priority': priority}, room='admin_room')
        return jsonify({'message': 'Complaint submitted successfully', 'priority': priority, 'area': area})
    
    else:
        user_id = request.args.get('user_id')
        role = request.args.get('role')
        
        if role == 'Admin':
            complaints = conn.execute('''
                SELECT c.complaint_id as id, c.*, 
                       CASE WHEN c.submitter_role = 'Student' THEN s.name ELSE f_sub.name END as student_name,
                       CASE WHEN c.submitter_role = 'Student' THEN 'Student' ELSE f_sub.department END as department,
                       CASE WHEN c.submitter_role = 'Student' THEN s.roll_number ELSE 'N/A' END as student_roll,
                       f_assign.name as assigned_faculty_name
                FROM Complaints c
                LEFT JOIN Students s ON c.submitter_id = s.student_id AND c.submitter_role = 'Student'
                LEFT JOIN Faculty f_sub ON c.submitter_id = f_sub.faculty_id AND c.submitter_role = 'Faculty'
                LEFT JOIN Faculty f_assign ON c.assigned_faculty_id = f_assign.faculty_id
                ORDER BY c.date_submitted DESC
            ''').fetchall()
            
        elif role == 'Faculty':
            complaints = conn.execute('''
                SELECT c.complaint_id as id, c.*, 
                       CASE WHEN c.submitter_role = 'Student' THEN s.name ELSE f_sub.name END as student_name,
                       CASE WHEN c.submitter_role = 'Student' THEN 'Student' ELSE f_sub.department END as department,
                       CASE WHEN c.submitter_role = 'Student' THEN s.roll_number ELSE 'N/A' END as student_roll,
                       f_assign.name as assigned_faculty_name
                FROM Complaints c
                LEFT JOIN Students s ON c.submitter_id = s.student_id AND c.submitter_role = 'Student'
                LEFT JOIN Faculty f_sub ON c.submitter_id = f_sub.faculty_id AND c.submitter_role = 'Faculty'
                LEFT JOIN Faculty f_assign ON c.assigned_faculty_id = f_assign.faculty_id
                WHERE (c.submitter_id = ? AND c.submitter_role = 'Faculty') OR c.assigned_faculty_id = ?
                ORDER BY c.date_submitted DESC
            ''', (user_id, user_id)).fetchall()
            
        else:
            complaints = conn.execute('''
                SELECT c.complaint_id as id, c.*, s.name as student_name, 'Student' as department, s.roll_number as student_roll, f_assign.name as assigned_faculty_name
                FROM Complaints c
                LEFT JOIN Students s ON c.submitter_id = s.student_id
                LEFT JOIN Faculty f_assign ON c.assigned_faculty_id = f_assign.faculty_id
                WHERE c.submitter_id = ? AND c.submitter_role = 'Student'
                ORDER BY c.date_submitted DESC
            ''', (user_id,)).fetchall()
            
        results = []
        for c in complaints:
            c_dict = dict(c)
            if role == 'Faculty' and c_dict.get('is_anonymous'):
                if not (str(c_dict['submitter_id']) == str(user_id) and c_dict['submitter_role'] == 'Faculty'):
                    c_dict['student_name'] = 'Anonymous User'
                    c_dict['department'] = 'Hidden'
                    c_dict['student_roll'] = 'Hidden'
            results.append(c_dict)
            
        return jsonify(results)

@app.route('/api/complaints/<int:id>/status', methods=['PUT'])
def update_status(id):
    data = request.json
    new_status = data['status']
    conn = get_db()
    conn.execute('UPDATE Complaints SET status=? WHERE complaint_id=?', (new_status, id))
    conn.commit()
    conn.close()
    
    socketio.emit('status_update', {'complaint_id': id, 'status': new_status}, room=f'complaint_{id}')
    return jsonify({'message': 'Status updated'})

@app.route('/api/complaints/<int:id>/assign', methods=['PUT'])
def assign_complaint(id):
    data = request.json
    faculty_id = data.get('assigned_faculty_id')
    if faculty_id == "": faculty_id = None 
    
    conn = get_db()
    conn.execute('UPDATE Complaints SET assigned_faculty_id=? WHERE complaint_id=?', (faculty_id, id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Assigned successfully'})

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('send_message')
def handle_message(data):
    conn = get_db()
    conn.execute('INSERT INTO Responses (complaint_id, sender_name, message) VALUES (?, ?, ?)',
                 (data['complaint_id'], data['sender_name'], data['message']))
    conn.commit()
    conn.close()
    emit('receive_message', data, room=f"complaint_{data['complaint_id']}")

@app.route('/api/chat/<int:complaint_id>')
def get_chat(complaint_id):
    conn = get_db()
    msgs = conn.execute('SELECT * FROM Responses WHERE complaint_id=? ORDER BY timestamp ASC', (complaint_id,)).fetchall()
    return jsonify([dict(m) for m in msgs])

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)