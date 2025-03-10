from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, make_response
import requests
import json
import sqlite3
import os
import re
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
from datetime import datetime
import textstat
import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
from docx import Document
import pdfkit
from io import BytesIO
import base64
from docx2pdf import convert
import tempfile
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
import logging
import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' 


# Download NLTK resources
try:
    nltk.data.find('vader_lexicon')
except LookupError:
    nltk.download('vader_lexicon')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_default_secret_key')
csrf = CSRFProtect(app)

# LM Studio API configuration
LM_STUDIO_API_URL = "http://localhost:1234/v1/chat/completions"

# Google OAuth setup
blueprint = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    scope=["https://www.googleapis.com/auth/userinfo.profile",
           "https://www.googleapis.com/auth/userinfo.email",
           "openid"],
    redirect_to="google_authorized"
)
app.register_blueprint(blueprint, url_prefix="/login")

# Initialize Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

### 📌 Database Functions ###

def get_db_connection():
    """Establish a database connection."""
    conn = sqlite3.connect('resume_builder.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users table with OAuth fields
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        oauth_id TEXT UNIQUE,
        oauth_provider TEXT,
        role TEXT DEFAULT 'user',
        credits INTEGER DEFAULT 3,
        last_credit_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Resumes table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        score REAL,
        target_job TEXT,
        template_id INTEGER,  -- Allow NULL for optional templates
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (template_id) REFERENCES templates (id) ON DELETE SET NULL
    )
    ''')
    # Resume sections table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS resume_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resume_id INTEGER NOT NULL,
        section_name TEXT NOT NULL,
        content TEXT NOT NULL,
        FOREIGN KEY (resume_id) REFERENCES resumes (id) ON DELETE CASCADE
    )
    ''')

    # Templates table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        css_content TEXT,
        html_structure TEXT
    )
    ''')

    # Check if admin user already exists
    cursor.execute('SELECT COUNT(*) FROM users WHERE username = ? AND role = ?', ('admin', 'admin'))
    if cursor.fetchone()[0] == 0:
        # Create a dummy admin user for testing
        hashed_password = generate_password_hash('admin123')
        cursor.execute('''
        INSERT INTO users (username, email, password, role, credits)
        VALUES (?, ?, ?, ?, ?)
        ''', ('admin', 'admin@example.com', hashed_password, 'admin', 100))


    # Insert default templates if not already present
    cursor.execute('SELECT COUNT(*) FROM templates')
    if cursor.fetchone()[0] == 0:
        cursor.executemany('''
        INSERT INTO templates (name, description, css_content, html_structure)
        VALUES (?, ?, ?, ?)
        ''', [
            ('Professional', 'Clean and professional template', 'body { font-family: Arial; }', '<div>{sections}</div>'),
            ('Modern', 'Contemporary layout', 'body { font-family: Segoe UI; }', '<div>{sections}</div>'),
            ('Minimalist', 'Simple and elegant', 'body { font-family: Georgia; }', '<div>{sections}</div>')
        ])
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        resume_id INTEGER,
        messages TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (resume_id) REFERENCES resumes (id)
    )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def find_user_by_oauth(oauth_id, provider):
    """Find a user by their OAuth ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE oauth_id = ? AND oauth_provider = ?", (oauth_id, provider))
    user = cursor.fetchone()
    conn.close()
    return user

def find_user_by_email(email):
    """Find a user by their email."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_or_update_oauth_user(email, username, oauth_id, provider):
    """Create a new OAuth user or update an existing one."""
    user = find_user_by_email(email)
    conn = get_db_connection()
    cursor = conn.cursor()

    if user:
        # Update existing user with OAuth details
        cursor.execute(
            "UPDATE users SET oauth_id = ?, oauth_provider = ? WHERE id = ?",
            (oauth_id, provider, user['id'])
        )
        user_id = user['id']
        username = user['username']
    else:
        import secrets
        random_password = generate_password_hash(secrets.token_urlsafe(16))

        # Ensure unique username
        base_username = username
        count = 1
        while True:
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            if not cursor.fetchone():
                break
            username = f"{base_username}{count}"
            count += 1

        # Insert new user
        cursor.execute(
            "INSERT INTO users (username, email, password, oauth_id, oauth_provider) VALUES (?, ?, ?, ?, ?)",
            (username, email, random_password, oauth_id, provider)
        )
        user_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return user_id, username

### 📌 OAuth Login Handlers ###

@oauth_authorized.connect_via(blueprint)
def google_logged_in(blueprint, token):
    """Handle Google OAuth login."""
    if not token:
        flash("Failed to log in with Google.", category="error")
        return False

    resp = blueprint.session.get("/oauth2/v1/userinfo")
    if not resp.ok:
        flash("Failed to retrieve user information.", category="error")
        return False

    user_info = resp.json()
    google_id = user_info.get("id")
    email = user_info.get("email")
    username = email.split('@')[0]

    # Create or update user in the database
    user_id, username = create_or_update_oauth_user(email, username, google_id, 'google')

    # Set session variables
    session['user_id'] = user_id
    session['username'] = username

    flash('Logged in successfully via Google!')

    # Handle redirects properly
    next_url = session.pop('next', url_for('dashboard'))
    return redirect(next_url)

@app.route('/login/google')
def login_google():
    """Trigger Google login."""
    if not google.authorized:
        return redirect(url_for("google.login"))
    return redirect(url_for('dashboard'))

@app.route('/login/google/authorized')
def google_authorized():
    """OAuth callback route."""
    if not google.authorized:
        flash('Authentication failed.')
        return redirect(url_for('login'))

    return google_logged_in(blueprint, google.token)



def reset_daily_credits():
    """Reset user credits to 3 if 24 hours have passed since the last reset."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get current timestamp
    now = datetime.now()

    # Update credits only if last reset was more than 24 hours ago
    cursor.execute("""
        UPDATE users 
        SET credits = 3, last_credit_reset = ?
        WHERE role = 'user' AND (last_credit_reset IS NULL OR last_credit_reset < ?)
    """, (now, now - timedelta(days=1)))

    conn.commit()
    conn.close()
    print("✅ Daily credits reset for eligible users.")

# Start background scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_credits, 'interval', hours=24, next_run_time=datetime.now())  # Run on startup
scheduler.start()


# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Admin role check decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            flash("Access denied. Admins only!", "danger")
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# Create chat completion using LM Studio API
def create_chat_completion(user_input, context=""):
    """
    Creates a chat completion using the LM Studio API
    With optional context for more personalized responses
    """
    # For resume-specific guidance with additional context
    resume_instructions = f"""
    You are a helpful resume-building assistant. Help the user create or improve their resume
    by providing specific, actionable advice on formatting, content, and wording.
    Be encouraging but honest. Focus on personalized recommendations.
    Use strong action verbs and help quantify achievements.

    {context}

    User query:
    """

    enhanced_input = resume_instructions + user_input

    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistral-7b-instruct-v0.3:2",
        "messages": [
            {"role": "user", "content": enhanced_input}
        ],
        "temperature": 0.7,
        "max_tokens": -1,
        "stream": False
    }

    response = requests.post(LM_STUDIO_API_URL, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error {response.status_code}: {response.text}")

# ATS Keyword Optimization
def optimize_for_ats(resume_text, job_description):
    """Analyze and optimize a resume for ATS compatibility based on job description"""
    prompt = f"""
    Analyze this resume for ATS (Applicant Tracking System) compatibility based on the job description.
    Identify missing keywords and suggest improvements.
    
    Job Description:
    {job_description}
    
    Resume:
    {resume_text}
    
    Please provide:
    1. A list of important keywords from the job description that are missing in the resume
    2. Specific suggestions for incorporating these keywords naturally
    3. Overall ATS optimization score out of 100
    """
    
    try:
        completion = create_chat_completion(prompt)
        return completion['choices'][0]['message']['content']
    except Exception as e:
        return f"Error in ATS analysis: {str(e)}"

# Grammar and formatting check
def check_grammar_formatting(text):
    """Perform basic grammar and formatting checks on resume text"""
    prompt = f"""
    Analyze this resume text for grammatical errors, formatting inconsistencies, and style improvements:
    
    {text}
    
    Please provide:
    1. Grammatical errors with corrections
    2. Formatting inconsistencies and how to fix them
    3. Style improvements for stronger impact
    """
    
    try:
        completion = create_chat_completion(prompt)
        return completion['choices'][0]['message']['content']
    except Exception as e:
        return f"Error in grammar check: {str(e)}"

# Resume scoring system
def score_resume(resume_text, job_title=None):
    """Score a resume based on various factors and return score with feedback"""
    # Basic metrics
    word_count = len(resume_text.split())
    
    # Readability
    readability_score = textstat.flesch_reading_ease(resume_text)
    
    # Action verbs list (simplified)
    action_verbs = [
        'achieved', 'improved', 'launched', 'developed', 'created', 
        'implemented', 'managed', 'led', 'designed', 'increased',
        'decreased', 'reduced', 'negotiated', 'coordinated', 'generated',
        'delivered', 'organized', 'supervised', 'trained', 'analyzed'
    ]
    
    # Count action verbs
    action_verb_count = sum(1 for word in resume_text.lower().split() if word in action_verbs)
    
    # Count bullet points
    bullet_point_count = resume_text.count('•') + resume_text.count('-') + resume_text.count('*')
    
    # Job-specific scoring if job title is provided
    job_specific_score = 0
    if job_title:
        prompt = f"""
        Score this resume for the position of {job_title} on a scale of 0-100.
        Consider relevance, qualifications, and presentation.
        
        Resume:
        {resume_text}
        
        Provide a numerical score and very brief explanation.
        """
        try:
            completion = create_chat_completion(prompt)
            response = completion['choices'][0]['message']['content']
            # Extract score from response (simple approach)
            match = re.search(r'(\d{1,3})(?:/100)?', response)
            if match:
                job_specific_score = int(match.group(1))
            else:
                job_specific_score = 60  # Default if parsing fails
        except:
            job_specific_score = 60  # Default on error
    
    # Calculate final score (weighted components)
    base_score = 50
    length_score = min(20, max(0, (word_count - 200) / 20)) if word_count < 600 else max(0, 20 - (word_count - 600) / 50)
    readability_bonus = min(10, max(0, (readability_score - 30) / 5))
    action_verb_bonus = min(10, action_verb_count / 2)
    bullet_point_bonus = min(10, bullet_point_count / 3)
    
    total_score = base_score + length_score + readability_bonus + action_verb_bonus + bullet_point_bonus
    
    if job_title:
        total_score = (total_score + job_specific_score) / 2
    
    # Round to integer
    total_score = round(min(100, max(0, total_score)))
    
    # Generate feedback
    feedback = {
        'score': total_score,
        'length': 'Good length' if 300 <= word_count <= 700 else ('Too short' if word_count < 300 else 'Too long'),
        'readability': 'Easy to read' if readability_score > 50 else 'Could be more readable',
        'action_verbs': f'Used {action_verb_count} action verbs' + (' (good)' if action_verb_count >= 10 else ' (needs more)'),
        'bullet_points': f'Contains {bullet_point_count} bullet points' + (' (good)' if bullet_point_count >= 15 else ' (consider adding more)'),
    }
    
    return feedback

# Basic sentiment analysis
def analyze_sentiment(text):
    """Perform basic sentiment analysis on resume text"""
    sia = SentimentIntensityAnalyzer()
    sentiment_scores = sia.polarity_scores(text)
    
    # Interpret scores
    if sentiment_scores['compound'] >= 0.05:
        tone = "positive"
    elif sentiment_scores['compound'] <= -0.05:
        tone = "negative"
    else:
        tone = "neutral"
    
    # Resume-specific advice based on sentiment
    if tone == "positive":
        advice = "Your resume has a positive tone, which is generally good. Make sure achievements are backed by specific metrics."
    elif tone == "negative":
        advice = "Your resume has a somewhat negative tone. Try to frame challenges as opportunities and use more positive language."
    else:
        advice = "Your resume has a neutral tone. Consider adding more dynamic language and achievement-focused content."
    
    return {
        'tone': tone,
        'scores': sentiment_scores,
        'advice': advice
    }


# Create table for credit requests
def create_credit_requests_table():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
CREATE TABLE IF NOT EXISTS credit_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    requested_credits INTEGER NOT NULL,
    status TEXT CHECK( status IN ('pending', 'approved', 'rejected') ) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
)''')

    conn.commit()
    conn.close()

create_credit_requests_table()

# User requests additional credits
@app.route('/request_credits', methods=['POST'])
@login_required
def request_credits():
    user_id = session['user_id']
    requested_credits = int(request.form.get('requested_credits', 0))

    if requested_credits < 1:
        flash("Invalid credit request.", "danger")
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Insert request into DB
    cursor.execute("""
        INSERT INTO credit_requests (user_id, requested_credits, status)
        VALUES (?, ?, 'pending')
    """, (user_id, requested_credits))

    conn.commit()
    conn.close()

    flash("Your request has been submitted for review.", "success")
    return redirect(url_for('dashboard'))


@app.route('/admin')
def admin_panel():
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('Unauthorized access')
        return redirect(url_for('login'))

    return render_template('base_admin.html')  # Create an admin panel template


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Total Users & Admins
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    total_admins = cursor.fetchone()[0]

    # Total resumes generated
    cursor.execute("SELECT COUNT(*) FROM resumes")
    total_resumes = cursor.fetchone()[0]

    # Most used templates
    cursor.execute("""
        SELECT COALESCE(t.name, 'No Template') AS template_name, COUNT(r.id) as count
        FROM resumes r
        LEFT JOIN templates t ON r.template_id = t.id
        GROUP BY template_name
        ORDER BY count DESC
        LIMIT 5
    """)

    top_templates = cursor.fetchall()

    # Common resume errors (missing sections)
    cursor.execute("""
        SELECT section_name, COUNT(*) as count
        FROM resume_sections
        WHERE content = ''
        GROUP BY section_name
        ORDER BY count DESC
        LIMIT 5
    """)
    common_errors = cursor.fetchall()

    # User engagement: Active users in last 24 hours
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id)
        FROM resumes
        WHERE created_at >= datetime('now', '-1 day')
    """)
    active_users_24h = cursor.fetchone()[0]

    conn.close()

    return render_template("admin_dashboard.html",
                           total_users=total_users,
                           total_admins=total_admins,
                           total_resumes=total_resumes,
                           top_templates=top_templates,
                           common_errors=common_errors,
                           active_users_24h=active_users_24h)

# Admin panel for credit requests
@app.route('/admin/credit_requests')
@admin_required
def admin_credit_requests():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT credit_requests.id, users.username, credit_requests.requested_credits, credit_requests.status
        FROM credit_requests
        JOIN users ON credit_requests.user_id = users.id
        WHERE credit_requests.status = 'pending'
    """)
    requests = cursor.fetchall()

    # Admin Analytics
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM credit_requests WHERE status = 'approved'")
    total_credits_approved = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM credit_requests WHERE status = 'pending'")
    pending_requests = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    total_admins = cursor.fetchone()[0]

    conn.close()

    return render_template("admin_credit_requests.html",
                           requests=requests,
                           total_users=total_users,
                           total_credits_approved=total_credits_approved,
                           pending_requests=pending_requests,
                           total_admins=total_admins)


@app.route('/admin/approve_credits/<int:request_id>/<action>')
@admin_required
def approve_credits(request_id, action):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, requested_credits FROM credit_requests WHERE id = ?", (request_id,))
    request = cursor.fetchone()

    if not request:
        logging.warning(f"Admin attempted to access non-existent credit request (ID: {request_id})")
        flash("Request not found.", "danger")
        conn.close()
        return redirect(url_for("admin_credit_requests"))

    user_id, requested_credits = request
    admin_id = session['user_id']  # Get admin ID from session

    if action == "approve":
        cursor.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (requested_credits, user_id))
        cursor.execute("UPDATE credit_requests SET status = 'approved' WHERE id = ?", (request_id,))
        flash(f"Approved {requested_credits} extra credits.", "success")
        logging.info(f"Admin (ID: {admin_id}) approved {requested_credits} credits for User (ID: {user_id})")
    elif action == "reject":
        cursor.execute("UPDATE credit_requests SET status = 'rejected' WHERE id = ?", (request_id,))
        flash("Credit request rejected.", "danger")
        logging.info(f"Admin (ID: {admin_id}) rejected credit request (ID: {request_id}) for User (ID: {user_id})")

    conn.commit()
    conn.close()

    return redirect(url_for("admin_credit_requests"))

# Deduct credit when a user generates a resume
def deduct_credit(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()

    if user and user["credits"] > 0:
        cursor.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    else:
        conn.close()
        return False

### Routes ###

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        # Hash password
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if username or email already exists
        cursor.execute('SELECT * FROM users WHERE username = ? OR email = ?', (username, email))
        if cursor.fetchone():
            conn.close()
            flash('Username or email already exists. Please choose another or login.')
            return redirect(url_for('register'))

        # Insert new user
        cursor.execute(
            'INSERT INTO users (username, email, password) VALUES (?, ?, ?)',
            (username, email, hashed_password)
        )

        conn.commit()
        user_id = cursor.lastrowid
        conn.close()

        # Set session
        session['user_id'] = user_id
        session['username'] = username

        flash('Registration successful!')
        return redirect(url_for('dashboard'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']  # Store role in session

            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))  # Redirect to admin panel
            else:
                return redirect(url_for('dashboard'))  # Redirect to user dashboard

        flash('Invalid username or password')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch user's resumes
    cursor.execute('SELECT * FROM resumes WHERE user_id = ? ORDER BY updated_at DESC', (session['user_id'],))
    resumes = cursor.fetchall()

    # Fetch updated credits
    cursor.execute('SELECT credits FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()

    conn.close()

    updated_credits = user['credits'] if user else 0  # Fallback if user not found

    return render_template('dashboard.html', resumes=resumes, credits=updated_credits)


@app.route('/resume/new', methods=['GET', 'POST'])
@login_required
def new_resume():
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Fetch user credits **BEFORE creating a resume**
            cursor.execute('SELECT credits FROM users WHERE id = ?', (session['user_id'],))
            user = cursor.fetchone()

            if not user or user['credits'] <= 0:
                conn.close()
                flash('Not enough credits to create a resume.', 'danger')
                return redirect(url_for('dashboard'))  # Stop execution if no credits

            title = request.form['title']
            target_job = request.form.get('target_job', '')

            # Deduct one credit BEFORE inserting the resume (within the same transaction)
            new_credits = user['credits'] - 1
            cursor.execute('UPDATE users SET credits = ? WHERE id = ?', (new_credits, session['user_id']))

            # Create new resume
            cursor.execute(
                'INSERT INTO resumes (user_id, title, content, target_job) VALUES (?, ?, ?, ?)',
                (session['user_id'], title, '', target_job)
            )
            resume_id = cursor.lastrowid

            # Initialize default sections
            default_sections = [
                ('Personal Information', ''),
                ('Work Experience', ''),
                ('Education', ''),
                ('Skills', ''),
                ('Additional Sections', '')
            ]
            for section_name, content in default_sections:
                cursor.execute(
                    'INSERT INTO resume_sections (resume_id, section_name, content) VALUES (?, ?, ?)',
                    (resume_id, section_name, content)
                )

            # Commit all changes in one go
            conn.commit()

            # ✅ Ensure session is updated with new credits
            session['credits'] = new_credits

            flash('Resume created successfully! 1 credit deducted.', 'success')
            return redirect(url_for('edit_resume', resume_id=resume_id))

        except Exception as e:
            conn.rollback()  # ❗ Rollback on failure
            flash(f"An error occurred: {str(e)}", 'danger')
            return redirect(url_for('dashboard'))

        finally:
            conn.close()

    return render_template('new_resume.html')

@app.route('/resume/<int:resume_id>')
@login_required
def view_resume(resume_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get resume
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
    resume = cursor.fetchone()

    if not resume:
        conn.close()
        flash('Resume not found or access denied')
        return redirect(url_for('dashboard'))

    # Get sections
    cursor.execute('SELECT * FROM resume_sections WHERE resume_id = ?', (resume_id,))
    sections = cursor.fetchall()

    # Get templates
    cursor.execute('SELECT id, name, description FROM templates')
    templates = cursor.fetchall()

    conn.close()

    return render_template('view_resume.html', resume=resume, sections=sections, templates=templates)

@app.route('/resume/<int:resume_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_resume(resume_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get resume
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
    resume = cursor.fetchone()

    if not resume:
        conn.close()
        flash('Resume not found or access denied')
        return redirect(url_for('dashboard'))

    # Get sections
    cursor.execute('SELECT * FROM resume_sections WHERE resume_id = ?', (resume_id,))
    sections = cursor.fetchall()

    if request.method == 'POST':
        # Update resume title and target job
        title = request.form['title']
        target_job = request.form.get('target_job', '')

        cursor.execute(
            'UPDATE resumes SET title = ?, target_job = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (title, target_job, resume_id)
        )

        # Update sections
        for section in sections:
            section_content = request.form.get(f'section_{section["id"]}', '')
            cursor.execute(
                'UPDATE resume_sections SET content = ? WHERE id = ?',
                (section_content, section["id"])
            )

        # Compile full content for scoring
        full_content = "\n\n".join(request.form.get(f'section_{section["id"]}', '') for section in sections)

        # Score resume
        score_result = score_resume(full_content, target_job)

        # Ensure `score_result` is a dictionary
        if isinstance(score_result, int):
            score_result = {"score": score_result}  # Convert to dictionary

        # Update resume content and score
        cursor.execute(
            'UPDATE resumes SET content = ?, score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (full_content, score_result['score'], resume_id)
        )

        conn.commit()
        conn.close()

        flash('Resume updated successfully!')
        return redirect(url_for('view_resume', resume_id=resume_id))

    conn.close()

    return render_template('edit_resume.html', resume=resume, sections=sections)

@app.route('/resume/<int:resume_id>/chat', methods=['GET', 'POST'])
@login_required
def resume_chat(resume_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get resume
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
    resume = cursor.fetchone()

    if not resume:
        conn.close()
        flash('Resume not found or access denied')
        return redirect(url_for('dashboard'))

    # Get sections
    cursor.execute('SELECT * FROM resume_sections WHERE resume_id = ?', (resume_id,))
    sections = cursor.fetchall()

    # Get or initialize conversation
    cursor.execute('SELECT * FROM conversations WHERE resume_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1',
                  (resume_id, session['user_id']))
    conversation = cursor.fetchone()

    if conversation:
        messages = json.loads(conversation['messages'])
    else:
        # Start new conversation
        initial_message = {
            "role": "assistant",
            "content": "Hi! I'm your resume building assistant. I'll help you create or improve your resume. Let's get started! What part of your resume would you like to work on first?"
        }
        messages = [initial_message]
        cursor.execute(
            'INSERT INTO conversations (user_id, resume_id, messages) VALUES (?, ?, ?)',
            (session['user_id'], resume_id, json.dumps(messages))
        )
        conn.commit()
        conversation_id = cursor.lastrowid

    if request.method == 'POST':
        user_input = request.form['user_input']

        # Add user message
        messages.append({"role": "user", "content": user_input})

        # Create context based on resume content
        context = f"User is working on a resume titled '{resume['title']}'"
        if resume['target_job']:
            context += f" for the position of {resume['target_job']}"

        # Get AI response
        try:
            completion = create_chat_completion(user_input, context)
            ai_response = completion['choices'][0]['message']['content']

            # Add AI message
            messages.append({"role": "assistant", "content": ai_response})

            # Update conversation in database
            if conversation:
                cursor.execute(
                    'UPDATE conversations SET messages = ? WHERE id = ?',
                    (json.dumps(messages), conversation['id'])
                )
            else:
                cursor.execute(
                    'INSERT INTO conversations (user_id, resume_id, messages) VALUES (?, ?, ?)',
                    (session['user_id'], resume_id, json.dumps(messages))
                )

            conn.commit()

            # Check if response contains section updates
            update_section = None
            for section in sections:
                section_name = section['section_name'].lower()
                if section_name in user_input.lower():
                    update_section = section
                    break

            # If discussing a specific section, analyze for potential updates
            if update_section and ("improve" in user_input.lower() or "update" in user_input.lower() or "add" in user_input.lower()):
                # Parse AI suggestions into potential content (simplified approach)
                suggested_content = ai_response

                # For demo purposes, we'll just make this available to the template
                # In a real implementation, you might use NLP to extract structured suggestions
                pass

        except Exception as e:
            flash(f'Error getting response: {str(e)}')

    conn.close()

    return render_template('resume_chat.html', resume=resume, sections=sections, messages=messages)

# Add these parsing functions to your code

def parse_sentiment_analysis(sentiment_result):
    """
    Parse the sentiment analysis result from AI completion into structured format
    with strengths and areas for improvement
    """
    parsed_result = {
        'strengths': [],
        'areas_for_improvement': []
    }
    
    # If it's already a dict with the right structure, return it
    if isinstance(sentiment_result, dict) and 'strengths' in sentiment_result and 'areas_for_improvement' in sentiment_result:
        return sentiment_result
    
    # Handle the existing sentiment analysis output
    if isinstance(sentiment_result, dict) and 'tone' in sentiment_result:
        # Add strength based on tone
        if sentiment_result['tone'] == 'positive':
            parsed_result['strengths'].append("Uses confident, positive language throughout the resume")
        
        # Add the advice as an area for improvement
        if 'advice' in sentiment_result:
            parsed_result['areas_for_improvement'].append(sentiment_result['advice'])
    else:
        # Try to parse the string response from AI
        response_text = str(sentiment_result)
        
        # Look for strengths and weaknesses in the AI response
        strengths_pattern = r"(?:strength|positive|good|excellent|impressive).*?(?:\n|\.)"
        weaknesses_pattern = r"(?:weakness|improve|enhance|consider|suggestion|negative).*?(?:\n|\.)"
        
        strengths = re.findall(strengths_pattern, response_text, re.IGNORECASE)
        weaknesses = re.findall(weaknesses_pattern, response_text, re.IGNORECASE)
        
        # Clean up and add strengths
        for strength in strengths[:3]:  # Limit to 3 strengths
            cleaned = re.sub(r'^[^:]*:', '', strength).strip()
            if cleaned and len(cleaned) > 10:
                parsed_result['strengths'].append(cleaned)
        
        # Clean up and add areas for improvement
        for weakness in weaknesses[:3]:  # Limit to 3 areas
            cleaned = re.sub(r'^[^:]*:', '', weakness).strip()
            if cleaned and len(cleaned) > 10:
                parsed_result['areas_for_improvement'].append(cleaned)
    
    # Add default entries if we couldn't extract enough
    if len(parsed_result['strengths']) < 2:
        parsed_result['strengths'].extend([
            "Your resume has a professional structure and organization",
            "Good presentation of your work history and experience"
        ])
    
    if len(parsed_result['areas_for_improvement']) < 2:
        parsed_result['areas_for_improvement'].extend([
            "Consider adding more quantifiable achievements to strengthen impact",
            "Ensure your resume is tailored to each specific job application"
        ])
    
    return parsed_result

def parse_grammar_formatting(grammar_result):
    """
    Parse the grammar and formatting analysis from AI completion into structured format
    with section feedback and recommendations
    """
    parsed_result = {
        'section_feedback': {},
        'recommendations': []
    }
    
    # If it's already a dict with the right structure, return it
    if isinstance(grammar_result, dict) and 'section_feedback' in grammar_result:
        return grammar_result
    
    # Try to parse the string response from AI
    response_text = str(grammar_result)
    
    # Split the response into sections
    sections = re.split(r'\n\s*\d+\.\s*', response_text)
    
    # Extract recommendations
    recommendations_pattern = r"(?:recommend|suggest|improve|enhance|consider).*?(?:\n|\.)"
    all_recommendations = re.findall(recommendations_pattern, response_text, re.IGNORECASE)
    
    # Clean and add recommendations
    for rec in all_recommendations[:5]:  # Limit to 5 recommendations
        cleaned = re.sub(r'^[^:]*:', '', rec).strip()
        if cleaned and len(cleaned) > 10:
            parsed_result['recommendations'].append(cleaned)
    
    # Create general section feedback
    section_types = ['summary', 'experience', 'education', 'skills', 'projects']
    
    for i, section_type in enumerate(section_types):
        feedback_items = []
        
        # Look for feedback relevant to this section
        section_pattern = fr"(?:{section_type}|{section_type.capitalize()}).*?(?:\n|\.)"
        section_feedback = re.findall(section_pattern, response_text, re.IGNORECASE)
        
        for feedback in section_feedback[:2]:  # Limit to 2 feedbacks per section
            cleaned = re.sub(r'^[^:]*:', '', feedback).strip()
            if cleaned and len(cleaned) > 10:
                feedback_items.append(cleaned)
        
        # Add default feedback if needed
        if not feedback_items:
            if section_type == 'summary':
                feedback_items.append("Ensure your summary highlights your unique value proposition")
            elif section_type == 'experience':
                feedback_items.append("Use strong action verbs and quantify achievements where possible")
            elif section_type == 'education':
                feedback_items.append("List your education in reverse chronological order")
            elif section_type == 'skills':
                feedback_items.append("Group similar skills and prioritize those most relevant to your target job")
            elif section_type == 'projects':
                feedback_items.append("Highlight projects that demonstrate skills relevant to your target position")
        
        parsed_result['section_feedback'][i] = {
            'title': section_type.capitalize(),
            'feedback': feedback_items
        }
    
    # Add default recommendations if needed
    if len(parsed_result['recommendations']) < 3:
        parsed_result['recommendations'].extend([
            "Use consistent formatting for dates, job titles, and section headers",
            "Ensure each bullet point starts with a strong action verb",
            "Keep your resume to 1-2 pages depending on your experience level"
        ])
    
    return parsed_result

def parse_ats_result(ats_result):
    """
    Parse the ATS optimization result from AI completion into structured format
    with keyword match score, missing keywords, and recommendations
    """
    parsed_result = {
        'keyword_match_score': 'N/A',
        'missing_keywords': [],
        'recommendations': []
    }
    
    # If it's already a dict with the right structure, return it
    if isinstance(ats_result, dict) and 'keyword_match_score' in ats_result:
        return ats_result
    
    # Try to parse the string response from AI
    response_text = str(ats_result)
    
    # Extract score
    score_pattern = r'(\d+)(?:\s*\/\s*100|\s*percent|\s*%)'
    score_match = re.search(score_pattern, response_text)
    if score_match:
        parsed_result['keyword_match_score'] = int(score_match.group(1))
    
    # Extract missing keywords
    keywords_section = re.search(r'missing keywords.*?:(.+?)(?:\n\n|\n\d+\.|\Z)', response_text, re.IGNORECASE | re.DOTALL)
    if keywords_section:
        keywords_text = keywords_section.group(1)
        # Extract keywords that might be in a list format or comma-separated
        keywords = re.findall(r'(?:^|\n)\s*[-•*]?\s*([^,\n]+)(?:,|$|\n)', keywords_text)
        if not keywords:
            # Try comma separation
            keywords = [k.strip() for k in keywords_text.split(',')]
        
        # Clean and add keywords
        for keyword in keywords:
            cleaned = keyword.strip()
            if cleaned and len(cleaned) < 30:  # Avoid sentences
                parsed_result['missing_keywords'].append(cleaned)
    
    # Extract recommendations
    recommendations_pattern = r"(?:recommend|suggest|consider|add|include).*?(?:\n|\.)"
    all_recommendations = re.findall(recommendations_pattern, response_text, re.IGNORECASE)
    
    # Clean and add recommendations
    for rec in all_recommendations[:3]:  # Limit to 3 recommendations
        cleaned = re.sub(r'^[^:]*:', '', rec).strip()
        if cleaned and len(cleaned) > 10:
            parsed_result['recommendations'].append(cleaned)
    
    # Add default missing keywords if needed
    if not parsed_result['missing_keywords']:
        parsed_result['missing_keywords'] = ["Keywords not specifically identified"]
    
    # Add default recommendations if needed
    if len(parsed_result['recommendations']) < 2:
        parsed_result['recommendations'].extend([
            "Mirror key terms from the job description in your resume",
            "Use both spelled-out terms and acronyms for technical skills"
        ])
    
    return parsed_result

@app.route('/resume/<int:resume_id>/analyze', methods=['GET', 'POST'])
@login_required
def analyze_resume(resume_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Get resume
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
    resume = cursor.fetchone()
    if not resume:
        conn.close()
        flash('Resume not found or access denied')
        return redirect(url_for('dashboard'))
        
    # Get sections
    cursor.execute('SELECT * FROM resume_sections WHERE resume_id = ?', (resume_id,))
    sections = cursor.fetchall()
    
    analysis = {}
    if request.method == 'POST' or resume['content']:
        content = resume['content']
        job_description = request.form.get('job_description', '')
        
        # Perform analyses
        try:
            # Score resume
            score_result = score_resume(content, resume['target_job'])
            if isinstance(score_result, dict) and 'score' in score_result:
                analysis['score'] = score_result['score']
                # Extract other details if available
                if 'length' in score_result:
                    analysis['details'] = {
                        'length': score_result['length'],
                        'readability': score_result.get('readability', 'N/A'),
                        'action_verbs': score_result.get('action_verbs', 'N/A'),
                        'bullet_points': score_result.get('bullet_points', 'N/A')
                    }
            else:
                analysis['score'] = score_result
                analysis['details'] = {}
        except Exception as e:
            print(f"Error in score_resume: {str(e)}")
            analysis['score'] = "N/A"
            analysis['details'] = {}
            
        try:
            # Sentiment analysis with parsing
            sentiment_result = analyze_sentiment(content)
            parsed_sentiment = parse_sentiment_analysis(sentiment_result)
            analysis['strengths'] = parsed_sentiment['strengths']
            analysis['areas_for_improvement'] = parsed_sentiment['areas_for_improvement']
        except Exception as e:
            print(f"Error in analyze_sentiment: {str(e)}")
            analysis['strengths'] = [
                "Your resume follows a professional format",
                "Your experience is presented in a clear manner"
            ]
            analysis['areas_for_improvement'] = [
                "Consider adding more quantifiable achievements",
                "Ensure your resume is tailored to each job application"
            ]
            
        try:
            # Grammar check with parsing
            grammar_result = check_grammar_formatting(content)
            parsed_grammar = parse_grammar_formatting(grammar_result)
            analysis['section_feedback'] = parsed_grammar['section_feedback']
            analysis['recommendations'] = parsed_grammar['recommendations']
        except Exception as e:
            print(f"Error in check_grammar_formatting: {str(e)}")
            analysis['section_feedback'] = {
                0: {
                    'title': 'Resume Format',
                    'feedback': ["Maintain consistent formatting throughout your resume"]
                }
            }
            analysis['recommendations'] = [
                "Use strong action verbs to begin bullet points",
                "Quantify achievements where possible",
                "Ensure consistent formatting throughout"
            ]
            
        if job_description:
            try:
                # ATS analysis with parsing
                ats_result = optimize_for_ats(content, job_description)
                parsed_ats = parse_ats_result(ats_result)
                analysis['ats'] = {
                    'keyword_match_score': parsed_ats['keyword_match_score'],
                    'missing_keywords': parsed_ats['missing_keywords'],
                    'recommendations': parsed_ats['recommendations']
                }
                
                # Add ATS recommendations to general recommendations if not many exist
                if len(analysis['recommendations']) < 3:
                    analysis['recommendations'].extend(parsed_ats['recommendations'])
            except Exception as e:
                print(f"Error in optimize_for_ats: {str(e)}")
                analysis['ats'] = {
                    'keyword_match_score': 'N/A',
                    'missing_keywords': ["Could not analyze keywords"],
                    'recommendations': ["Ensure your resume contains key terms from the job description"]
                }
    
    conn.close()
    # Ensure all expected keys exist in analysis
    if 'strengths' not in analysis:
        analysis['strengths'] = []
    if 'areas_for_improvement' not in analysis:
        analysis['areas_for_improvement'] = []
    if 'recommendations' not in analysis:
        analysis['recommendations'] = []
    if 'section_feedback' not in analysis:
        analysis['section_feedback'] = {}
    if 'details' not in analysis:
        analysis['details'] = {}
        
    return render_template('analyze_resume.html', resume=resume, sections=sections, analysis=analysis)

@app.route('/resume/<int:resume_id>/export', methods=['GET', 'POST'])
@login_required
def export_resume(resume_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get resume
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
    resume = cursor.fetchone()

    if not resume:
        conn.close()
        flash('Resume not found or access denied')
        return redirect(url_for('dashboard'))

    # Get sections
    cursor.execute('SELECT * FROM resume_sections WHERE resume_id = ?', (resume_id,))
    sections = cursor.fetchall()

    # Get templates
    cursor.execute('SELECT * FROM templates')
    templates = cursor.fetchall()

    # Check if this is a GET request with format_type parameter (for iframe direct loading)
    is_preview = False
    format_type = None
    template_id = 1  # Default template

    if request.method == 'GET' and 'format_type' in request.args:
        format_type = request.args.get('format_type')
        template_id = request.args.get('template_id', 1)
        is_preview = request.args.get('preview', 'false') == 'true'

    if request.method == 'POST' or format_type:
        if not format_type:  # If not set from GET params
            format_type = request.form['format_type']
            template_id = request.form.get('template_id', 1)
            is_preview = request.form.get('preview', 'false') == 'true'

        # Get selected template
        cursor.execute('SELECT * FROM templates WHERE id = ?', (template_id,))
        template = cursor.fetchone()

        # Prepare content
        content = ""
        for section in sections:
            if section['content'].strip():
                content += f"<div class='section'><h2>{section['section_name']}</h2><div>{section['content']}</div></div>"

        # Extract personal info for header
        personal_info = ""
        for section in sections:
            if section['section_name'] == 'Personal Information':
                personal_info = section['content']
                break

        # Parse name and contact from personal info (simple implementation)
        name = "John Doe"  # Default
        contact = "email@example.com • (555) 123-4567"  # Default

        lines = personal_info.split('\n')
        if lines and lines[0].strip():
            name = lines[0].strip()
        if len(lines) > 1:
            contact = ' • '.join(line.strip() for line in lines[1:3] if line.strip())

        # Format HTML based on template
        html_structure = template['html_structure']
        html_content = html_structure.replace('{name}', name).replace('{contact}', contact).replace('{sections}', content)

        # Add template CSS
        css = template['css_content']
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{resume['title']}</title>
            <style>
                {css}
                @page {{ size: letter; margin: 0.75in; }}
                body {{ margin: 0; padding: 0; }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        if format_type == 'pdf':
            try:
                # Import required modules here to ensure COM initialization happens correctly
                import pythoncom

                # Initialize COM for this thread - this fixes the CoInitialize error
                pythoncom.CoInitialize()

                try:
                    # Create a new Document
                    doc = Document()

                    # Add name as title
                    doc.add_heading(name, 0)

                    # Add contact info
                    doc.add_paragraph(contact)

                    # Add sections
                    for section in sections:
                        if section['content'].strip():
                            doc.add_heading(section['section_name'], 1)
                            doc.add_paragraph(section['content'])

                    # Create temporary files for the conversion process
                    with tempfile.TemporaryDirectory() as temp_dir:
                        # Create temporary file paths
                        docx_path = os.path.join(temp_dir, f"{resume['title']}.docx")
                        pdf_path = os.path.join(temp_dir, f"{resume['title']}.pdf")

                        # Save the DOCX to the temporary location
                        doc.save(docx_path)

                        # Convert DOCX to PDF
                        convert(docx_path, pdf_path)

                        # Read the PDF file
                        with open(pdf_path, 'rb') as pdf_file:
                            pdf_content = pdf_file.read()

                        # Return the PDF file as a response
                        response = make_response(pdf_content)
                        response.headers['Content-Type'] = 'application/pdf'

                        # Set Content-Disposition based on whether this is a preview or download
                        if is_preview:
                            # For preview, use 'inline' to display in browser
                            response.headers['Content-Disposition'] = f'inline; filename={resume["title"]}.pdf'
                        else:
                            # For download, use 'attachment' to prompt download
                            response.headers['Content-Disposition'] = f'attachment; filename={resume["title"]}.pdf'

                        conn.close()
                        return response
                finally:
                    # Always uninitialize COM when done, even if an exception occurred
                    pythoncom.CoUninitialize()

            except Exception as e:
                flash(f'Error generating PDF: {str(e)}')
                conn.close()
                return redirect(url_for('export_resume', resume_id=resume_id))

        elif format_type == 'docx':
            try:
                # Create a new Document
                doc = Document()

                # Add name as title
                doc.add_heading(name, 0)

                # Add contact info
                doc.add_paragraph(contact)

                # Add sections
                for section in sections:
                    if section['content'].strip():
                        doc.add_heading(section['section_name'], 1)
                        doc.add_paragraph(section['content'])

                # Save to BytesIO
                file_stream = BytesIO()
                doc.save(file_stream)
                file_stream.seek(0)

                response = make_response(file_stream.read())
                response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                response.headers['Content-Disposition'] = f'attachment; filename={resume["title"]}.docx'

                conn.close()
                return response
            except Exception as e:
                flash(f'Error generating DOCX: {str(e)}')
                conn.close()
                return redirect(url_for('export_resume', resume_id=resume_id))

        else:
            # HTML preview
            conn.close()
            return render_template('preview_resume.html', resume=resume, html_content=full_html)

    # If we get here, it's a GET request without format_type, so show the export options page
    conn.close()
    return render_template('export_resume.html', resume=resume, templates=templates)

@app.route('/api/get_response', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_get_response():
    user_input = request.json.get('prompt')
    resume_id = request.json.get('resume_id')

    context = ""
    if resume_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, session['user_id']))
        resume = cursor.fetchone()

        if resume:
            context = f"User is working on a resume titled '{resume['title']}'"
            if resume['target_job']:
                context += f" for the position of {resume['target_job']}"

        conn.close()

    try:
        completion = create_chat_completion(user_input, context)
        response_text = completion['choices'][0]['message']['content']
        return jsonify({'response': response_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/check_grammar', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_check_grammar():
    text = request.json.get('text', '')

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    result = check_grammar_formatting(text)
    return jsonify({'result': result})

@app.route('/api/optimize_ats', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_optimize_ats():
    resume_text = request.json.get('resume_text', '')
    job_description = request.json.get('job_description', '')

    if not resume_text or not job_description:
        return jsonify({'error': 'Missing required parameters'}), 400

    result = optimize_for_ats(resume_text, job_description)
    return jsonify({'result': result})

@app.route('/api/score_resume', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_score_resume():
    resume_text = request.json.get('resume_text', '')
    job_title = request.json.get('job_title', '')

    if not resume_text:
        return jsonify({'error': 'No resume text provided'}), 400

    result = score_resume(resume_text, job_title)
    return jsonify({'result': result})

@app.route('/api/analyze_sentiment', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_analyze_sentiment():
    text = request.json.get('text', '')

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    result = analyze_sentiment(text)
    return jsonify({'result': result})

@app.route('/get_credits')
def get_credits():
    """Fetch updated credits dynamically."""
    if 'user_id' not in session:
        return jsonify({"credits": 0})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT credits FROM users WHERE id = ?", (session['user_id'],))
    user = cursor.fetchone()
    conn.close()

    return jsonify({"credits": user['credits'] if user else 0})

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500


def initiliaze_scheduler():
    reset_daily_credits()
    
if __name__ == '__main__':
    app.run(debug=True, host='localhost', port=5000)
