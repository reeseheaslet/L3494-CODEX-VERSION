import os
import json
import secrets
import smtplib
import threading
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from db import get_db, init_db
import firebase_admin
from firebase_admin import credentials, messaging as fcm_messaging

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'union-dev-secret-change-in-prod')
app.config['WTF_CSRF_SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'union-dev-secret-change-in-prod')
csrf = CSRFProtect(app)

# Initialize Firebase Admin SDK
_firebase_initialized = False
def init_firebase():
    global _firebase_initialized
    if not _firebase_initialized:
        try:
            cred_path = os.path.join(os.path.dirname(__file__), 'firebase-service-account.json')
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            print("[Firebase] Admin SDK initialized")
        except Exception as e:
            print(f"[Firebase] Failed to initialize: {e}")

init_firebase()

@app.context_processor
def inject_notifications():
    if session.get('role') in ('admin', 'super_admin'):
        db = get_db()
        unread_contact = db.execute("SELECT COUNT(*) FROM contact_messages WHERE viewed_at IS NULL").fetchone()[0]
        pending_members = db.execute("SELECT COUNT(*) FROM members WHERE status = 'pending'").fetchone()[0]
        db.close()
        return {
            'unread_contact_count': unread_contact,
            'pending_member_count': pending_members,
            'total_notification_count': unread_contact + pending_members
        }
    return {'unread_contact_count': 0, 'pending_member_count': 0, 'total_notification_count': 0}

@app.context_processor
def inject_next_meeting():
    try:
        db = get_db()
        from datetime import date
        today = date.today().isoformat()
        meeting = db.execute(
            "SELECT * FROM general_meetings WHERE meeting_date >= ? ORDER BY meeting_date ASC, meeting_time ASC LIMIT 1",
            (today,)
        ).fetchone()
        db.close()
        return {'next_gm_meeting': dict(meeting) if meeting else None}
    except Exception:
        return {'next_gm_meeting': None}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Email configuration — Gmail SMTP
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('SMTP_FROM', '')
BASE_URL = os.environ.get('BASE_URL', 'https://local3494.pythonanywhere.com')

AGENTS_STATUS_FILE = "/home/reese/.openclaw/workspace/agents_status.json"
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif'}
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static/images/family')
MEMBER_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static/images/members')
PROFILE_PHOTO_FOLDER = os.path.join(os.path.dirname(__file__), 'static/images/profiles')
DISCUSSION_ATTACHMENTS_FOLDER = os.path.join(os.path.dirname(__file__), 'static/images/discussion_attachments')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_profile_photo(member_id):
    """Jinja2 helper to get profile photo URL for a member"""
    if not member_id:
        return '/static/images/default-avatar.png'
    
    db = get_db()
    member = db.execute("SELECT profile_photo FROM members WHERE id = ?", (member_id,)).fetchone()
    db.close()
    
    if member and member['profile_photo']:
        return f'/static/images/profiles/{member["profile_photo"]}'
    return '/static/images/default-avatar.png'

@app.template_filter('friendly_time')
def friendly_time_filter(value):
    """Format a datetime string like '2026-03-29 21:54:00' into 'Mar 29, 2026 · 9:54 PM'"""
    if not value:
        return ''
    try:
        from datetime import datetime
        dt = datetime.strptime(str(value)[:16], '%Y-%m-%d %H:%M')
        return dt.strftime('%b %-d, %Y · %-I:%M %p')
    except Exception:
        return str(value)[:16]

import markupsafe

@app.template_filter('highlight_mentions')
def highlight_mentions_filter(value):
    """Wrap @username in a styled span for visual highlighting."""
    import re
    if not value:
        return value
    escaped = markupsafe.escape(value)
    highlighted = re.sub(
        r'@(\w+)',
        r'<span class="mention-tag">@\1</span>',
        str(escaped)
    )
    return markupsafe.Markup(highlighted)

# Register Jinja2 global helpers
app.jinja_env.globals.update(get_profile_photo=get_profile_photo)

def send_email(to_email, subject, html_body):
    """
    Send an email via Gmail SMTP, or log to debug file if not configured.

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_body: HTML body of the email

    Returns:
        True on success, False on failure (never raises)
    """
    try:
        if not SMTP_USER:
            # Dev fallback: log to file
            debug_log = '/tmp/union_email_debug.log'
            with open(debug_log, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"TO: {to_email}\n")
                f.write(f"SUBJECT: {subject}\n")
                f.write(f"TIMESTAMP: {datetime.now().isoformat()}\n")
                f.write(f"{'='*60}\n")
                f.write(html_body)
                f.write(f"\n\n")
            return True

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        return True

    except Exception as e:
        print(f"[ERROR] Failed to send email to {to_email}: {str(e)}")
        return False

def update_agent_status(agent_id, status, result=None):
    """Update this agent's status in agents_status.json"""
    try:
        with open(AGENTS_STATUS_FILE, 'r') as f:
            data = json.load(f)
        for agent in data.get('agents', []):
            if agent['id'] == agent_id:
                agent['status'] = status
                if result:
                    agent['result'] = result
                if status in ('done', 'error'):
                    agent['finished'] = datetime.now().isoformat()[:19]
        with open(AGENTS_STATUS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# Helper functions for member auth and role checking
def require_login():
    """Check if user is logged in, redirect if not"""
    if 'user_id' not in session:
        flash('Please log in to access this page.', 'error')
        return False
    return True

def get_member_role():
    """Get current member's role from session"""
    return session.get('role', 'member')

def is_board_member():
    """Check if current user is board_member, admin, or super_admin"""
    role = get_member_role()
    return role in ('board_member', 'admin', 'super_admin')

def get_member_id():
    """Get current member's ID from session"""
    return session.get('user_id', None)

def get_member_name():
    """Get current member's name from session"""
    return session.get('username', '')

def is_admin():
    """Check if current user is admin or super_admin"""
    role = get_member_role()
    return role in ('admin', 'super_admin')

def is_super_admin():
    """Check if current user is super_admin"""
    role = get_member_role()
    return role == 'super_admin'

def require_role(*allowed_roles):
    """Decorator to require specific roles"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to access this page.', 'error')
                return redirect(url_for('login'))
            if session.get('role') not in allowed_roles:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('members'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def require_family_access():
    """Check if user can access family portal (any logged in user)"""
    if 'user_id' not in session:
        flash('Please log in to access the family portal.', 'error')
        return False
    return True

def require_member_access():
    """Check if user can access member portal (non-family users only)"""
    if 'user_id' not in session:
        flash('Please log in to access the member portal.', 'error')
        return False
    if session.get('role') == 'family':
        flash('Family members cannot access the member portal.', 'error')
        return False
    return True

@app.route('/')
def index():
    db = get_db()
    from datetime import date
    today = date.today().isoformat()
    upcoming_events = db.execute(
        """SELECT * FROM events WHERE visibility LIKE '%public_homepage%' AND event_date >= ? ORDER BY event_date ASC LIMIT 3""",
        (today,)
    ).fetchall()
    db.close()
    return render_template('index.html', upcoming_events=[dict(e) for e in upcoming_events])

@app.route('/events')
def events():
    """Show public events on homepage (filtered by visibility)"""
    db = get_db()
    
    # Get events visible on public homepage (LIKE for comma-separated values)
    public_events = db.execute("""
        SELECT * FROM events 
        WHERE visibility LIKE '%public_homepage%' 
        ORDER BY event_date ASC
    """).fetchall()
    
    db.close()
    
    return render_template('events.html', events=[dict(e) for e in public_events])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        db = get_db()
        user = db.execute("SELECT * FROM members WHERE LOWER(email)=?", (email,)).fetchone()
        # Handle both hashed and plaintext passwords (auto-upgrades plaintext on login)
        password_ok = False
        if user:
            try:
                password_ok = check_password_hash(user['password'], password)
            except Exception:
                password_ok = False
            if not password_ok and user['password'] == password:
                # Plaintext match — upgrade to hashed
                password_ok = True
                db2 = get_db()
                db2.execute("UPDATE members SET password=? WHERE id=?", (generate_password_hash(password), user['id']))
                db2.commit()
                db2.close()
        db.close()
        if user and password_ok:
            # Check if member is active
            if user['status'] != 'active':
                flash('Your account is pending approval. Please contact your union representative.', 'error')
                return render_template('login.html')
            session['user_id'] = user['id']
            session['username'] = user['name']
            session['role'] = user['role']
            session['member_role'] = user['role']
            session['linked_member_id'] = user['linked_member_id']
            session['status'] = user['status']
            session['user_type'] = 'family' if user['role'] == 'family' else 'member'
            flash('Welcome back!', 'success')
            # Redirect family members to family portal, others to member portal
            if user['role'] == 'family':
                return redirect(url_for('family_home'))
            else:
                return redirect(url_for('members'))
        else:
            flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        db = get_db()
        user = db.execute("SELECT * FROM members WHERE LOWER(email)=?", (email,)).fetchone()
        if user:
            # Prevent duplicate emails: skip if a valid token was already sent in the last 5 minutes
            recent = db.execute("""
                SELECT id FROM password_reset_tokens
                WHERE member_id = ? AND used = 0
                AND expires_at > datetime('now')
                AND created_at > datetime('now', '-5 minutes')
            """, (user['id'],)).fetchone()
            if recent:
                db.close()
                flash('If that email is in our system, you\'ll receive a reset link shortly.', 'success')
                return redirect(url_for('login'))
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            db.execute("INSERT INTO password_reset_tokens (member_id, token, expires_at) VALUES (?, ?, ?)",
                       (user['id'], token, expires_at))
            db.commit()
            reset_url = f"{BASE_URL}/reset-password/{token}"
            html_body = f"""
            <p>Hi {user['name']},</p>
            <p>You requested a password reset for your Local 3494 account.</p>
            <p><a href="{reset_url}">Click here to reset your password</a></p>
            <p>This link expires in 1 hour. If you didn't request this, you can ignore this email.</p>
            <p>— Local 3494</p>
            """
            send_email(user['email'], 'Password Reset — Local 3494', html_body)
        db.close()
        # Always show success (don't reveal if email exists)
        flash('If that email is in our system, you\'ll receive a reset link shortly.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    row = db.execute("""
        SELECT prt.*, m.name FROM password_reset_tokens prt
        JOIN members m ON m.id = prt.member_id
        WHERE prt.token=? AND prt.used=0 AND prt.expires_at > datetime('now')
    """, (token,)).fetchone()
    if not row:
        db.close()
        flash('This reset link is invalid or has expired.', 'error')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        password_confirm = request.form.get('password_confirm', '').strip()
        if not password or len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token)
        if password != password_confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)
        db.execute("UPDATE members SET password=? WHERE id=?",
                   (generate_password_hash(password), row['member_id']))
        db.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))
        db.commit()
        db.close()
        flash('Password updated! You can now log in.', 'success')
        return redirect(url_for('login'))
    db.close()
    return render_template('reset_password.html', token=token)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Member registration page"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        password_confirm = request.form.get('password_confirm', '').strip()
        
        # Validation
        if not all([name, email, password]):
            flash('Please fill in all required fields.', 'error')
            return render_template('register.html')
        
        if password != password_confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('register.html')
        
        db = get_db()
        
        # Check if email already exists
        existing = db.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
        if existing:
            flash('An account with this email already exists.', 'error')
            db.close()
            return render_template('register.html')
        
        # Insert new member with 'pending' status
        try:
            db.execute("""
                INSERT INTO members (name, email, password, role, status)
                VALUES (?, ?, ?, ?, ?)
            """, (name, email, generate_password_hash(password), 'member', 'pending'))
            db.commit()
            
            # Notify all super_admins of new registration
            try:
                admins = db.execute(
                    "SELECT email, name FROM members WHERE role = 'super_admin' AND status = 'active'"
                ).fetchall()
                for admin in admins:
                    admin_html = f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #B91C1C;">New Member Registration – Approval Needed</h2>
                            <p>Hello {admin['name']},</p>
                            <p><strong>{name}</strong> ({email}) has registered for Local 3494 and is pending your approval.</p>
                            <p style="margin-top: 20px;">
                                <a href="{BASE_URL}/admin?tab=pending" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                                    Review in Admin Panel
                                </a>
                            </p>
                            <p>—<br>Local 3494 Admin System</p>
                        </div>
                    </body>
                    </html>
                    """
                    send_email(
                        admin['email'],
                        f"New member registration: {name}",
                        admin_html
                    )
            except Exception:
                pass  # Don't block registration if notification fails
            
            db.close()
            flash('Registration successful! Your account is pending approval. Please check back soon.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.close()
            flash('An error occurred during registration. Please try again.', 'error')
            return render_template('register.html')
    
    return render_template('register.html')

@app.route('/members')
def members():
    if not require_member_access():
        return redirect(url_for('family_home') if session.get('role') == 'family' else url_for('login'))
    
    from datetime import date
    today = date.today().isoformat()
    db = get_db()
    upcoming_events = db.execute("""
        SELECT id, title, description, event_date, event_time, location, signup_enabled
        FROM events
        WHERE (visibility LIKE '%member_portal%' OR visibility LIKE '%public_homepage%')
          AND event_date >= ?
        ORDER BY event_date ASC
        LIMIT 6
    """, (today,)).fetchall()
    db.close()
    
    return render_template('members.html',
        logged_in=True,
        username=session.get('username', ''),
        role=session.get('role', 'member'),
        upcoming_events=[dict(e) for e in upcoming_events])

@app.route('/about')
def about():
    return render_template('about.html') if os.path.exists(os.path.join(app.template_folder, 'about.html')) else render_template('index.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        # Honeypot check — bots fill this in, humans don't see it
        if request.form.get('website_url', ''):
            return render_template('contact.html', success=True)  # silently fake success
        
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        preferred_contact = request.form.get('preferred_contact', 'email')
        subject = request.form.get('subject', '').strip()
        message = request.form.get('message', '').strip()
        
        if not name or not email or not message:
            return render_template('contact.html', error='Please fill in all required fields.')
        
        db = get_db()
        db.execute("""
            INSERT INTO contact_messages (name, email, phone, preferred_contact, subject, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, email, phone, preferred_contact, subject, message))
        db.commit()
        db.close()
        return render_template('contact.html', success=True)
    
    return render_template('contact.html')

@app.route('/store')
def store():
    db = get_db()
    items = db.execute(
        "SELECT * FROM store_items WHERE store_type='public' AND active=1 ORDER BY id ASC"
    ).fetchall()
    db.close()
    items_list = [dict(item) for item in items]
    
    # Parse JSON sizes for template
    for item in items_list:
        if item['sizes']:
            try:
                item['sizes_list'] = json.loads(item['sizes'])
            except:
                item['sizes_list'] = []
        else:
            item['sizes_list'] = []
    
    return render_template('store.html', items=items_list)

@app.route('/store/order', methods=['POST'])
def store_order():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    payment_method = request.form.get('payment_method', '').strip()
    
    items_json = request.form.get('items_json', '[]')
    try:
        items = json.loads(items_json)
    except:
        flash('Error processing order. Please try again.', 'error')
        return redirect(url_for('store'))
    
    if not name or not email or not payment_method:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('store'))
    
    if not items:
        flash('Your order is empty.', 'error')
        return redirect(url_for('store'))
    
    db = get_db()
    order_ids = []
    total = 0
    
    for item in items:
        item_id = item.get('item_id')
        size = item.get('size')
        quantity = item.get('quantity', 1)
        
        # Fetch item details
        item_detail = db.execute("SELECT * FROM store_items WHERE id=? AND store_type='public' AND active=1", (item_id,)).fetchone()
        if not item_detail:
            continue
        
        price_per = item_detail['price']
        total_price = price_per * quantity
        total += total_price
        
        # Insert order record
        db.execute("""
            INSERT INTO store_orders (item_id, item_name, size, quantity, price, customer_name, customer_email, customer_phone, payment_method, order_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, item_detail['name'], size, quantity, total_price, name, email, phone, payment_method, 'pending'))
        db.commit()
        
        # Get the inserted order ID
        cursor = db.execute("SELECT last_insert_rowid()")
        order_id = cursor.fetchone()[0]
        order_ids.append(order_id)
    
    db.close()
    
    if not order_ids:
        flash('Error creating order. Please try again.', 'error')
        return redirect(url_for('store'))
    
    # Render confirmation
    return render_template('store_confirmation.html', 
        name=name, 
        email=email, 
        phone=phone, 
        payment_method=payment_method, 
        items=items, 
        total=total, 
        order_ids=order_ids)

@app.route('/gallery')
def gallery():
    db = get_db()
    images = db.execute(
        "SELECT * FROM gallery_images ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    db.close()
    images_list = [dict(img) for img in images]
    return render_template('gallery.html', images=images_list)

@app.route('/family/store')
def family_store():
    """Family store with items for firefighter families"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    # Get items visible to family members (public + family-specific + members_family items)
    items = db.execute(
        "SELECT * FROM store_items WHERE store_type IN ('public', 'family', 'members_family') AND active=1 ORDER BY id ASC"
    ).fetchall()
    db.close()
    
    items_list = [dict(item) for item in items]
    
    # Parse JSON sizes for template
    for item in items_list:
        if item['sizes']:
            try:
                item['sizes_list'] = json.loads(item['sizes'])
            except:
                item['sizes_list'] = []
        else:
            item['sizes_list'] = []
    
    return render_template('family_store.html', items=items_list)

@app.route('/shift-calendar')
def shift_calendar():
    if 'user_id' not in session:
        flash('Please log in to view the shift calendar.', 'error')
        return redirect(url_for('login'))
    
    # Fetch public events from database
    db = get_db()
    events = db.execute(
        "SELECT * FROM events WHERE visibility LIKE '%public_homepage%' ORDER BY event_date ASC"
    ).fetchall()
    db.close()
    
    # Convert to list of dicts for easier template access
    events_list = [dict(event) for event in events]
    
    return render_template('shift_calendar.html', events=events_list) if os.path.exists(os.path.join(app.template_folder, 'shift_calendar.html')) else render_template('members.html')

@app.route('/family/peer-support')
def family_peer_support():
    """Peer support resources for family members"""
    if not require_family_access():
        return redirect(url_for('login'))
    return render_template('family_peer_support.html')

# ========== PART B: Family Member Management ==========
@app.route('/members/family')
def members_family():
    """Manage family members linked to current user"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') == 'family':
        flash('Family members cannot manage other family accounts.', 'error')
        return redirect(url_for('family_home'))
    
    db = get_db()
    member_id = get_member_id()
    
    # Get all family members linked to this user
    family_members = db.execute(
        "SELECT id, name, email FROM members WHERE linked_member_id=?",
        (member_id,)
    ).fetchall()
    
    # Get pending invitations
    pending_invitation = db.execute(
        "SELECT * FROM family_invitations WHERE firefighter_id=? AND status='pending'",
        (member_id,)
    ).fetchone()
    
    db.close()
    
    return render_template('members_family.html',
        family_members=[dict(m) for m in family_members],
        pending_invitation=dict(pending_invitation) if pending_invitation else None,
        username=get_member_name(),
        role=session.get('role', 'member'))

@app.route('/members/family/add', methods=['POST'])
def members_family_add():
    """Add a new family member linked to current user"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') == 'family':
        flash('Family members cannot add other family accounts.', 'error')
        return redirect(url_for('family_home'))
    
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    
    # Validation
    if not all([name, email, password]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('members_family'))
    
    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'error')
        return redirect(url_for('members_family'))
    
    db = get_db()
    
    # Check if email already exists
    existing = db.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
    if existing:
        flash('An account with this email already exists.', 'error')
        db.close()
        return redirect(url_for('members_family'))
    
    # Create new family member account
    try:
        db.execute("""
            INSERT INTO members (name, email, password, role, status, linked_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, email, generate_password_hash(password), 'family', 'pending', get_member_id()))
        db.commit()
        db.close()
        flash(f'Family member {name} added successfully!', 'success')
        return redirect(url_for('members_family'))
    except Exception as e:
        db.close()
        flash('An error occurred while adding the family member. Please try again.', 'error')
        return redirect(url_for('members_family'))

@app.route('/members/family/remove', methods=['POST'])
def members_family_remove():
    """Remove a family member"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') == 'family':
        flash('Family members cannot remove other family accounts.', 'error')
        return redirect(url_for('family_home'))
    
    family_member_id = request.form.get('family_member_id', '').strip()
    
    if not family_member_id:
        flash('Invalid family member.', 'error')
        return redirect(url_for('members_family'))
    
    db = get_db()
    
    # Verify this family member belongs to the current user
    member = db.execute(
        "SELECT * FROM members WHERE id=? AND linked_member_id=?",
        (family_member_id, get_member_id())
    ).fetchone()
    
    if not member:
        flash('Family member not found.', 'error')
        db.close()
        return redirect(url_for('members_family'))
    
    # Delete the family member account
    db.execute("DELETE FROM members WHERE id=?", (family_member_id,))
    db.commit()
    db.close()
    
    flash(f'Family member removed.', 'success')
    return redirect(url_for('members_family'))


# ========== PHASE 3: New Email Invitation Routes ==========

@app.route('/members/family/invite', methods=['POST'])
def members_family_invite():
    """Send an email invitation for a spouse/partner to join"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') == 'family':
        flash('Family members cannot invite other family accounts.', 'error')
        return redirect(url_for('family_home'))
    
    invitee_name = request.form.get('invitee_name', '').strip()
    invitee_email = request.form.get('invitee_email', '').strip()
    
    # Validation
    if not all([invitee_name, invitee_email]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('members_family'))
    
    db = get_db()
    member_id = get_member_id()
    
    # Check: Does this firefighter already have an active family account?
    active_family = db.execute(
        "SELECT id FROM members WHERE linked_member_id=? AND role='family' AND status='active'",
        (member_id,)
    ).fetchone()
    
    if active_family:
        flash('You already have an active family account linked. Only one family account per firefighter is allowed.', 'error')
        db.close()
        return redirect(url_for('members_family'))
    
    # Check: Does a pending invitation already exist?
    existing_invite = db.execute(
        "SELECT id, token FROM family_invitations WHERE firefighter_id=? AND status='pending'",
        (member_id,)
    ).fetchone()
    
    if existing_invite:
        # Resend email to existing invitation
        token = existing_invite['token']
        invitation = db.execute(
            "SELECT * FROM family_invitations WHERE id=?",
            (existing_invite['id'],)
        ).fetchone()
    else:
        # Create new invitation
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(days=7)).isoformat()
        
        db.execute("""
            INSERT INTO family_invitations 
            (firefighter_id, invitee_name, invitee_email, token, status, expires_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (member_id, invitee_name, invitee_email, token, expires_at))
        
        # Mark that this firefighter has a pending invite
        db.execute("UPDATE members SET has_family_invite=1 WHERE id=?", (member_id,))
        db.commit()
        
        invitation = db.execute(
            "SELECT * FROM family_invitations WHERE token=?",
            (token,)
        ).fetchone()
    
    # Send invitation email
    register_url = f"{BASE_URL}/family/register/{token}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #B91C1C;">Welcome to the Local 3494 Family Portal</h2>
            
            <p>Hello {invitee_name},</p>
            
            <p>You've been invited by your firefighter to join the <strong>Local 3494 Family Portal</strong>. 
            This is a private community where families can connect, share resources, discuss important topics, 
            and stay informed about union events and activities.</p>
            
            <p><strong>To complete your registration, click the button below:</strong></p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{register_url}" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                    Create Your Account
                </a>
            </div>
            
            <p style="font-size: 12px; color: #666;">Or copy and paste this link in your browser:<br>
            <code>{register_url}</code></p>
            
            <p style="font-size: 12px; color: #666;"><strong>This invitation expires in 7 days.</strong></p>
            
            <p>If you have any questions, please contact your firefighter.</p>
            
            <p>Welcome to the Local 3494 family!</p>
            
            <p>—<br>
            <strong>Local 3494 Family Portal</strong></p>
        </div>
    </body>
    </html>
    """
    
    send_email(
        invitee_email,
        "You're invited to join the Local 3494 Family Portal",
        html_body
    )
    
    flash(f'Invitation sent to {invitee_email}!', 'success')
    db.close()
    return redirect(url_for('members_family'))


@app.route('/members/family/invite/cancel', methods=['POST'])
def members_family_invite_cancel():
    """Cancel a pending invitation"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') == 'family':
        flash('Family members cannot manage invitations.', 'error')
        return redirect(url_for('family_home'))
    
    invitation_id = request.form.get('invitation_id', '').strip()
    
    if not invitation_id:
        flash('Invalid invitation.', 'error')
        return redirect(url_for('members_family'))
    
    db = get_db()
    member_id = get_member_id()
    
    # Verify this invitation belongs to current user
    invitation = db.execute(
        "SELECT * FROM family_invitations WHERE id=? AND firefighter_id=?",
        (invitation_id, member_id)
    ).fetchone()
    
    if not invitation:
        flash('Invitation not found.', 'error')
        db.close()
        return redirect(url_for('members_family'))
    
    # Cancel the invitation
    db.execute("UPDATE family_invitations SET status='cancelled' WHERE id=?", (invitation_id,))
    
    # Reset has_family_invite flag
    db.execute("UPDATE members SET has_family_invite=0 WHERE id=?", (member_id,))
    db.commit()
    db.close()
    
    flash('Invitation cancelled.', 'success')
    return redirect(url_for('members_family'))


@app.route('/family/register/<token>', methods=['GET', 'POST'])
def family_register(token):
    """Registration page for invited family members"""
    db = get_db()
    
    # Look up invitation by token
    invitation = db.execute(
        "SELECT * FROM family_invitations WHERE token=?",
        (token,)
    ).fetchone()
    
    if not invitation:
        db.close()
        return render_template('family_register.html',
            error='This invitation is invalid or has expired.',
            token=token,
            invitation=None)
    
    if invitation['status'] != 'pending':
        db.close()
        return render_template('family_register.html',
            error='This invitation is invalid or has expired.',
            token=token,
            invitation=None)
    
    # Check if expired
    if datetime.fromisoformat(invitation['expires_at']) < datetime.now():
        db.execute("UPDATE family_invitations SET status='expired' WHERE id=?", (invitation['id'],))
        db.commit()
        db.close()
        return render_template('family_register.html',
            error='This invitation has expired. Please contact your firefighter for a new invitation.',
            token=token,
            invitation=None)
    
    if request.method == 'GET':
        # Show registration form pre-filled with invitation data
        db.close()
        return render_template('family_register.html',
            token=token,
            invitation=dict(invitation),
            error=None)
    
    # POST: Process registration — re-validate token expiration
    invitation = db.execute(
        "SELECT * FROM family_invitations WHERE token=? AND status='pending'",
        (token,)
    ).fetchone()
    if not invitation or datetime.fromisoformat(invitation['expires_at']) < datetime.now():
        db.close()
        return render_template('family_register.html',
            error='This invitation has expired. Please contact your firefighter for a new invitation.',
            token=token,
            invitation=None)

    name = request.form.get('name', '').strip()
    password = request.form.get('password', '').strip()
    password_confirm = request.form.get('password_confirm', '').strip()
    
    # Validation
    if not all([name, password, password_confirm]):
        db.close()
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('family_register', token=token))
    
    if password != password_confirm:
        db.close()
        flash('Passwords do not match.', 'error')
        return redirect(url_for('family_register', token=token))
    
    if len(password) < 6:
        db.close()
        flash('Password must be at least 6 characters long.', 'error')
        return redirect(url_for('family_register', token=token))
    
    # Check if email already exists
    existing = db.execute(
        "SELECT * FROM members WHERE email=?",
        (invitation['invitee_email'],)
    ).fetchone()
    
    if existing:
        db.close()
        flash('An account with this email already exists.', 'error')
        return redirect(url_for('family_register', token=token))
    
    try:
        # Create new family member account
        firefighter_id = invitation['firefighter_id']
        hashed_password = generate_password_hash(password)
        
        db.execute("""
            INSERT INTO members 
            (name, email, password, role, status, linked_member_id)
            VALUES (?, ?, ?, 'family', 'pending', ?)
        """, (name, invitation['invitee_email'], hashed_password, firefighter_id))
        
        # Mark invitation as accepted
        db.execute("UPDATE family_invitations SET status='accepted' WHERE id=?", (invitation['id'],))
        
        db.commit()
        
        # Get firefighter info for notification email
        firefighter = db.execute(
            "SELECT name, email FROM members WHERE id=?",
            (firefighter_id,)
        ).fetchone()
        
        # Get admin emails
        admins = db.execute(
            "SELECT email, name FROM members WHERE role IN ('admin', 'super_admin')"
        ).fetchall()
        
        db.close()
        
        # Send notification to firefighter
        firefighter_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #B91C1C;">Family Member Registration Pending</h2>
                <p>Hello {firefighter['name']},</p>
                <p>Your family member <strong>{name}</strong> has registered for the Local 3494 Family Portal and is awaiting admin approval.</p>
                <p>Once approved, they'll be able to access the portal and connect with other families.</p>
                <p>—<br>Local 3494 Family Portal</p>
            </div>
        </body>
        </html>
        """
        
        send_email(
            firefighter['email'],
            f"Family member {name} has registered",
            firefighter_html
        )
        
        # Send notification to admins
        for admin in admins:
            admin_html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #B91C1C;">New Family Member Registration - Approval Needed</h2>
                    <p>Hello {admin['name']},</p>
                    <p><strong>{name}</strong> has registered as a family member for firefighter <strong>{firefighter['name']}</strong> and needs your approval.</p>
                    <p>Please log in to the admin panel to review and approve the registration.</p>
                    <p>—<br>Local 3494 Admin System</p>
                </div>
            </body>
            </html>
            """
            
            send_email(
                admin['email'],
                f"New family member registration: {name}",
                admin_html
            )
        
        flash('Registration complete! Your account is pending admin approval.', 'success')
        return redirect(url_for('login'))
    
    except Exception as e:
        db.close()
        print(f"[ERROR] Family registration failed: {str(e)}")
        flash('An error occurred during registration. Please try again.', 'error')
        return redirect(url_for('family_register', token=token))


# ========== PART C: Family Portal Pages ==========
@app.route('/family')
def family_home():
    """Family portal home page"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get next 3 upcoming public events
    from datetime import date
    today = date.today().isoformat()
    upcoming_events = db.execute(
        "SELECT * FROM events WHERE visibility LIKE '%family_section%' AND event_date >= ? ORDER BY event_date ASC LIMIT 3",
        (today,)
    ).fetchall()
    
    # Get trending discussions (5 most recently active)
    trending_discussions = db.execute("""
        SELECT dp.id, dp.title, dp.created_at, m.name as author_name, m.profile_photo, 
               dc.slug as category_slug,
               (SELECT COUNT(*) FROM discussion_comments WHERE post_id = dp.id AND hidden = 0) as comment_count
        FROM discussion_posts dp
        LEFT JOIN members m ON dp.author_id = m.id
        LEFT JOIN discussion_categories dc ON dp.category_id = dc.id
        WHERE dp.hidden = 0 AND dp.archived = 0
        ORDER BY dp.created_at DESC LIMIT 5
    """).fetchall()
    
    db.close()
    
    return render_template('family_home.html',
        username=session.get('username', ''),
        upcoming_events=[dict(e) for e in upcoming_events],
        trending_discussions=[dict(d) for d in trending_discussions],
        role=session.get('role', 'member'))

@app.route('/family/events')
def family_events():
    """Show events for family members (both union-posted and community events)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Union-posted events visible in family section
    union_events = db.execute("""
        SELECT * FROM events 
        WHERE visibility LIKE '%family_section%' 
        ORDER BY event_date ASC
    """).fetchall()
    
    # Community events created by family members or firefighters
    community_events = db.execute("""
        SELECT fce.*, 
               (SELECT COUNT(*) FROM family_event_rsvps WHERE event_id = fce.id) as rsvp_count
        FROM family_community_events fce
        ORDER BY fce.event_date ASC
    """).fetchall()
    
    # Get current user's RSVPs
    user_id = session.get('user_id')
    user_type = session.get('user_type', 'family')  # 'member' or 'family'
    user_rsvps = set()
    if user_id:
        rsvps = db.execute(
            "SELECT event_id FROM family_event_rsvps WHERE user_type = ? AND user_id = ?",
            (user_type, user_id)
        ).fetchall()
        user_rsvps = {r['event_id'] for r in rsvps}
    
    db.close()
    
    return render_template('family_events.html',
        union_events=[dict(e) for e in union_events],
        community_events=[dict(e) for e in community_events],
        user_rsvps=user_rsvps,
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/family/events/create', methods=['POST'])
def family_event_create():
    """Create a community event (family or member)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    event_date = request.form.get('event_date', '').strip()
    event_time = request.form.get('event_time', '').strip()
    location = request.form.get('location', '').strip()
    category = request.form.get('category', 'social').strip()
    
    if not title or not event_date:
        flash('Title and date are required.', 'error')
        return redirect(url_for('family_events'))
    
    user_id = session.get('user_id')
    username = session.get('username', 'Unknown')
    user_type = session.get('user_type', 'family')
    
    db = get_db()
    db.execute("""
        INSERT INTO family_community_events 
        (title, description, event_date, event_time, location, category, created_by_type, created_by_id, created_by_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, description, event_date, event_time or None, location or None, category, user_type, user_id, username))
    db.commit()
    
    # Send push notification for new community event
    send_push_notification('family_community_events', '📅 New Family Event', f"{username} created: {title}")
    
    db.close()
    
    flash('Event created!', 'success')
    return redirect(url_for('family_events'))

@app.route('/family/events/<int:event_id>/rsvp', methods=['POST'])
def family_event_rsvp(event_id):
    """Toggle RSVP for a community event"""
    if not require_family_access():
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session.get('user_id')
    user_type = session.get('user_type', 'family')
    username = session.get('username', 'Unknown')
    
    db = get_db()
    existing = db.execute(
        "SELECT id FROM family_event_rsvps WHERE event_id = ? AND user_type = ? AND user_id = ?",
        (event_id, user_type, user_id)
    ).fetchone()
    
    if existing:
        db.execute("DELETE FROM family_event_rsvps WHERE id = ?", (existing['id'],))
        action = 'removed'
    else:
        db.execute(
            "INSERT INTO family_event_rsvps (event_id, user_type, user_id, user_name) VALUES (?, ?, ?, ?)",
            (event_id, user_type, user_id, username)
        )
        action = 'added'
    
    db.commit()
    count = db.execute("SELECT COUNT(*) as c FROM family_event_rsvps WHERE event_id = ?", (event_id,)).fetchone()['c']
    db.close()
    
    return jsonify({'action': action, 'count': count})

@app.route('/family/roster')
def family_roster():
    """Show department roster/directory for family members (with shift filtering)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    # Get shift filter from query params
    shift_filter = request.args.get('shift', '')
    
    db = get_db()
    
    # Get all active firefighter members with their spouse info
    query = """
        SELECT m.id, m.name, m.rank, m.badge_number, m.profile_photo, m.shift,
               f.name as spouse_name, f.profile_photo as spouse_photo, f.id as spouse_id
        FROM members m
        LEFT JOIN members f ON f.linked_member_id = m.id AND f.role = 'family' AND f.status = 'active'
        WHERE m.role IN ('member', 'board_member', 'admin', 'super_admin')
        AND m.status = 'active'
    """
    
    params = []
    
    # Add shift filter if provided
    if shift_filter and shift_filter in ('A', 'B', 'C'):
        query += " AND m.shift = ?"
        params.append(shift_filter)
    
    query += " ORDER BY m.name"
    
    members = db.execute(query, params).fetchall()
    
    db.close()
    
    return render_template('family_roster.html',
        members=[dict(m) for m in members],
        shift_filter=shift_filter,
        username=session.get('username', ''),
        role=session.get('role', 'member'),
        current_user_id=session.get('user_id'),
        linked_member_id=session.get('linked_member_id'))

@app.route('/family/profile/update', methods=['POST'])
def family_profile_update():
    """Update a member or family member's profile from the family directory"""
    if not require_family_access():
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    member_id = request.form.get('member_id', type=int)
    name = request.form.get('name', '').strip()
    shift = request.form.get('shift', '').strip()
    
    if not member_id or not name:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    # Permission check: can only edit self or linked member
    user_id = session.get('user_id')
    linked_id = session.get('linked_member_id')
    if member_id != user_id and member_id != linked_id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Validate shift
    if shift and shift not in ('A', 'B', 'C'):
        shift = None
    
    db = get_db()
    db.execute(
        "UPDATE members SET name = ?, shift = ? WHERE id = ?",
        (name, shift or None, member_id)
    )
    db.commit()
    db.close()
    
    # Update session username if editing own record
    if member_id == user_id:
        session['username'] = name
    
    return jsonify({'success': True, 'name': name, 'shift': shift})

@app.route('/family/announcements')
def family_announcements():
    """Show family announcements"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get all announcements sorted by date descending
    announcements = db.execute("""
        SELECT fa.id, fa.title, fa.content, fa.category, fa.created_at, m.name as author_name
        FROM family_announcements fa
        LEFT JOIN members m ON fa.author_id = m.id
        ORDER BY fa.created_at DESC
    """).fetchall()
    
    db.close()
    
    return render_template('family_announcements.html',
        announcements=[dict(a) for a in announcements],
        username=session.get('username', ''),
        role=session.get('role', 'member'),
        is_board=session.get('role') in ('board_member', 'admin', 'super_admin'))

@app.route('/family/announcements/post', methods=['POST'])
def family_announcements_post():
    """Post an announcement (board+ only)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    if session.get('role') not in ('board_member', 'admin', 'super_admin'):
        flash('Only board members can post announcements.', 'error')
        return redirect(url_for('family_announcements'))
    
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    category = request.form.get('category', 'general').strip()
    
    if not all([title, content]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('family_announcements'))
    
    if category not in ('birth', 'hire', 'promotion', 'general'):
        category = 'general'
    
    db = get_db()
    db.execute("""
        INSERT INTO family_announcements (title, content, category, author_id)
        VALUES (?, ?, ?, ?)
    """, (title, content, category, get_member_id()))
    db.commit()
    db.close()
    
    announcement_snippet = content[:100]
    send_push_notification('family_announcements', '📣 Family Announcement', announcement_snippet)
    
    flash('Announcement posted successfully!', 'success')
    return redirect(url_for('family_announcements'))

@app.route('/family/photos')
def family_photos():
    """Show family photo wall - only approved photos, organized by album"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get selected album from query params (default to 'all')
    selected_album = request.args.get('album', 'all')
    
    # Get all approved albums with photo count
    albums = db.execute("""
        SELECT pa.id, pa.name, COUNT(fp.id) as photo_count
        FROM photo_albums pa
        LEFT JOIN family_photos fp ON pa.id = fp.album_id AND fp.status = 'approved'
        WHERE pa.status = 'approved'
        GROUP BY pa.id
        ORDER BY pa.created_at ASC
    """).fetchall()
    
    # Get approved photos, filtered by album if specified
    if selected_album == 'all':
        photos = db.execute("""
            SELECT fp.id, fp.filename, fp.caption, fp.created_at, fp.album_id,
                   m.name as uploader_name, pa.name as album_name, fp.uploader_id
            FROM family_photos fp
            LEFT JOIN members m ON fp.uploader_id = m.id
            LEFT JOIN photo_albums pa ON fp.album_id = pa.id
            WHERE fp.status = 'approved'
            ORDER BY fp.created_at DESC
        """).fetchall()
    else:
        try:
            album_id = int(selected_album)
            photos = db.execute("""
                SELECT fp.id, fp.filename, fp.caption, fp.created_at, fp.album_id,
                       m.name as uploader_name, pa.name as album_name, fp.uploader_id
                FROM family_photos fp
                LEFT JOIN members m ON fp.uploader_id = m.id
                LEFT JOIN photo_albums pa ON fp.album_id = pa.id
                WHERE fp.status = 'approved' AND fp.album_id = ?
                ORDER BY fp.created_at DESC
            """, (album_id,)).fetchall()
        except (ValueError, TypeError):
            photos = []
    
    # Get user's pending photos
    current_member_id = get_member_id()
    my_pending_photos = []
    if current_member_id:
        my_pending_photos = db.execute("""
            SELECT fp.id, fp.filename, fp.caption, fp.created_at, fp.album_id,
                   pa.name as album_name
            FROM family_photos fp
            LEFT JOIN photo_albums pa ON fp.album_id = pa.id
            WHERE fp.status = 'pending' AND fp.uploader_id = ?
            ORDER BY fp.created_at DESC
        """, (current_member_id,)).fetchall()
    
    # Get approved albums for dropdown in upload form
    approved_albums = db.execute("""
        SELECT id, name FROM photo_albums WHERE status = 'approved' ORDER BY name ASC
    """).fetchall()
    
    # Fetch comments for approved photos
    comments = db.execute("""
        SELECT pc.*, m.name as commenter_name 
        FROM photo_comments pc 
        JOIN members m ON pc.commenter_id = m.id 
        WHERE pc.photo_type = 'family'
        ORDER BY pc.created_at ASC
    """).fetchall()
    
    # Group comments by photo_id
    comments_by_photo = {}
    for comment in comments:
        photo_id = comment['photo_id']
        if photo_id not in comments_by_photo:
            comments_by_photo[photo_id] = []
        comments_by_photo[photo_id].append(dict(comment))
    
    db.close()
    
    return render_template('family_photos.html',
        photos=[dict(p) for p in photos],
        albums=[dict(a) for a in albums],
        approved_albums=[dict(a) for a in approved_albums],
        selected_album=selected_album,
        my_pending_photos=[dict(p) for p in my_pending_photos],
        current_member_id=current_member_id,
        comments_by_photo=comments_by_photo,
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/family/photos/upload', methods=['POST'])
def family_photos_upload():
    """Upload a photo to family portal - requires admin approval before showing"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    # Check if file is in request
    if 'photo' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('family_photos'))
    
    file = request.files['photo']
    caption = request.form.get('caption', '').strip()
    album_id = request.form.get('album_id', '1')
    
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('family_photos'))
    
    if not allowed_file(file.filename):
        flash('Only JPG, PNG, and GIF files are allowed.', 'error')
        return redirect(url_for('family_photos'))
    
    # Validate album_id
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        album_id = 1
    
    # Create upload folder if it doesn't exist
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    # Save file with secure filename
    filename = secure_filename(file.filename)
    # Add timestamp to filename to avoid collisions
    import time
    filename = f"{int(time.time())}_{filename}"
    
    try:
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        
        # Save photo record to database with status='pending'
        db = get_db()
        db.execute("""
            INSERT INTO family_photos (uploader_id, filename, caption, album_id, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (get_member_id(), filename, caption if caption else None, album_id))
        db.commit()
        
        # Send admin notification email about pending photo
        try:
            uploader_name = session.get('username', 'A family member')
            admin_html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #B91C1C;">New Photo Upload Pending Approval</h2>
                    <p>Hello Admin,</p>
                    <p><strong>{uploader_name}</strong> has uploaded a photo to the family portal that needs your approval.</p>
                    <p style="margin-top: 20px;">
                        <a href="{BASE_URL}/admin?tab=photos" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                            Review in Admin Panel
                        </a>
                    </p>
                    <p>—<br>Local 3494 Admin System</p>
                </div>
            </body>
            </html>
            """
            send_email('reese_joseph14@yahoo.com', 'New Photo Upload Pending Approval', admin_html)
        except Exception as e:
            # Don't block the upload if email fails
            print(f"[WARNING] Failed to send admin notification for photo upload: {str(e)}")
        
        db.close()
        
        flash('Photo submitted! It will appear after admin approval.', 'success')
        return redirect(url_for('family_photos'))
    except Exception as e:
        flash('An error occurred while uploading the photo. Please try again.', 'error')
        return redirect(url_for('family_photos'))

@app.route('/family/photos/albums')
def family_photo_albums():
    """Show all approved photo albums"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get all approved albums with cover photo and count
    albums = db.execute("""
        SELECT pa.id, pa.name, pa.description,
               (SELECT filename FROM family_photos WHERE album_id = pa.id AND status = 'approved' ORDER BY created_at ASC LIMIT 1) as cover_photo,
               COUNT(fp.id) as photo_count
        FROM photo_albums pa
        LEFT JOIN family_photos fp ON pa.id = fp.album_id AND fp.status = 'approved'
        WHERE pa.status = 'approved'
        GROUP BY pa.id
        ORDER BY pa.name ASC
    """).fetchall()
    
    db.close()
    
    return render_template('family_photo_albums.html',
        albums=[dict(a) for a in albums],
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/family/photos/album/request', methods=['POST'])
def request_new_album():
    """Request a new photo album"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    
    if not name:
        flash('Album name is required.', 'error')
        return redirect(url_for('family_photo_albums'))
    
    db = get_db()
    db.execute("""
        INSERT INTO photo_albums (name, description, created_by, status)
        VALUES (?, ?, ?, 'pending')
    """, (name, description if description else None, get_member_id()))
    db.commit()
    
    # Send admin notification email about pending album request
    try:
        requester_name = session.get('username', 'A family member')
        admin_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #B91C1C;">New Album Request Pending Approval</h2>
                <p>Hello Admin,</p>
                <p><strong>{requester_name}</strong> has requested a new photo album titled "<strong>{name}</strong>" that needs your approval.</p>
                <p style="margin-top: 20px;">
                    <a href="{BASE_URL}/admin?tab=album" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                        Review in Admin Panel
                    </a>
                </p>
                <p>—<br>Local 3494 Admin System</p>
            </div>
        </body>
        </html>
        """
        send_email('reese_joseph14@yahoo.com', 'New Album Request Pending Approval', admin_html)
    except Exception as e:
        # Don't block the request if email fails
        print(f"[WARNING] Failed to send admin notification for album request: {str(e)}")
    
    db.close()
    
    flash('Album request submitted for admin approval.', 'success')
    return redirect(url_for('family_photo_albums'))

@app.route('/family/photos/<int:photo_id>/delete', methods=['POST'])
def family_photo_delete(photo_id):
    """Delete a family photo (owner or admin only)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    photo = db.execute("SELECT * FROM family_photos WHERE id = ?", (photo_id,)).fetchone()
    
    if not photo:
        flash('Photo not found.', 'error')
        db.close()
        return redirect(url_for('family_photos'))
    
    current_member_id = get_member_id()
    is_owner = photo['uploader_id'] == current_member_id
    is_admin = session.get('role') in ['admin', 'super_admin']
    
    if not (is_owner or is_admin):
        flash('You do not have permission to delete this photo.', 'error')
        db.close()
        return redirect(url_for('family_photos'))
    
    # Delete file from disk
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, photo['filename']))
    except FileNotFoundError:
        pass
    
    # Delete from database
    db.execute("DELETE FROM family_photos WHERE id = ?", (photo_id,))
    db.commit()
    db.close()
    
    flash('Photo deleted.', 'success')
    return redirect(url_for('family_photos'))

@app.route('/family/photos/<int:photo_id>/comment', methods=['POST'])
def family_photo_comment(photo_id):
    """Add a comment to a family photo"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    comment_text = request.form.get('comment', '').strip()
    
    if not comment_text:
        flash('Comment cannot be empty.', 'error')
        return redirect(url_for('family_photos'))
    
    if len(comment_text) > 500:
        flash('Comment is too long (max 500 characters).', 'error')
        return redirect(url_for('family_photos'))
    
    db = get_db()
    
    # Check if photo exists and is approved
    photo = db.execute("SELECT * FROM family_photos WHERE id = ? AND status = 'approved'", (photo_id,)).fetchone()
    if not photo:
        flash('Photo not found or not approved.', 'error')
        db.close()
        return redirect(url_for('family_photos'))
    
    db.execute("""
        INSERT INTO photo_comments (photo_id, photo_type, commenter_id, comment)
        VALUES (?, ?, ?, ?)
    """, (photo_id, 'family', get_member_id(), comment_text))
    db.commit()
    db.close()
    
    flash('Comment posted!', 'success')
    return redirect(url_for('family_photos'))

# Member Photo Routes

@app.route('/members/photos')
def member_photos_page():
    """Show member photo wall - only approved photos, organized by album"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get selected album from query params (default to 'all')
    selected_album = request.args.get('album', 'all')
    
    # Get all approved albums with photo count
    albums = db.execute("""
        SELECT mpa.id, mpa.name, COUNT(mp.id) as photo_count
        FROM member_photo_albums mpa
        LEFT JOIN member_photos mp ON mpa.id = mp.album_id AND mp.status = 'approved'
        WHERE mpa.status = 'approved'
        GROUP BY mpa.id
        ORDER BY mpa.created_at ASC
    """).fetchall()
    
    # Get approved photos, filtered by album if specified
    if selected_album == 'all':
        photos = db.execute("""
            SELECT mp.id, mp.filename, mp.caption, mp.created_at, mp.album_id,
                   m.name as uploader_name, mpa.name as album_name, mp.uploader_id
            FROM member_photos mp
            LEFT JOIN members m ON mp.uploader_id = m.id
            LEFT JOIN member_photo_albums mpa ON mp.album_id = mpa.id
            WHERE mp.status = 'approved'
            ORDER BY mp.created_at DESC
        """).fetchall()
    else:
        try:
            album_id = int(selected_album)
            photos = db.execute("""
                SELECT mp.id, mp.filename, mp.caption, mp.created_at, mp.album_id,
                       m.name as uploader_name, mpa.name as album_name, mp.uploader_id
                FROM member_photos mp
                LEFT JOIN members m ON mp.uploader_id = m.id
                LEFT JOIN member_photo_albums mpa ON mp.album_id = mpa.id
                WHERE mp.status = 'approved' AND mp.album_id = ?
                ORDER BY mp.created_at DESC
            """, (album_id,)).fetchall()
        except (ValueError, TypeError):
            photos = []
    
    # Get user's pending photos
    current_member_id = get_member_id()
    my_pending_photos = []
    if current_member_id:
        my_pending_photos = db.execute("""
            SELECT mp.id, mp.filename, mp.caption, mp.created_at, mp.album_id,
                   mpa.name as album_name
            FROM member_photos mp
            LEFT JOIN member_photo_albums mpa ON mp.album_id = mpa.id
            WHERE mp.status = 'pending' AND mp.uploader_id = ?
            ORDER BY mp.created_at DESC
        """, (current_member_id,)).fetchall()
    
    # Get approved albums for dropdown in upload form
    approved_albums = db.execute("""
        SELECT id, name FROM member_photo_albums WHERE status = 'approved' ORDER BY name ASC
    """).fetchall()
    
    # Fetch comments for approved photos
    comments = db.execute("""
        SELECT pc.*, m.name as commenter_name 
        FROM photo_comments pc 
        JOIN members m ON pc.commenter_id = m.id 
        WHERE pc.photo_type = 'member'
        ORDER BY pc.created_at ASC
    """).fetchall()
    
    # Group comments by photo_id
    comments_by_photo = {}
    for comment in comments:
        photo_id = comment['photo_id']
        if photo_id not in comments_by_photo:
            comments_by_photo[photo_id] = []
        comments_by_photo[photo_id].append(dict(comment))
    
    db.close()
    
    return render_template('member_photos.html',
        photos=[dict(p) for p in photos],
        albums=[dict(a) for a in albums],
        approved_albums=[dict(a) for a in approved_albums],
        selected_album=selected_album,
        my_pending_photos=[dict(p) for p in my_pending_photos],
        current_member_id=current_member_id,
        comments_by_photo=comments_by_photo,
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/members/photos/upload', methods=['POST'])
def member_photos_upload():
    """Upload a photo to member portal - requires admin approval before showing"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    # Check if file is in request
    if 'photo' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('member_photos_page'))
    
    file = request.files['photo']
    caption = request.form.get('caption', '').strip()
    album_id = request.form.get('album_id', None)
    
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('member_photos_page'))
    
    if not allowed_file(file.filename):
        flash('Only JPG, PNG, and GIF files are allowed.', 'error')
        return redirect(url_for('member_photos_page'))
    
    # Validate album_id if provided
    if album_id:
        try:
            album_id = int(album_id)
        except (ValueError, TypeError):
            album_id = None
    
    # Create upload folder if it doesn't exist
    os.makedirs(MEMBER_UPLOAD_FOLDER, exist_ok=True)
    
    # Save file with secure filename
    filename = secure_filename(file.filename)
    # Add timestamp to filename to avoid collisions
    import time
    filename = f"{int(time.time())}_{filename}"
    
    try:
        file.save(os.path.join(MEMBER_UPLOAD_FOLDER, filename))
        
        # Save photo record to database with status='pending'
        db = get_db()
        db.execute("""
            INSERT INTO member_photos (uploader_id, filename, caption, album_id, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (get_member_id(), filename, caption if caption else None, album_id))
        db.commit()
        
        # Send admin notification email about pending photo
        try:
            uploader_name = session.get('username', 'A member')
            admin_html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #B91C1C;">New Photo Upload Pending Approval</h2>
                    <p>Hello Admin,</p>
                    <p><strong>{uploader_name}</strong> has uploaded a photo to the member portal that needs your approval.</p>
                    <p style="margin-top: 20px;">
                        <a href="{BASE_URL}/admin/member-photos/queue" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                            Review in Admin Panel
                        </a>
                    </p>
                    <p>—<br>Local 3494 Admin System</p>
                </div>
            </body>
            </html>
            """
            send_email('reese_joseph14@yahoo.com', 'New Photo Upload Pending Approval', admin_html)
        except Exception as e:
            # Don't block the upload if email fails
            print(f"[WARNING] Failed to send admin notification for member photo upload: {str(e)}")
        
        db.close()
        
        flash('Photo submitted! It will appear after admin approval.', 'success')
        return redirect(url_for('member_photos_page'))
    except Exception as e:
        flash('An error occurred while uploading the photo. Please try again.', 'error')
        return redirect(url_for('member_photos_page'))

@app.route('/members/photos/<int:photo_id>/delete', methods=['POST'])
def member_photo_delete(photo_id):
    """Delete a member photo (owner or admin only)"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    db = get_db()
    photo = db.execute("SELECT * FROM member_photos WHERE id = ?", (photo_id,)).fetchone()
    
    if not photo:
        flash('Photo not found.', 'error')
        db.close()
        return redirect(url_for('member_photos_page'))
    
    current_member_id = get_member_id()
    is_owner = photo['uploader_id'] == current_member_id
    is_admin = session.get('role') in ['admin', 'super_admin']
    
    if not (is_owner or is_admin):
        flash('You do not have permission to delete this photo.', 'error')
        db.close()
        return redirect(url_for('member_photos_page'))
    
    # Delete file from disk
    try:
        os.remove(os.path.join(MEMBER_UPLOAD_FOLDER, photo['filename']))
    except FileNotFoundError:
        pass
    
    # Delete from database
    db.execute("DELETE FROM member_photos WHERE id = ?", (photo_id,))
    db.commit()
    db.close()
    
    flash('Photo deleted.', 'success')
    return redirect(url_for('member_photos_page'))

@app.route('/members/photos/<int:photo_id>/comment', methods=['POST'])
def member_photo_comment(photo_id):
    """Add a comment to a member photo"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    comment_text = request.form.get('comment', '').strip()
    
    if not comment_text:
        flash('Comment cannot be empty.', 'error')
        return redirect(url_for('member_photos_page'))
    
    if len(comment_text) > 500:
        flash('Comment is too long (max 500 characters).', 'error')
        return redirect(url_for('member_photos_page'))
    
    db = get_db()
    
    # Check if photo exists and is approved
    photo = db.execute("SELECT * FROM member_photos WHERE id = ? AND status = 'approved'", (photo_id,)).fetchone()
    if not photo:
        flash('Photo not found or not approved.', 'error')
        db.close()
        return redirect(url_for('member_photos_page'))
    
    db.execute("""
        INSERT INTO photo_comments (photo_id, photo_type, commenter_id, comment)
        VALUES (?, ?, ?, ?)
    """, (photo_id, 'member', get_member_id(), comment_text))
    db.commit()
    db.close()
    
    flash('Comment posted!', 'success')
    return redirect(url_for('member_photos_page'))

@app.route('/members/photos/albums')
def member_photo_albums_page():
    """Show all approved member photo albums"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get all approved albums with cover photo and count
    albums = db.execute("""
        SELECT mpa.id, mpa.name, mpa.description,
               (SELECT filename FROM member_photos WHERE album_id = mpa.id AND status = 'approved' ORDER BY created_at ASC LIMIT 1) as cover_photo,
               COUNT(mp.id) as photo_count
        FROM member_photo_albums mpa
        LEFT JOIN member_photos mp ON mpa.id = mp.album_id AND mp.status = 'approved'
        WHERE mpa.status = 'approved'
        GROUP BY mpa.id
        ORDER BY mpa.name ASC
    """).fetchall()
    
    db.close()
    
    return render_template('member_photo_albums.html',
        albums=[dict(a) for a in albums],
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/members/photos/album/request', methods=['POST'])
def member_request_new_album():
    """Request a new member photo album"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    
    if not name:
        flash('Album name is required.', 'error')
        return redirect(url_for('member_photos_page'))
    
    db = get_db()
    db.execute("""
        INSERT INTO member_photo_albums (name, description, created_by, status)
        VALUES (?, ?, ?, 'pending')
    """, (name, description if description else None, get_member_id()))
    db.commit()
    
    # Send admin notification email about pending album request
    try:
        requester_name = session.get('username', 'A member')
        admin_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #B91C1C;">New Album Request Pending Approval</h2>
                <p>Hello Admin,</p>
                <p><strong>{requester_name}</strong> has requested a new photo album titled "<strong>{name}</strong>" that needs your approval.</p>
                <p style="margin-top: 20px;">
                    <a href="{BASE_URL}/admin/member-photos/albums" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                        Review in Admin Panel
                    </a>
                </p>
                <p>—<br>Local 3494 Admin System</p>
            </div>
        </body>
        </html>
        """
        send_email('reese_joseph14@yahoo.com', 'New Album Request Pending Approval', admin_html)
    except Exception as e:
        # Don't block the request if email fails
        print(f"[WARNING] Failed to send admin notification for member album request: {str(e)}")
    
    db.close()
    
    flash('Album request submitted for admin approval.', 'success')
    return redirect(url_for('member_photos_page'))

# Admin Member Photo Routes

@app.route('/admin/member-photos/queue')
@require_role('admin', 'super_admin')
def admin_member_photo_queue():
    """Show member photo moderation queue - pending photos only"""
    db = get_db()
    
    pending_photos = db.execute("""
        SELECT mp.id, mp.filename, mp.caption, mp.created_at,
               m.name as uploader_name, mpa.name as album_name
        FROM member_photos mp
        LEFT JOIN members m ON mp.uploader_id = m.id
        LEFT JOIN member_photo_albums mpa ON mp.album_id = mpa.id
        WHERE mp.status = 'pending'
        ORDER BY mp.created_at ASC
    """).fetchall()
    
    db.close()
    
    return render_template('admin_member_photo_queue.html',
        photos=[dict(p) for p in pending_photos],
        username=session.get('username', ''))

@app.route('/admin/member-photos/approve', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_approve_member_photo():
    """Approve a member photo or all pending photos"""
    db = get_db()
    
    approve_all = request.form.get('approve_all')
    photo_id = request.form.get('photo_id')
    
    if approve_all:
        db.execute("UPDATE member_photos SET status = 'approved' WHERE status = 'pending'")
        flash('All pending member photos approved!', 'success')
    elif photo_id:
        try:
            photo_id = int(photo_id)
            db.execute("UPDATE member_photos SET status = 'approved' WHERE id = ?", (photo_id,))
            flash('Member photo approved!', 'success')
        except (ValueError, TypeError):
            flash('Invalid photo ID.', 'error')
    else:
        flash('No photo selected.', 'error')
    
    db.commit()
    db.close()
    
    return redirect(url_for('admin_member_photo_queue'))

@app.route('/admin/member-photos/reject', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_reject_member_photo():
    """Reject a member photo"""
    photo_id = request.form.get('photo_id', '').strip()
    
    if not photo_id:
        flash('No photo selected.', 'error')
        return redirect(url_for('admin_member_photo_queue'))
    
    try:
        photo_id = int(photo_id)
    except (ValueError, TypeError):
        flash('Invalid photo ID.', 'error')
        return redirect(url_for('admin_member_photo_queue'))
    
    db = get_db()
    db.execute("UPDATE member_photos SET status = 'rejected' WHERE id = ?", (photo_id,))
    db.commit()
    db.close()
    
    flash('Member photo rejected.', 'success')
    return redirect(url_for('admin_member_photo_queue'))

@app.route('/admin/member-photos/albums')
@require_role('admin', 'super_admin')
def admin_member_photo_albums():
    """List pending and approved member photo albums"""
    db = get_db()
    
    pending_albums = db.execute("""
        SELECT mpa.id, mpa.name, mpa.description, mpa.created_by,
               m.name as creator_name, COUNT(mp.id) as photo_count
        FROM member_photo_albums mpa
        LEFT JOIN members m ON mpa.created_by = m.id
        LEFT JOIN member_photos mp ON mpa.id = mp.album_id
        WHERE mpa.status = 'pending'
        GROUP BY mpa.id
        ORDER BY mpa.created_at ASC
    """).fetchall()
    
    approved_albums = db.execute("""
        SELECT mpa.id, mpa.name, mpa.description, mpa.created_by,
               m.name as creator_name, COUNT(mp.id) as photo_count
        FROM member_photo_albums mpa
        LEFT JOIN members m ON mpa.created_by = m.id
        LEFT JOIN member_photos mp ON mpa.id = mp.album_id AND mp.status = 'approved'
        WHERE mpa.status = 'approved'
        GROUP BY mpa.id
        ORDER BY mpa.name ASC
    """).fetchall()
    
    db.close()
    
    return render_template('admin_member_photo_albums.html',
        pending_albums=[dict(a) for a in pending_albums],
        approved_albums=[dict(a) for a in approved_albums],
        username=session.get('username', ''))

@app.route('/admin/member-photos/album/approve', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_approve_member_album():
    """Approve a member photo album"""
    album_id = request.form.get('album_id', '').strip()
    
    if not album_id:
        flash('No album selected.', 'error')
        return redirect(url_for('admin_member_photo_albums'))
    
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        flash('Invalid album ID.', 'error')
        return redirect(url_for('admin_member_photo_albums'))
    
    db = get_db()
    db.execute("UPDATE member_photo_albums SET status = 'approved' WHERE id = ?", (album_id,))
    db.commit()
    db.close()
    
    flash('Member photo album approved!', 'success')
    return redirect(url_for('admin_member_photo_albums'))

@app.route('/admin/member-photos/album/delete', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_delete_member_album():
    """Delete a member photo album"""
    album_id = request.form.get('album_id', '').strip()
    
    if not album_id:
        flash('No album selected.', 'error')
        return redirect(url_for('admin_member_photo_albums'))
    
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        flash('Invalid album ID.', 'error')
        return redirect(url_for('admin_member_photo_albums'))
    
    db = get_db()
    # Delete all photos in the album first
    photos = db.execute("SELECT filename FROM member_photos WHERE album_id = ?", (album_id,)).fetchall()
    for photo in photos:
        try:
            os.remove(os.path.join(MEMBER_UPLOAD_FOLDER, photo['filename']))
        except FileNotFoundError:
            pass
    
    db.execute("DELETE FROM member_photos WHERE album_id = ?", (album_id,))
    db.execute("DELETE FROM member_photo_albums WHERE id = ?", (album_id,))
    db.commit()
    db.close()
    
    flash('Member photo album deleted.', 'success')
    return redirect(url_for('admin_member_photo_albums'))

@app.route('/profile/photo/upload', methods=['POST'])
def upload_profile_photo():
    """Upload profile photo for current member"""
    if 'user_id' not in session:
        flash('Please log in to upload a photo.', 'error')
        return redirect(url_for('login'))
    
    # Check if user is pending (not allowed to upload)
    if session.get('status') == 'pending':
        flash('Pending members cannot upload profile photos.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    # Check if file is in request
    if 'photo' not in request.files:
        flash('No file selected.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    file = request.files['photo']
    
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    if not allowed_file(file.filename):
        flash('Only JPG, PNG, and GIF files are allowed.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    # Create upload folder if it doesn't exist
    os.makedirs(PROFILE_PHOTO_FOLDER, exist_ok=True)
    
    # Save file with secure filename
    filename = secure_filename(file.filename)
    # Add member_id and timestamp to filename to avoid collisions
    import time
    filename = f"{get_member_id()}_{int(time.time())}_{filename}"
    
    try:
        file.save(os.path.join(PROFILE_PHOTO_FOLDER, filename))
        
        # Update member's profile_photo in database
        db = get_db()
        db.execute("""
            UPDATE members SET profile_photo = ? WHERE id = ?
        """, (filename, get_member_id()))
        db.commit()
        db.close()
        
        flash('Profile photo updated successfully!', 'success')
        return redirect(request.referrer or url_for('family_home'))
    except Exception as e:
        flash('An error occurred while uploading the photo. Please try again.', 'error')
        return redirect(request.referrer or url_for('family_home'))

@app.route('/family/chat')
def family_chat():
    """Show family chat"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Fetch last 50 messages with member info
    messages = db.execute("""
        SELECT fc.id, fc.member_id, fc.message, fc.created_at, m.name as member_name, m.role, m.linked_member_id
        FROM family_chat fc
        LEFT JOIN members m ON fc.member_id = m.id
        ORDER BY fc.created_at ASC
        LIMIT 50
    """).fetchall()
    
    # Get linked member names for family members
    messages_list = []
    for msg in messages:
        msg_dict = dict(msg)
        if msg_dict['role'] == 'family' and msg_dict['linked_member_id']:
            # Get the linked firefighter's name
            linked_member = db.execute(
                "SELECT name FROM members WHERE id=?",
                (msg_dict['linked_member_id'],)
            ).fetchone()
            if linked_member:
                msg_dict['display_name'] = f"{msg_dict['member_name']} (family of {linked_member['name']})"
            else:
                msg_dict['display_name'] = msg_dict['member_name']
        else:
            msg_dict['display_name'] = msg_dict['member_name']
        messages_list.append(msg_dict)
    
    db.close()
    
    return render_template('family_chat.html',
        messages=messages_list,
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/family/chat/send', methods=['POST'])
def family_chat_send():
    """Send a message to family chat"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    message = request.form.get('message', '').strip()
    
    if not message:
        flash('Please enter a message.', 'error')
        return redirect(url_for('family_chat'))
    
    db = get_db()
    db.execute("""
        INSERT INTO family_chat (member_id, message)
        VALUES (?, ?)
    """, (get_member_id(), message))
    db.commit()
    notify_mentions(message, get_member_id(), 'Family Chat')
    db.close()
    
    message_snippet = message[:100]
    send_push_notification('family_chat', 'New Family Chat', message_snippet, exclude_user_id=get_member_id())
    
    return redirect(url_for('family_chat'))


@app.route('/admin')
def admin():
    if 'user_id' not in session or not is_admin():
        flash('Admin access required.', 'error')
        return redirect(url_for('members'))
    
    db = get_db()
    
    # Get all members for members management tab (include linked member name for family accounts)
    all_members = db.execute("""
        SELECT m.id, m.name, m.email, m.role, m.status, m.created_at, m.linked_member_id,
               p.name as linked_member_name
        FROM members m
        LEFT JOIN members p ON m.linked_member_id = p.id
        ORDER BY m.name ASC
    """).fetchall()
    members_list = [dict(m) for m in all_members]
    
    # Get pending members
    pending_members = [m for m in members_list if m['status'] == 'pending']
    
    # Get all store orders
    all_orders = db.execute("""
        SELECT id, item_name, size, quantity, price, customer_name, customer_email, payment_method, order_status, created_at FROM store_orders ORDER BY created_at DESC
    """).fetchall()
    orders_list = [dict(o) for o in all_orders]
    
    # Calculate total revenue (sum of fulfilled orders)
    total_revenue = db.execute("""
        SELECT SUM(price) FROM store_orders WHERE order_status = 'fulfilled'
    """).fetchone()[0] or 0
    
    # Get all events with signups
    all_events = db.execute("""
        SELECT id, title, event_date, event_time, location, description, event_type, signup_enabled, visibility FROM events ORDER BY event_date DESC
    """).fetchall()
    events_list = [dict(e) for e in all_events]
    
    # Fetch signups for each event
    for event in events_list:
        signups = db.execute(
            "SELECT m.name, es.note FROM event_signups es JOIN members m ON es.member_id=m.id WHERE es.event_id=?",
            (event['id'],)
        ).fetchall()
        event['signups'] = [dict(s) for s in signups]
    
    # Get all bulletin posts
    all_posts = db.execute("""
        SELECT bp.id, bp.title, bp.content, bp.pinned, bp.created_at, m.name as author_name
        FROM bulletin_posts bp
        LEFT JOIN members m ON bp.author_id = m.id
        ORDER BY bp.pinned DESC, bp.created_at DESC
    """).fetchall()
    posts_list = [dict(p) for p in all_posts]
    
    # Get count of pending photos for badge
    pending_photos_count = db.execute("""
        SELECT COUNT(*) FROM family_photos WHERE status = 'pending'
    """).fetchone()[0]
    
    # Get contact messages
    contact_messages = db.execute("SELECT * FROM contact_messages ORDER BY created_at DESC").fetchall()
    
    db.close()
    
    return render_template('admin.html',
        members=members_list,
        pending_members=pending_members,
        is_super_admin=is_super_admin(),
        orders=orders_list,
        total_revenue=total_revenue,
        events=events_list,
        posts=posts_list,
        pending_photos=pending_photos_count,
        contact_messages=[dict(m) for m in contact_messages],
        username=get_member_name())

# ========== ADMIN MESSAGE ROUTES ==========

@app.route('/admin/messages/mark-viewed', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_messages_mark_viewed():
    db = get_db()
    db.execute("UPDATE contact_messages SET viewed_at = datetime('now') WHERE viewed_at IS NULL")
    db.commit()
    db.close()
    return '', 204

@app.route('/admin/messages/<int:msg_id>/status', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_message_status(msg_id):
    new_status = request.form.get('status')
    if new_status not in ('new', 'in_progress', 'closed'):
        return '', 400
    db = get_db()
    db.execute("UPDATE contact_messages SET status = ? WHERE id = ?", (new_status, msg_id))
    db.commit()
    db.close()
    return redirect('/admin?tab=messages')

# ========== ADMIN ROUTES ==========
@app.route('/admin/members/role', methods=['POST'])
@require_role('super_admin')
def admin_update_member_role():
    """Update a member's role (super_admin only)"""
    member_id = request.form.get('member_id', '').strip()
    new_role = request.form.get('role', '').strip()
    
    if not member_id or new_role not in ['member', 'board_member', 'admin', 'super_admin', 'family']:
        flash('Invalid member or role.', 'error')
        return redirect('/admin?tab=members')
    
    db = get_db()
    
    # Prevent super_admin from demoting themselves
    current_user_id = get_member_id()
    if int(member_id) == current_user_id and new_role != 'super_admin':
        flash('You cannot demote yourself from super_admin.', 'error')
        db.close()
        return redirect('/admin?tab=members')
    
    # Update role
    db.execute("UPDATE members SET role = ? WHERE id = ?", (new_role, member_id))
    db.commit()
    db.close()
    
    flash('Member role updated successfully.', 'success')
    return redirect('/admin?tab=members')

@app.route('/admin/members/approve', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_approve_member():
    """Approve a pending member (admin+ only)"""
    member_id = request.form.get('member_id', '').strip()
    action = request.form.get('action', '').strip()  # 'approve' or 'suspend'
    
    if not member_id or action not in ['approve', 'suspend']:
        flash('Invalid request.', 'error')
        return redirect('/admin?tab=pending')
    
    db = get_db()
    new_status = 'active' if action == 'approve' else 'suspended'
    
    # Get member details before updating
    member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    
    db.execute("UPDATE members SET status = ? WHERE id = ?", (new_status, member_id))
    db.commit()
    
    # Send notification emails if approved
    if action == 'approve' and member:
        # Email to approved member
        member_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #B91C1C;">Your Account Has Been Approved!</h2>
                <p>Hello {member['name']},</p>
                <p>Your Local 3494 account has been approved and is now active. You can now log in to the portal and access all member resources.</p>
                <p style="margin-top: 20px;">
                    <a href="{BASE_URL}/login" style="display: inline-block; background-color: #B91C1C; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                        Log In Now
                    </a>
                </p>
                <p>Welcome to Local 3494!</p>
                <p>—<br>Local 3494 Admin</p>
            </div>
        </body>
        </html>
        """
        
        send_email(
            member['email'],
            'Your Local 3494 account has been approved!',
            member_html
        )
        
        # If this is a family member, also email the linked firefighter
        if member['role'] == 'family' and member['linked_member_id']:
            firefighter = db.execute(
                "SELECT name, email FROM members WHERE id = ?",
                (member['linked_member_id'],)
            ).fetchone()
            
            if firefighter:
                firefighter_html = f"""
                <html>
                <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #B91C1C;">Family Member Account Approved</h2>
                        <p>Hello {firefighter['name']},</p>
                        <p>Your family member <strong>{member['name']}</strong>'s account has been approved and is now active. They can now access the Local 3494 Family Portal.</p>
                        <p>—<br>Local 3494 Admin</p>
                    </div>
                </body>
                </html>
                """
                
                send_email(
                    firefighter['email'],
                    f'Your family member {member["name"]} account has been approved',
                    firefighter_html
                )
    
    db.close()
    
    action_text = 'approved' if action == 'approve' else 'suspended'
    flash(f'Member {action_text} successfully.', 'success')
    return redirect('/admin?tab=pending')

@app.route('/admin/members/delete', methods=['POST'])
@require_role('super_admin')
def admin_delete_member():
    """Permanently delete a member account (super_admin only)"""
    member_id = request.form.get('member_id', '').strip()
    
    if not member_id:
        flash('Invalid request.', 'error')
        return redirect('/admin?tab=members')
    
    db = get_db()
    
    # Don't allow deleting yourself
    if str(member_id) == str(session.get('user_id')):
        flash('You cannot delete your own account.', 'error')
        db.close()
        return redirect('/admin?tab=members')
    
    member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not member:
        flash('Member not found.', 'error')
        db.close()
        return redirect('/admin?tab=members')
    
    member_name = member['name']
    
    # Delete related data first (foreign key cleanup)
    # Remove family links where this member is the linked firefighter
    db.execute("UPDATE members SET linked_member_id = NULL WHERE linked_member_id = ?", (member_id,))
    # Delete any pending invites they sent
    db.execute("DELETE FROM family_invitations WHERE firefighter_id = ?", (member_id,))
    # Cascade delete all related data
    db.execute('DELETE FROM event_signups WHERE member_id = ?', (member_id,))
    db.execute('DELETE FROM chat_messages WHERE member_id = ?', (member_id,))
    db.execute('DELETE FROM family_chat WHERE member_id = ?', (member_id,))
    db.execute('DELETE FROM discussion_posts WHERE author_id = ?', (member_id,))
    db.execute('DELETE FROM discussion_comments WHERE author_id = ?', (member_id,))
    db.execute('DELETE FROM discussion_reactions WHERE member_id = ?', (member_id,))
    db.execute('DELETE FROM push_tokens WHERE user_id = ?', (member_id,))
    db.execute('DELETE FROM notification_preferences WHERE user_id = ?', (member_id,))
    db.execute('DELETE FROM family_photos WHERE uploader_id = ?', (member_id,))
    db.execute('DELETE FROM member_photos WHERE uploader_id = ?', (member_id,))
    db.execute('DELETE FROM photo_comments WHERE author_id = ?', (member_id,))
    # Delete the member
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    db.commit()
    db.close()
    
    flash(f'Member "{member_name}" has been permanently deleted.', 'success')
    return redirect('/admin?tab=members')

@app.route('/admin/store/orders', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_update_order():
    """Update store order status (admin+ only)"""
    order_id = request.form.get('order_id', '').strip()
    new_status = request.form.get('status', '').strip()
    
    if not order_id or new_status not in ['pending', 'paid', 'fulfilled']:
        flash('Invalid order or status.', 'error')
        return redirect('/admin?tab=orders')
    
    db = get_db()
    db.execute("UPDATE store_orders SET order_status = ? WHERE id = ?", (new_status, order_id))
    db.commit()
    db.close()
    
    flash('Order status updated successfully.', 'success')
    return redirect('/admin?tab=orders')

@app.route('/admin/events/edit', methods=['POST'])
@require_role('board_member', 'admin', 'super_admin')
def admin_edit_event():
    """Edit an event (board+ only)"""
    event_id = request.form.get('event_id', '').strip()
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    location = request.form.get('location', '').strip()
    event_date = request.form.get('event_date', '').strip()
    event_time = request.form.get('event_time', '').strip()
    event_type = request.form.get('event_type', 'member').strip()
    signup_enabled = 1 if request.form.get('signup_enabled') else 0
    
    # Build visibility string from checkboxes
    visibility_targets = []
    if request.form.get('visibility_public_homepage'):
        visibility_targets.append('public_homepage')
    if request.form.get('visibility_member_portal'):
        visibility_targets.append('member_portal')
    if request.form.get('visibility_family_section'):
        visibility_targets.append('family_section')
    visibility = ','.join(visibility_targets) if visibility_targets else 'public_homepage,member_portal'
    
    if not all([event_id, title, event_date, location]):
        flash('Please fill in all required fields.', 'error')
        return redirect('/admin?tab=events')
    
    db = get_db()
    db.execute("""
        UPDATE events 
        SET title = ?, description = ?, location = ?, event_date = ?, event_time = ?, event_type = ?, signup_enabled = ?, visibility = ?
        WHERE id = ?
    """, (title, description, location, event_date, event_time, event_type, signup_enabled, visibility, event_id))
    db.commit()
    db.close()
    
    flash('Event updated successfully.', 'success')
    return redirect('/admin?tab=events')

@app.route('/admin/events/delete', methods=['POST'])
@require_role('board_member', 'admin', 'super_admin')
def admin_delete_event():
    """Delete an event (board+ only)"""
    event_id = request.form.get('event_id', '').strip()
    
    if not event_id:
        flash('Invalid event.', 'error')
        return redirect('/admin?tab=events')
    
    db = get_db()
    db.execute("DELETE FROM event_signups WHERE event_id = ?", (event_id,))
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    db.close()
    
    flash('Event deleted successfully.', 'success')
    return redirect('/admin?tab=events')

@app.route('/admin/events/create', methods=['POST'])
@require_role('board_member', 'admin', 'super_admin')
def admin_create_event():
    """Create a new event from admin panel"""
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    location = request.form.get('location', '').strip()
    event_date = request.form.get('event_date', '').strip()
    event_time = request.form.get('event_time', '').strip()
    event_type = request.form.get('event_type', 'member').strip()
    signup_enabled = 1 if request.form.get('signup_enabled') else 0
    
    visibility_targets = []
    if request.form.get('visibility_public_homepage'):
        visibility_targets.append('public_homepage')
    if request.form.get('visibility_member_portal'):
        visibility_targets.append('member_portal')
    if request.form.get('visibility_family_section'):
        visibility_targets.append('family_section')
    visibility = ','.join(visibility_targets) if visibility_targets else 'member_portal'
    
    if not all([title, event_date, location]):
        flash('Please fill in all required fields.', 'error')
        return redirect('/admin?tab=events')
    
    db = get_db()
    db.execute("""
        INSERT INTO events (title, description, location, event_date, event_time, event_type, signup_enabled, visibility, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, description, location, event_date, event_time, event_type, signup_enabled, visibility, session.get('user_id')))
    db.commit()
    db.close()
    
    send_push_notification('events', 'New Event Posted', title)
    flash('Event created successfully!', 'success')
    return redirect('/admin?tab=events')

@app.route('/admin/bulletin/delete', methods=['POST'])
@require_role('board_member', 'admin', 'super_admin')
def admin_delete_bulletin():
    """Delete a bulletin post (board+ only)"""
    post_id = request.form.get('post_id', '').strip()
    
    if not post_id:
        flash('Invalid post.', 'error')
        return redirect('/admin?tab=bulletin')
    
    db = get_db()
    db.execute("DELETE FROM bulletin_posts WHERE id = ?", (post_id,))
    db.commit()
    db.close()
    
    flash('Post deleted successfully.', 'success')
    return redirect('/admin?tab=bulletin')

@app.route('/admin/bulletin/pin', methods=['POST'])
@require_role('board_member', 'admin', 'super_admin')
def admin_pin_bulletin():
    """Pin or unpin a bulletin post (board+ only)"""
    post_id = request.form.get('post_id', '').strip()
    
    if not post_id:
        flash('Invalid post.', 'error')
        return redirect('/admin?tab=bulletin')
    
    db = get_db()
    post = db.execute("SELECT pinned FROM bulletin_posts WHERE id = ?", (post_id,)).fetchone()
    
    if post:
        new_pinned = 0 if post['pinned'] else 1
        db.execute("UPDATE bulletin_posts SET pinned = ? WHERE id = ?", (new_pinned, post_id))
        db.commit()
        flash('Post pin status updated.', 'success')
    else:
        flash('Post not found.', 'error')
    
    db.close()
    return redirect('/admin?tab=bulletin')

# ========== Photo Moderation Routes ==========

@app.route('/admin/photos/queue')
@require_role('admin', 'super_admin')
def admin_photo_queue():
    """Show photo moderation queue - pending photos only"""
    db = get_db()
    
    pending_photos = db.execute("""
        SELECT fp.id, fp.filename, fp.caption, fp.created_at,
               m.name as uploader_name, pa.name as album_name
        FROM family_photos fp
        LEFT JOIN members m ON fp.uploader_id = m.id
        LEFT JOIN photo_albums pa ON fp.album_id = pa.id
        WHERE fp.status = 'pending'
        ORDER BY fp.created_at ASC
    """).fetchall()
    
    db.close()
    
    return render_template('admin_photo_queue.html',
        photos=[dict(p) for p in pending_photos],
        username=session.get('username', ''))

@app.route('/admin/photos/approve', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_approve_photo():
    """Approve a photo or all pending photos"""
    db = get_db()
    
    approve_all = request.form.get('approve_all')
    photo_id = request.form.get('photo_id')
    
    if approve_all:
        db.execute("UPDATE family_photos SET status = 'approved' WHERE status = 'pending'")
        flash('All pending photos approved!', 'success')
    elif photo_id:
        try:
            photo_id = int(photo_id)
            db.execute("UPDATE family_photos SET status = 'approved' WHERE id = ?", (photo_id,))
            flash('Photo approved!', 'success')
        except (ValueError, TypeError):
            flash('Invalid photo ID.', 'error')
    else:
        flash('No photo selected.', 'error')
    
    db.commit()
    db.close()
    
    return redirect(url_for('admin_photo_queue'))

@app.route('/admin/photos/reject', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_reject_photo():
    """Reject a photo"""
    photo_id = request.form.get('photo_id', '').strip()
    
    if not photo_id:
        flash('No photo selected.', 'error')
        return redirect(url_for('admin_photo_queue'))
    
    try:
        photo_id = int(photo_id)
    except (ValueError, TypeError):
        flash('Invalid photo ID.', 'error')
        return redirect(url_for('admin_photo_queue'))
    
    db = get_db()
    db.execute("UPDATE family_photos SET status = 'rejected' WHERE id = ?", (photo_id,))
    db.commit()
    db.close()
    
    flash('Photo rejected.', 'success')
    return redirect(url_for('admin_photo_queue'))

@app.route('/admin/photos/albums')
@require_role('admin', 'super_admin')
def admin_photo_albums():
    """Manage photo albums - approve, rename, delete"""
    db = get_db()
    
    albums = db.execute("""
        SELECT pa.id, pa.name, pa.status, pa.created_by,
               m.name as created_by_name,
               COUNT(fp.id) as photo_count
        FROM photo_albums pa
        LEFT JOIN members m ON pa.created_by = m.id
        LEFT JOIN family_photos fp ON pa.id = fp.album_id
        GROUP BY pa.id
        ORDER BY pa.status DESC, pa.name ASC
    """).fetchall()
    
    db.close()
    
    return render_template('admin_photo_albums.html',
        albums=[dict(a) for a in albums],
        username=session.get('username', ''))

@app.route('/admin/photos/album/approve', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_approve_album():
    """Approve a pending album request"""
    album_id = request.form.get('album_id', '').strip()
    
    if not album_id:
        flash('No album selected.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        flash('Invalid album ID.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    db = get_db()
    db.execute("UPDATE photo_albums SET status = 'approved' WHERE id = ?", (album_id,))
    db.commit()
    db.close()
    
    flash('Album approved!', 'success')
    return redirect(url_for('admin_photo_albums'))

@app.route('/admin/photos/album/rename', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_rename_album():
    """Rename a photo album"""
    album_id = request.form.get('album_id', '').strip()
    new_name = request.form.get('new_name', '').strip()
    
    if not album_id or not new_name:
        flash('Album ID and new name are required.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        flash('Invalid album ID.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    db = get_db()
    db.execute("UPDATE photo_albums SET name = ? WHERE id = ?", (new_name, album_id))
    db.commit()
    db.close()
    
    flash('Album renamed successfully!', 'success')
    return redirect(url_for('admin_photo_albums'))

@app.route('/admin/photos/album/delete', methods=['POST'])
@require_role('admin', 'super_admin')
def admin_delete_album():
    """Delete a photo album - reassign photos to General Photos first"""
    album_id = request.form.get('album_id', '').strip()
    
    if not album_id:
        flash('No album selected.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    try:
        album_id = int(album_id)
    except (ValueError, TypeError):
        flash('Invalid album ID.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    # Don't allow deleting the General Photos album (id=1)
    if album_id == 1:
        flash('Cannot delete the General Photos album.', 'error')
        return redirect(url_for('admin_photo_albums'))
    
    db = get_db()
    
    # Reassign all photos in this album to General Photos (id=1)
    db.execute("UPDATE family_photos SET album_id = 1 WHERE album_id = ?", (album_id,))
    
    # Delete the album
    db.execute("DELETE FROM photo_albums WHERE id = ?", (album_id,))
    db.commit()
    db.close()
    
    flash('Album deleted. Photos have been moved to General Photos.', 'success')
    return redirect(url_for('admin_photo_albums'))

# ========== PART A: Member Store ==========
@app.route('/members/store')
def members_store():
    """Member-only store page"""
    if not require_member_access():
        return redirect(url_for('login'))
    if session.get('role') == 'family':
        return redirect(url_for('family_home'))
    
    db = get_db()
    # Show all active items (both public and member type)
    items = db.execute(
        "SELECT * FROM store_items WHERE active=1 ORDER BY id ASC"
    ).fetchall()
    db.close()
    
    items_list = [dict(item) for item in items]
    
    # Parse JSON sizes for template
    for item in items_list:
        if item['sizes']:
            try:
                item['sizes_list'] = json.loads(item['sizes'])
            except:
                item['sizes_list'] = []
        else:
            item['sizes_list'] = []
    
    return render_template('member_store.html', items=items_list, username=get_member_name())

@app.route('/members/store/order', methods=['POST'])
def members_store_order():
    """Process member store order"""
    if not require_member_access():
        return redirect(url_for('login'))
    if session.get('role') == 'family':
        return redirect(url_for('family_home'))
    
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    payment_method = request.form.get('payment_method', '').strip()
    
    items_json = request.form.get('items_json', '[]')
    try:
        items = json.loads(items_json)
    except:
        flash('Error processing order. Please try again.', 'error')
        return redirect(url_for('members_store'))
    
    if not name or not email or not payment_method:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('members_store'))
    
    if not items:
        flash('Your order is empty.', 'error')
        return redirect(url_for('members_store'))
    
    db = get_db()
    order_ids = []
    total = 0
    
    for item in items:
        item_id = item.get('item_id')
        size = item.get('size')
        quantity = item.get('quantity', 1)
        
        # Fetch item details
        item_detail = db.execute("SELECT * FROM store_items WHERE id=?", (item_id,)).fetchone()
        if not item_detail:
            continue
        
        price_per = item_detail['price']
        total_price = price_per * quantity
        total += total_price
        
        # Insert order record
        db.execute("""
            INSERT INTO store_orders (item_id, item_name, size, quantity, price, customer_name, customer_email, customer_phone, payment_method, order_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, item_detail['name'], size, quantity, total_price, name, email, phone, payment_method, 'pending'))
        db.commit()
        
        # Get the inserted order ID
        cursor = db.execute("SELECT last_insert_rowid()")
        order_id = cursor.fetchone()[0]
        order_ids.append(order_id)
    
    db.close()
    
    if not order_ids:
        flash('Error creating order. Please try again.', 'error')
        return redirect(url_for('members_store'))
    
    # Render confirmation
    return render_template('store_confirmation.html', 
        name=name, 
        email=email, 
        phone=phone, 
        payment_method=payment_method, 
        items=items, 
        total=total, 
        order_ids=order_ids,
        pickup_location="Station 31")

# ========== PART B: Events & Signup ==========
@app.route('/members/events')
def members_events():
    """Show all events with signup functionality (filtered by visibility)"""
    if not require_login():
        return redirect(url_for('login'))
    
    db = get_db()
    member_id = get_member_id()
    
    # Fetch events visible in member portal (LIKE for comma-separated values)
    events = db.execute("""
        SELECT * FROM events 
        WHERE (visibility LIKE '%member_portal%' OR visibility LIKE '%public_homepage%')
        ORDER BY event_date ASC
    """).fetchall()
    
    # For each event, get signup info
    events_list = []
    for event in events:
        event_dict = dict(event)
        
        # Parse supplies JSON
        if event_dict['supplies']:
            try:
                event_dict['supplies_list'] = json.loads(event_dict['supplies'])
            except:
                event_dict['supplies_list'] = []
        else:
            event_dict['supplies_list'] = []
        
        # Check if current member is signed up
        signup = db.execute(
            "SELECT * FROM event_signups WHERE event_id=? AND member_id=?",
            (event_dict['id'], member_id)
        ).fetchone()
        event_dict['user_signed_up'] = signup is not None
        
        # Get list of members who signed up
        signups = db.execute(
            "SELECT m.name FROM event_signups es JOIN members m ON es.member_id=m.id WHERE es.event_id=?",
            (event_dict['id'],)
        ).fetchall()
        event_dict['signups_list'] = [dict(s)['name'] for s in signups]
        
        events_list.append(event_dict)
    
    db.close()
    
    return render_template('member_events.html', 
        events=events_list, 
        username=get_member_name(),
        is_board=is_board_member())

@app.route('/members/events/create', methods=['GET'])
def members_events_create_page():
    """Show the create event form (board+ only)"""
    if not require_login():
        return redirect(url_for('login'))
    if not is_board_member():
        flash('Board member access required.', 'error')
        return redirect(url_for('members_events'))
    return render_template('member_create_event.html', username=session.get('username', ''))

@app.route('/members/events/create', methods=['POST'])
def members_events_create():
    """Create a new event (board+ only)"""
    if not require_login():
        return redirect(url_for('login'))
    
    if not is_board_member():
        flash('Only board members can create events.', 'error')
        return redirect(url_for('members_events'))
    
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    location = request.form.get('location', '').strip()
    event_date = request.form.get('event_date', '').strip()
    event_time = request.form.get('event_time', '').strip()
    event_type = request.form.get('event_type', 'member').strip()
    signup_enabled = 1 if request.form.get('signup_enabled') else 0
    
    # Build visibility string
    visibility_targets = []
    if request.form.get('visibility_public_homepage'):
        visibility_targets.append('public_homepage')
    if request.form.get('visibility_member_portal'):
        visibility_targets.append('member_portal')
    if request.form.get('visibility_family_section'):
        visibility_targets.append('family_section')
    visibility = ','.join(visibility_targets) if visibility_targets else 'member_portal'
    
    if not all([title, event_date, location]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('members_events'))
    
    db = get_db()
    db.execute("""
        INSERT INTO events (title, description, location, event_date, event_time, event_type, signup_enabled, visibility, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, description, location, event_date, event_time, event_type, signup_enabled, visibility, get_member_id()))
    db.commit()
    db.close()
    
    send_push_notification('events', 'New Event Posted', title)
    
    flash('Event created successfully!', 'success')
    return redirect(url_for('members_events'))

@app.route('/members/events/signup', methods=['POST'])
def members_events_signup():
    """Sign up for an event"""
    if not require_login():
        return redirect(url_for('login'))
    
    event_id = request.form.get('event_id', '').strip()
    note = request.form.get('note', '').strip()
    member_id = get_member_id()
    
    if not event_id:
        flash('Invalid event.', 'error')
        return redirect(url_for('members_events'))
    
    db = get_db()
    
    # Check if already signed up
    existing = db.execute(
        "SELECT * FROM event_signups WHERE event_id=? AND member_id=?",
        (event_id, member_id)
    ).fetchone()
    
    if existing:
        flash('You are already signed up for this event.', 'error')
        db.close()
        return redirect(url_for('members_events'))
    
    # Add signup with optional note
    db.execute("""
        INSERT INTO event_signups (event_id, member_id, note)
        VALUES (?, ?, ?)
    """, (event_id, member_id, note))
    db.commit()
    db.close()
    
    flash('You have signed up for the event!', 'success')
    return redirect(url_for('members_events_detail', event_id=event_id))

@app.route('/members/events/unsignup', methods=['POST'])
def members_events_unsignup():
    """Cancel signup for an event"""
    if not require_login():
        return redirect(url_for('login'))
    
    event_id = request.form.get('event_id', '').strip()
    member_id = get_member_id()
    
    if not event_id:
        flash('Invalid event.', 'error')
        return redirect(url_for('members_events'))
    
    db = get_db()
    db.execute(
        "DELETE FROM event_signups WHERE event_id=? AND member_id=?",
        (event_id, member_id)
    )
    db.commit()
    db.close()
    
    flash('You have cancelled your signup.', 'success')
    return redirect(url_for('members_events_detail', event_id=event_id))

@app.route('/members/events/<int:event_id>')
def members_events_detail(event_id):
    """Show event detail page with signups and message board"""
    if not require_login():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Fetch event by ID
    event = db.execute(
        "SELECT * FROM events WHERE id=?",
        (event_id,)
    ).fetchone()
    
    if not event:
        flash('Event not found.', 'error')
        db.close()
        return redirect(url_for('members_events'))
    
    event_dict = dict(event)
    
    # Check if current member is signed up
    member_id = get_member_id()
    user_signup = db.execute(
        "SELECT note FROM event_signups WHERE event_id=? AND member_id=?",
        (event_id, member_id)
    ).fetchone()
    
    event_dict['user_signed_up'] = user_signup is not None
    event_dict['user_note'] = user_signup['note'] if user_signup else ''
    
    # Fetch all signups with member names and notes
    signups = db.execute(
        "SELECT m.name, es.note FROM event_signups es JOIN members m ON es.member_id=m.id WHERE es.event_id=?",
        (event_id,)
    ).fetchall()
    signups_list = [dict(s) for s in signups]
    
    # Fetch all messages with member names and timestamps
    messages = db.execute(
        "SELECT m.name, em.message, em.created_at FROM event_messages em JOIN members m ON em.member_id=m.id WHERE em.event_id=? ORDER BY em.created_at ASC",
        (event_id,)
    ).fetchall()
    messages_list = [dict(m) for m in messages]
    
    # Check if current user is board member
    is_board = session.get('role') in ('board_member', 'admin', 'super_admin')
    
    db.close()
    
    return render_template('member_event_detail.html',
        event=event_dict,
        signups=signups_list,
        messages=messages_list,
        user_signed_up=event_dict['user_signed_up'],
        user_note=event_dict['user_note'],
        is_board=is_board,
        username=get_member_name())

@app.route('/members/events/<int:event_id>/message', methods=['POST'])
def members_events_post_message(event_id):
    """Post a message to event discussion board"""
    if not require_login():
        return redirect(url_for('login'))
    
    message = request.form.get('message', '').strip()
    
    if not message:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('members_events_detail', event_id=event_id))
    
    db = get_db()
    
    # Verify event exists
    event = db.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        flash('Event not found.', 'error')
        db.close()
        return redirect(url_for('members_events'))
    
    # Insert message
    db.execute(
        "INSERT INTO event_messages (event_id, member_id, message) VALUES (?, ?, ?)",
        (event_id, get_member_id(), message)
    )
    db.commit()
    db.close()
    
    flash('Message posted!', 'success')
    return redirect(url_for('members_events_detail', event_id=event_id))

@app.route('/members/events/<int:event_id>/edit-note', methods=['POST'])
def members_events_edit_note(event_id):
    """Update signup note for an event"""
    if not require_login():
        return redirect(url_for('login'))
    
    note = request.form.get('note', '').strip()
    member_id = get_member_id()
    
    db = get_db()
    
    # Verify event exists
    event = db.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        flash('Event not found.', 'error')
        db.close()
        return redirect(url_for('members_events'))
    
    # Verify member is signed up
    signup = db.execute(
        "SELECT id FROM event_signups WHERE event_id=? AND member_id=?",
        (event_id, member_id)
    ).fetchone()
    
    if not signup:
        flash('You are not signed up for this event.', 'error')
        db.close()
        return redirect(url_for('members_events_detail', event_id=event_id))
    
    # Update note
    db.execute(
        "UPDATE event_signups SET note=? WHERE event_id=? AND member_id=?",
        (note, event_id, member_id)
    )
    db.commit()
    db.close()
    
    flash('Note updated!', 'success')
    return redirect(url_for('members_events_detail', event_id=event_id))

@app.route('/members/events/supply', methods=['POST'])
def members_events_supply():
    """Add a supply item to an event (board+ only)"""
    if not require_login():
        return redirect(url_for('login'))
    
    if not is_board_member():
        flash('Only board members can add supplies.', 'error')
        return redirect(url_for('members_events'))
    
    event_id = request.form.get('event_id', '').strip()
    supply_item = request.form.get('supply_item', '').strip()
    
    if not event_id or not supply_item:
        flash('Please fill in all fields.', 'error')
        return redirect(url_for('members_events'))
    
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    
    if not event:
        flash('Event not found.', 'error')
        db.close()
        return redirect(url_for('members_events'))
    
    # Parse existing supplies
    supplies = []
    if event['supplies']:
        try:
            supplies = json.loads(event['supplies'])
        except:
            supplies = []
    
    # Add new supply
    if supply_item not in supplies:
        supplies.append(supply_item)
    
    # Update event
    db.execute(
        "UPDATE events SET supplies=? WHERE id=?",
        (json.dumps(supplies), event_id)
    )
    db.commit()
    db.close()
    
    flash('Supply item added!', 'success')
    return redirect(url_for('members_events'))

# ========== GENERAL MEMBERSHIP MEETINGS ==========

@app.route('/members/meetings/create', methods=['GET'])
def members_meetings_create_page():
    """Show the create general membership meeting form (all members can view, but only board can submit)"""
    if not require_login():
        return redirect(url_for('login'))
    return render_template('member_create_meeting.html',
        username=session.get('username', ''),
        is_board=is_board_member())

@app.route('/members/meetings/create', methods=['POST'])
def members_meetings_create():
    """Create a new general membership meeting (board+ only)"""
    if not require_login():
        return redirect(url_for('login'))
    
    if not is_board_member():
        flash('Only board members can schedule General Membership Meetings.', 'error')
        return redirect(url_for('members_meetings_create_page'))
    
    meeting_date = request.form.get('meeting_date', '').strip()
    meeting_time = request.form.get('meeting_time', '').strip()
    location = request.form.get('location', '').strip()
    agenda = request.form.get('agenda', '').strip()
    
    if not all([meeting_date, meeting_time, location]):
        flash('Date, time, and location are required.', 'error')
        return redirect(url_for('members_meetings_create_page'))
    
    db = get_db()
    db.execute(
        "INSERT INTO general_meetings (meeting_date, meeting_time, location, agenda, created_by) VALUES (?, ?, ?, ?, ?)",
        (meeting_date, meeting_time, location, agenda, get_member_id())
    )
    db.commit()
    db.close()
    
    send_push_notification('meetings', '📢 Membership Meeting', f"A new meeting has been scheduled")
    
    flash('General Membership Meeting scheduled!', 'success')
    return redirect(url_for('members_events'))

# ========== PART C: Bulletin Board ==========
@app.route('/members/bulletin')
def members_bulletin():
    """Show bulletin board posts"""
    if not require_login():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get next membership meeting
    from datetime import date
    today = date.today().isoformat()
    next_meeting = db.execute(
        "SELECT * FROM general_meetings WHERE meeting_date >= ? ORDER BY meeting_date ASC LIMIT 1",
        (today,)
    ).fetchone()
    next_meeting_dict = dict(next_meeting) if next_meeting else None
    
    # Get all bulletin posts, sorted by pinned first then by date descending
    posts = db.execute("""
        SELECT bp.*, m.name as author_name
        FROM bulletin_posts bp
        LEFT JOIN members m ON bp.author_id = m.id
        ORDER BY bp.pinned DESC, bp.created_at DESC
    """).fetchall()
    
    posts_list = [dict(p) for p in posts]
    db.close()
    
    return render_template('bulletin.html',
        posts=posts_list,
        next_meeting=next_meeting_dict,
        username=get_member_name(),
        is_board=is_board_member())

@app.route('/members/bulletin/post', methods=['POST'])
def members_bulletin_post():
    """Create a new bulletin post (board+ only)"""
    if not require_login():
        return redirect(url_for('login'))
    
    if not is_board_member():
        flash('Only board members can create posts.', 'error')
        return redirect(url_for('members_bulletin'))
    
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    pinned = 1 if request.form.get('pinned') else 0
    
    if not title or not content:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('members_bulletin'))
    
    db = get_db()
    db.execute("""
        INSERT INTO bulletin_posts (title, content, author_id, pinned)
        VALUES (?, ?, ?, ?)
    """, (title, content, get_member_id(), pinned))
    db.commit()
    db.close()
    
    flash('Post created successfully!', 'success')
    return redirect(url_for('members_bulletin'))

# ========== PART D: Member Chat ==========
@app.route('/members/chat')
def members_chat():
    """Show member chat"""
    if not require_login():
        return redirect(url_for('login'))
    
    db = get_db()
    # Fetch last 50 messages with member names
    messages = db.execute("""
        SELECT cm.*, m.name as member_name
        FROM chat_messages cm
        LEFT JOIN members m ON cm.member_id = m.id
        ORDER BY cm.created_at ASC
        LIMIT 50
    """).fetchall()
    
    messages_list = [dict(m) for m in messages]
    db.close()
    
    return render_template('chat.html',
        messages=messages_list,
        username=get_member_name())

@app.route('/members/chat/send', methods=['POST'])
def members_chat_send():
    """Send a chat message"""
    if not require_login():
        return redirect(url_for('login'))
    
    message = request.form.get('message', '').strip()
    
    if not message:
        flash('Please enter a message.', 'error')
        return redirect(url_for('members_chat'))
    
    db = get_db()
    db.execute("""
        INSERT INTO chat_messages (member_id, message)
        VALUES (?, ?)
    """, (get_member_id(), message))
    db.commit()
    notify_mentions(message, get_member_id(), 'Members Chat')
    db.close()
    
    message_snippet = message[:100]
    send_push_notification('member_chat', 'New Member Chat', message_snippet, exclude_user_id=get_member_id())
    
    return redirect(url_for('members_chat'))

# ========== MEMBER PROFILE ==========

@app.route('/members/profile', methods=['GET', 'POST'])
def member_profile():
    """Member profile page — view and edit personal info"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        rank = request.form.get('rank', '').strip()
        shift = request.form.get('shift', '').strip()
        years_of_service = request.form.get('years_of_service', '').strip()
        
        if not name:
            flash('Name cannot be empty.', 'error')
            return redirect(url_for('member_profile'))
        
        try:
            years_int = int(years_of_service) if years_of_service else None
        except ValueError:
            years_int = None
        
        db.execute(
            "UPDATE members SET name = ?, rank = ?, shift = ?, years_of_service = ? WHERE id = ?",
            (name, rank or None, shift or None, years_int, session['user_id'])
        )
        db.commit()
        db.close()
        
        session['username'] = name
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('member_profile'))
    
    member = db.execute(
        "SELECT id, name, email, rank, shift, years_of_service, badge_number, profile_photo FROM members WHERE id = ?",
        (session['user_id'],)
    ).fetchone()
    db.close()
    
    return render_template('member_profile.html',
        member=dict(member) if member else {},
        username=session.get('username', ''),
        role=session.get('role', 'member')
    )

# ========== MEMBER DOCUMENTS ==========

@app.route('/members/documents')
def member_documents():
    """View member documents - members only"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    db = get_db()
    documents = db.execute("""
        SELECT id, title, description, url, added_by, created_at
        FROM member_documents
        ORDER BY created_at DESC
    """).fetchall()
    db.close()
    
    return render_template('member_documents.html',
        documents=[dict(d) for d in documents],
        username=session.get('username', ''),
        role=session.get('role', 'member')
    )

@app.route('/members/documents/add', methods=['POST'])
def add_document():
    """Add a new document - admin+ only"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') not in ('admin', 'super_admin'):
        flash('Only admins can add documents.', 'error')
        return redirect(url_for('member_documents'))
    
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    url = request.form.get('url', '').strip()
    
    if not title:
        flash('Title is required.', 'error')
        return redirect(url_for('member_documents'))
    
    if not url:
        flash('URL is required.', 'error')
        return redirect(url_for('member_documents'))
    
    db = get_db()
    db.execute("""
        INSERT INTO member_documents (title, description, url, added_by)
        VALUES (?, ?, ?, ?)
    """, (title, description if description else None, url, session['user_id']))
    db.commit()
    db.close()
    
    flash('Document added.', 'success')
    return redirect(url_for('member_documents'))

@app.route('/members/documents/<int:doc_id>/delete', methods=['POST'])
def delete_document(doc_id):
    """Delete a document - admin+ only"""
    if not require_member_access():
        return redirect(url_for('login'))
    
    if session.get('role') not in ('admin', 'super_admin'):
        flash('Only admins can delete documents.', 'error')
        return redirect(url_for('member_documents'))
    
    db = get_db()
    db.execute("DELETE FROM member_documents WHERE id = ?", (doc_id,))
    db.commit()
    db.close()
    
    flash('Document removed.', 'success')
    return redirect(url_for('member_documents'))

# ========== DISCUSSIONS BOARD ==========

@app.route('/family/discussions')
def family_discussions():
    """Show discussion categories"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    # Pending family cannot access discussions
    if session.get('status') == 'pending':
        flash('Pending members do not have access to discussions.', 'error')
        return redirect(url_for('family_home'))
    
    db = get_db()
    
    # Get all categories sorted by sort_order
    categories = db.execute("""
        SELECT * FROM discussion_categories ORDER BY sort_order ASC
    """).fetchall()
    
    # Get post counts and last activity for each category
    cats_with_stats = []
    for cat in categories:
        cat_dict = dict(cat)
        post_count = db.execute(
            "SELECT COUNT(*) FROM discussion_posts WHERE category_id = ? AND hidden = 0",
            (cat['id'],)
        ).fetchone()[0]
        
        last_post = db.execute("""
            SELECT created_at FROM discussion_posts 
            WHERE category_id = ? AND hidden = 0
            ORDER BY created_at DESC LIMIT 1
        """, (cat['id'],)).fetchone()
        
        cat_dict['post_count'] = post_count
        cat_dict['last_activity'] = last_post['created_at'] if last_post else None
        cats_with_stats.append(cat_dict)
    
    # Get recent posts across all categories (last 5)
    recent_posts = db.execute("""
        SELECT dp.id, dp.title, dp.created_at, m.name as author_name, dc.name as category_name,
               (SELECT COUNT(*) FROM discussion_comments WHERE post_id = dp.id AND hidden = 0) as comment_count
        FROM discussion_posts dp
        LEFT JOIN members m ON dp.author_id = m.id
        LEFT JOIN discussion_categories dc ON dp.category_id = dc.id
        WHERE dp.hidden = 0 AND dp.archived = 0
        ORDER BY dp.created_at DESC LIMIT 5
    """).fetchall()
    
    db.close()
    
    return render_template('family_discussions.html',
                         categories=cats_with_stats,
                         recent_posts=[dict(p) for p in recent_posts],
                         username=session.get('username', ''),
                         role=session.get('role', 'member'))

@app.route('/family/discussions/<category_slug>')
def family_discussion_category(category_slug):
    """Show posts in a discussion category"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    if session.get('status') == 'pending':
        flash('Pending members do not have access to discussions.', 'error')
        return redirect(url_for('family_home'))
    
    db = get_db()
    
    # Get category
    category = db.execute(
        "SELECT * FROM discussion_categories WHERE slug = ?",
        (category_slug,)
    ).fetchone()
    
    if not category:
        flash('Category not found.', 'error')
        db.close()
        return redirect(url_for('family_discussions'))
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    # Get posts for this category
    posts = db.execute("""
        SELECT dp.id, dp.title, dp.created_at, dp.expires_at, dp.locked, dp.archived,
               m.name as author_name, m.profile_photo,
               (SELECT COUNT(*) FROM discussion_comments WHERE post_id = dp.id AND hidden = 0) as comment_count,
               (SELECT COUNT(*) FROM discussion_reactions WHERE post_id = dp.id AND reaction_type = 'like') as like_count,
               (SELECT COUNT(*) FROM discussion_reactions WHERE post_id = dp.id AND reaction_type = 'heart') as heart_count
        FROM discussion_posts dp
        LEFT JOIN members m ON dp.author_id = m.id
        WHERE dp.category_id = ? AND dp.hidden = 0 AND dp.archived = 0
        ORDER BY dp.created_at DESC
        LIMIT ? OFFSET ?
    """, (category['id'], per_page, offset)).fetchall()
    
    # Get total count for pagination
    total = db.execute(
        "SELECT COUNT(*) FROM discussion_posts WHERE category_id = ? AND hidden = 0 AND archived = 0",
        (category['id'],)
    ).fetchone()[0]
    
    total_pages = (total + per_page - 1) // per_page
    
    db.close()
    
    return render_template('family_discussion_category.html',
                         category=dict(category),
                         posts=[dict(p) for p in posts],
                         page=page,
                         total_pages=total_pages,
                         username=session.get('username', ''),
                         role=session.get('role', 'member'))

@app.route('/family/discussions/post/new', methods=['GET', 'POST'])
def new_discussion_post():
    """Create a new discussion post"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    if session.get('status') == 'pending':
        flash('Pending members cannot create discussion posts.', 'error')
        return redirect(url_for('family_home'))
    
    db = get_db()
    
    if request.method == 'POST':
        category_id = request.form.get('category_id', type=int)
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        
        if not category_id or not title or not body:
            flash('Title and body are required.', 'error')
            return redirect(url_for('new_discussion_post'))
        
        # Get category to check expiry settings
        category = db.execute(
            "SELECT expiry_days FROM discussion_categories WHERE id = ?",
            (category_id,)
        ).fetchone()
        
        expires_at = None
        if category and category['expiry_days']:
            from datetime import timedelta
            expires_at = (datetime.now() + timedelta(days=category['expiry_days'])).isoformat()
        
        # Insert post
        db.execute("""
            INSERT INTO discussion_posts (category_id, author_id, title, body, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (category_id, get_member_id(), title, body, expires_at))
        db.commit()
        db.close()
        
        send_push_notification('family_discuss', 'New Family Discussion', title)
        
        flash('Post created successfully!', 'success')
        return redirect(url_for('family_discussions'))
    
    # GET: show form
    categories = db.execute(
        "SELECT id, name FROM discussion_categories ORDER BY sort_order ASC"
    ).fetchall()
    db.close()
    
    return render_template('family_discussion_post_new.html',
                         categories=[dict(c) for c in categories],
                         username=session.get('username', ''),
                         role=session.get('role', 'member'))

@app.route('/family/discussions/post/<int:post_id>')
def view_discussion_post(post_id):
    """View a single discussion post with comments"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    if session.get('status') == 'pending':
        flash('Pending members do not have access to discussions.', 'error')
        return redirect(url_for('family_home'))
    
    db = get_db()
    
    # Get post
    post = db.execute("""
        SELECT dp.*, m.name as author_name, m.profile_photo, m.id as author_id,
               dc.name as category_name, dc.slug as category_slug
        FROM discussion_posts dp
        LEFT JOIN members m ON dp.author_id = m.id
        LEFT JOIN discussion_categories dc ON dp.category_id = dc.id
        WHERE dp.id = ? AND dp.hidden = 0
    """, (post_id,)).fetchone()
    
    if not post:
        flash('Post not found.', 'error')
        db.close()
        return redirect(url_for('family_discussions'))
    
    post_dict = dict(post)
    
    # Get comments
    comments = db.execute("""
        SELECT dc.*, m.name as author_name, m.profile_photo, m.id as author_id,
               (SELECT COUNT(*) FROM discussion_reactions WHERE comment_id = dc.id AND reaction_type = 'like') as like_count,
               (SELECT COUNT(*) FROM discussion_reactions WHERE comment_id = dc.id AND reaction_type = 'heart') as heart_count,
               (SELECT COUNT(*) FROM discussion_reactions WHERE comment_id = dc.id AND member_id = ? AND reaction_type = 'like') as user_liked,
               (SELECT COUNT(*) FROM discussion_reactions WHERE comment_id = dc.id AND member_id = ? AND reaction_type = 'heart') as user_hearted
        FROM discussion_comments dc
        LEFT JOIN members m ON dc.author_id = m.id
        WHERE dc.post_id = ? AND dc.hidden = 0
        ORDER BY dc.created_at ASC
    """, (get_member_id(), get_member_id(), post_id)).fetchall()
    
    # Get post reactions
    post_likes = db.execute(
        "SELECT COUNT(*) FROM discussion_reactions WHERE post_id = ? AND reaction_type = 'like'",
        (post_id,)
    ).fetchone()[0]
    post_hearts = db.execute(
        "SELECT COUNT(*) FROM discussion_reactions WHERE post_id = ? AND reaction_type = 'heart'",
        (post_id,)
    ).fetchone()[0]
    user_liked_post = db.execute(
        "SELECT COUNT(*) FROM discussion_reactions WHERE post_id = ? AND member_id = ? AND reaction_type = 'like'",
        (post_id, get_member_id())
    ).fetchone()[0]
    user_hearted_post = db.execute(
        "SELECT COUNT(*) FROM discussion_reactions WHERE post_id = ? AND member_id = ? AND reaction_type = 'heart'",
        (post_id, get_member_id())
    ).fetchone()[0]
    
    post_dict['likes'] = post_likes
    post_dict['hearts'] = post_hearts
    post_dict['user_liked'] = bool(user_liked_post)
    post_dict['user_hearted'] = bool(user_hearted_post)
    
    db.close()
    
    return render_template('family_discussion_post.html',
                         post=post_dict,
                         comments=[dict(c) for c in comments],
                         username=session.get('username', ''),
                         role=session.get('role', 'member'),
                         is_admin=is_admin())

@app.route('/family/discussions/post/<int:post_id>/comment', methods=['POST'])
def add_discussion_comment(post_id):
    """Add a comment to a discussion post"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    if session.get('status') == 'pending':
        flash('Pending members cannot comment.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    body = request.form.get('body', '').strip()
    if not body:
        flash('Comment body cannot be empty.', 'error')
        return redirect(request.referrer or url_for('family_home'))
    
    db = get_db()
    
    # Check if post exists
    post = db.execute("SELECT id FROM discussion_posts WHERE id = ? AND hidden = 0", (post_id,)).fetchone()
    if not post:
        flash('Post not found.', 'error')
        db.close()
        return redirect(url_for('family_discussions'))
    
    # Handle optional image upload
    image_filename = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename and allowed_file(file.filename):
            os.makedirs(DISCUSSION_ATTACHMENTS_FOLDER, exist_ok=True)
            filename = secure_filename(file.filename)
            import time
            filename = f"{post_id}_{int(time.time())}_{filename}"
            try:
                file.save(os.path.join(DISCUSSION_ATTACHMENTS_FOLDER, filename))
                image_filename = filename
            except:
                pass
    
    # Insert comment
    db.execute("""
        INSERT INTO discussion_comments (post_id, author_id, body, image_filename)
        VALUES (?, ?, ?, ?)
    """, (post_id, get_member_id(), body, image_filename))
    db.commit()
    notify_mentions(body, get_member_id(), 'Family Discussion')
    
    # Send notification to post author
    post_row = db.execute("SELECT author_id, title FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if post_row and post_row['author_id'] != get_member_id():
        commenter = session.get('username', 'Someone')
        send_push_to_user(post_row['author_id'], '💬 New Reply', f"{commenter} replied to your post: {post_row['title'][:50]}")
    
    db.close()
    
    flash('Comment added!', 'success')
    return redirect(url_for('view_discussion_post', post_id=post_id))

@app.route('/family/discussions/react', methods=['POST'])
def toggle_reaction():
    """Toggle a reaction on a post or comment"""
    if not require_family_access():
        return {'error': 'Not logged in'}, 401
    
    if session.get('status') == 'pending':
        return {'error': 'Pending members cannot react'}, 403
    
    data = request.get_json() or {}
    post_id = data.get('post_id')
    comment_id = data.get('comment_id')
    reaction_type = data.get('reaction_type', 'like')
    
    if not post_id and not comment_id:
        return {'error': 'post_id or comment_id required'}, 400
    
    if reaction_type not in ('like', 'heart'):
        return {'error': 'Invalid reaction type'}, 400
    
    db = get_db()
    
    # Check if reaction already exists
    existing = db.execute("""
        SELECT id FROM discussion_reactions 
        WHERE (post_id = ? OR comment_id = ?) 
        AND member_id = ? AND reaction_type = ?
    """, (post_id, comment_id, get_member_id(), reaction_type)).fetchone()
    
    if existing:
        # Remove reaction
        db.execute(
            "DELETE FROM discussion_reactions WHERE id = ?",
            (existing['id'],)
        )
    else:
        # Add reaction
        db.execute("""
            INSERT INTO discussion_reactions (post_id, comment_id, member_id, reaction_type)
            VALUES (?, ?, ?, ?)
        """, (post_id, comment_id, get_member_id(), reaction_type))
    
    db.commit()
    
    # Get updated counts
    likes = db.execute("""
        SELECT COUNT(*) FROM discussion_reactions 
        WHERE (post_id = ? OR comment_id = ?) AND reaction_type = 'like'
    """, (post_id, comment_id)).fetchone()[0]
    
    hearts = db.execute("""
        SELECT COUNT(*) FROM discussion_reactions 
        WHERE (post_id = ? OR comment_id = ?) AND reaction_type = 'heart'
    """, (post_id, comment_id)).fetchone()[0]
    
    user_reacted = bool(db.execute("""
        SELECT id FROM discussion_reactions 
        WHERE (post_id = ? OR comment_id = ?) 
        AND member_id = ? AND reaction_type = ?
    """, (post_id, comment_id, get_member_id(), reaction_type)).fetchone())
    
    db.close()
    
    return {
        'likes': likes,
        'hearts': hearts,
        'user_reacted': user_reacted
    }

@app.route('/family/discussions/post/<int:post_id>/delete', methods=['POST'])
@require_role('admin', 'super_admin')
def delete_discussion_post(post_id):
    """Delete a discussion post (admin only)"""
    db = get_db()
    db.execute("UPDATE discussion_posts SET hidden = 1 WHERE id = ?", (post_id,))
    db.commit()
    db.close()
    
    flash('Post deleted.', 'success')
    return redirect(url_for('family_discussions'))

@app.route('/family/discussions/post/<int:post_id>/lock', methods=['POST'])
@require_role('admin', 'super_admin')
def lock_discussion_post(post_id):
    """Lock/unlock a discussion post (admin only)"""
    db = get_db()
    
    # Get current locked status
    post = db.execute("SELECT locked FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if post:
        new_locked = 1 - post['locked']
        db.execute("UPDATE discussion_posts SET locked = ? WHERE id = ?", (new_locked, post_id))
        db.commit()
    
    db.close()
    
    flash('Post lock status updated.', 'success')
    return redirect(request.referrer or url_for('family_discussions'))

@app.route('/family/discussions/comment/<int:comment_id>/delete', methods=['POST'])
@require_role('admin', 'super_admin')
def delete_discussion_comment(comment_id):
    """Delete a discussion comment (admin only)"""
    db = get_db()
    
    # Get post_id for redirect
    comment = db.execute("SELECT post_id FROM discussion_comments WHERE id = ?", (comment_id,)).fetchone()
    post_id = comment['post_id'] if comment else None
    
    db.execute("UPDATE discussion_comments SET hidden = 1 WHERE id = ?", (comment_id,))
    db.commit()
    db.close()
    
    flash('Comment deleted.', 'success')
    if post_id:
        return redirect(url_for('view_discussion_post', post_id=post_id))
    return redirect(url_for('family_discussions'))

@app.route('/family/discussions/post/<int:post_id>/renew', methods=['POST'])
def renew_discussion_post(post_id):
    """Renew an expiring post (author or admin only)"""
    if not require_family_access():
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get post
    post = db.execute(
        "SELECT author_id, category_id, expires_at FROM discussion_posts WHERE id = ?",
        (post_id,)
    ).fetchone()
    
    if not post:
        flash('Post not found.', 'error')
        db.close()
        return redirect(url_for('family_discussions'))
    
    # Check permission (author or admin)
    if post['author_id'] != get_member_id() and not is_admin():
        flash('You do not have permission to renew this post.', 'error')
        db.close()
        return redirect(url_for('view_discussion_post', post_id=post_id))
    
    # Get category to get expiry days
    category = db.execute(
        "SELECT expiry_days FROM discussion_categories WHERE id = ?",
        (post['category_id'],)
    ).fetchone()
    
    if category and category['expiry_days']:
        from datetime import timedelta
        new_expires_at = (datetime.now() + timedelta(days=category['expiry_days'])).isoformat()
        db.execute(
            "UPDATE discussion_posts SET expires_at = ? WHERE id = ?",
            (new_expires_at, post_id)
        )
        db.commit()
        flash('Post renewed!', 'success')
    else:
        flash('This post does not expire.', 'error')
    
    db.close()
    return redirect(url_for('view_discussion_post', post_id=post_id))


# ========== Member Discussions Routes ==========

@app.route('/members/discussions')
def member_discussions():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    db = get_db()
    categories = db.execute(
        "SELECT * FROM discussion_categories WHERE section='member' ORDER BY sort_order"
    ).fetchall()
    # For each category, get 3 most recent posts with author name + comment count
    cats_with_posts = []
    for cat in categories:
        posts = db.execute("""
            SELECT dp.*, m.name as author_name,
                   (SELECT COUNT(*) FROM discussion_comments dc WHERE dc.post_id = dp.id AND dc.hidden=0) as comment_count
            FROM discussion_posts dp
            LEFT JOIN members m ON dp.author_id = m.id
            WHERE dp.category_id = ? AND dp.hidden = 0 AND dp.archived = 0
            ORDER BY dp.created_at DESC LIMIT 3
        """, (cat['id'],)).fetchall()
        cats_with_posts.append({'category': dict(cat), 'posts': [dict(p) for p in posts]})
    db.close()
    return render_template('member_discussions.html',
        cats_with_posts=cats_with_posts,
        username=session.get('username', ''),
        role=session.get('role', 'member'))

@app.route('/members/discussions/new', methods=['GET', 'POST'])
def new_member_discussion():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    db = get_db()
    categories = db.execute(
        "SELECT * FROM discussion_categories WHERE section='member' ORDER BY sort_order"
    ).fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        category_id = request.form.get('category_id', '').strip()
        if not all([title, body, category_id]):
            flash('Please fill in all fields.', 'error')
            return render_template('member_discussion_new.html', categories=[dict(c) for c in categories], username=session.get('username',''), role=session.get('role','member'))
        from datetime import datetime
        db.execute(
            "INSERT INTO discussion_posts (category_id, author_id, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (category_id, session['user_id'], title, body, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        db.commit()

        # Determine the right notification category from the slug
        cat_row = db.execute("SELECT slug FROM discussion_categories WHERE id = ?", [category_id]).fetchone()
        slug = cat_row['slug'] if cat_row else ''
        if 'union' in slug:
            notify_cat = 'member_discuss_union'
        elif 'social' in slug or 'off-duty' in slug:
            notify_cat = 'member_discuss_social'
        elif 'question' in slug:
            notify_cat = 'member_discuss_questions'
        else:
            notify_cat = 'member_discuss_general'

        db.close()
        send_push_notification(notify_cat, 'New Discussion', title)
        
        flash('Discussion posted!', 'success')
        return redirect(url_for('member_discussions'))
    db.close()
    return render_template('member_discussion_new.html',
        categories=[dict(c) for c in categories],
        username=session.get('username',''),
        role=session.get('role','member'))

@app.route('/members/discussions/post/<int:post_id>')
def view_member_discussion(post_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    db = get_db()
    post = db.execute("""
        SELECT dp.*, m.name as author_name, m.profile_photo as author_photo,
               dc.name as category_name
        FROM discussion_posts dp
        LEFT JOIN members m ON dp.author_id = m.id
        LEFT JOIN discussion_categories dc ON dp.category_id = dc.id
        WHERE dp.id = ? AND dp.hidden = 0
    """, (post_id,)).fetchone()
    if not post:
        db.close()
        flash('Discussion not found.', 'error')
        return redirect(url_for('member_discussions'))
    comments = db.execute("""
        SELECT dc.*, m.name as author_name, m.profile_photo as author_photo
        FROM discussion_comments dc
        LEFT JOIN members m ON dc.author_id = m.id
        WHERE dc.post_id = ? AND dc.hidden = 0
        ORDER BY dc.created_at ASC
    """, (post_id,)).fetchall()
    db.close()
    return render_template('member_discussion_post.html',
        post=dict(post),
        comments=[dict(c) for c in comments],
        username=session.get('username',''),
        role=session.get('role','member'),
        current_user_id=session.get('user_id'))

@app.route('/members/discussions/post/<int:post_id>/comment', methods=['POST'])
def add_member_discussion_comment(post_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    body = request.form.get('body', '').strip()
    if not body:
        flash('Comment cannot be empty.', 'error')
        return redirect(url_for('view_member_discussion', post_id=post_id))
    from datetime import datetime
    db = get_db()
    db.execute(
        "INSERT INTO discussion_comments (post_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
        (post_id, session['user_id'], body, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    db.commit()
    notify_mentions(body, session['user_id'], 'Members Discussion')
    
    # Send notification to post author
    post = db.execute("SELECT author_id, title FROM discussion_posts WHERE id = ?", (post_id,)).fetchone()
    if post and post['author_id'] != session['user_id']:
        commenter = session.get('username', 'Someone')
        send_push_to_user(post['author_id'], '💬 New Reply', f"{commenter} replied to your post: {post['title'][:50]}")
    
    db.close()
    return redirect(url_for('view_member_discussion', post_id=post_id))


# ========== Background Task: Check Expiring Posts ==========

def check_expiring_posts():
    """
    Check for discussion posts about to expire and send notification emails to authors.
    Runs once per day (simple check on startup, then daily).
    """
    while True:
        try:
            db = get_db()
            
            # Find posts expiring in next 7 days that haven't been notified yet
            seven_days_from_now = (datetime.now() + timedelta(days=7)).isoformat()
            
            expiring_posts = db.execute("""
                SELECT dp.id, dp.title, dp.expires_at, m.email, m.name
                FROM discussion_posts dp
                JOIN members m ON dp.author_id = m.id
                WHERE dp.expires_at IS NOT NULL
                AND dp.expires_at < ?
                AND dp.expires_at > datetime('now')
                AND dp.archived = 0
                AND dp.expiry_notified = 0
            """, (seven_days_from_now,)).fetchall()
            
            for post in expiring_posts:
                # Send email to author
                html_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #B91C1C;">Your Post is Expiring Soon</h2>
                        <p>Hello {post['name']},</p>
                        <p>Your post <strong>"{post['title']}"</strong> in the family discussion forum is expiring on <strong>{post['expires_at']}</strong>.</p>
                        <p>If you'd like to keep this post active, you can renew it by visiting the forum.</p>
                        <p>Posts that expire are automatically archived and hidden from view.</p>
                        <p>—<br>Local 3494 Family Portal</p>
                    </div>
                </body>
                </html>
                """
                
                send_email(
                    post['email'],
                    f"Your post '{post['title']}' is expiring soon",
                    html_body
                )
                
                # Mark as notified
                db.execute(
                    "UPDATE discussion_posts SET expiry_notified=1 WHERE id=?",
                    (post['id'],)
                )
                db.commit()
            
            db.close()
        except Exception as e:
            print(f"[ERROR] check_expiring_posts failed: {str(e)}")
        
        # Sleep for 24 hours before next check
        threading.Event().wait(86400)


def start_background_tasks():
    """Start background tasks in separate threads"""
    expiry_thread = threading.Thread(target=check_expiring_posts, daemon=True)
    expiry_thread.start()


def run_migrations():
    """Run database migrations safely at startup"""
    db = get_db()
    
    # Add note column to event_signups if it doesn't exist
    try:
        db.execute("ALTER TABLE event_signups ADD COLUMN note TEXT DEFAULT ''")
        db.commit()
    except Exception:
        # Column already exists, that's fine
        pass
    
    # Create event_messages table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception:
        # Table already exists, that's fine
        pass
    
    # Create general_meetings table for General Membership Meetings
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS general_meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_date TEXT NOT NULL,
                meeting_time TEXT NOT NULL,
                location TEXT NOT NULL,
                agenda TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception:
        # Table already exists, that's fine
        pass
    
    # Create push_tokens table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS push_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES members(id)
            )
        """)
        db.commit()
    except Exception:
        pass

    # Create notification_preferences table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, category),
                FOREIGN KEY (user_id) REFERENCES members(id)
            )
        """)
        db.commit()
    except Exception:
        pass

    # Create family_community_events table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS family_community_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                event_date TEXT NOT NULL,
                event_time TEXT,
                location TEXT,
                category TEXT DEFAULT 'social',
                created_by_type TEXT NOT NULL,
                created_by_id INTEGER NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception:
        pass

    # Create family_event_rsvps table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS family_event_rsvps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                user_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, user_type, user_id)
            )
        """)
        db.commit()
    except Exception:
        pass

    # Add section column to discussion_categories if it doesn't exist
    try:
        db.execute("ALTER TABLE discussion_categories ADD COLUMN section TEXT DEFAULT 'family'")
        db.commit()
    except Exception:
        pass  # Column already exists

    # Seed member discussion categories if not already present
    member_cats = db.execute(
        "SELECT COUNT(*) FROM discussion_categories WHERE section='member'"
    ).fetchone()[0]
    if member_cats == 0:
        member_categories = [
            ('General', 'member-general', 'General member discussion', None, 10, 'member'),
            ('Union Business', 'union-business', 'Union news, votes, and official topics', None, 11, 'member'),
            ('Off-Duty & Social', 'off-duty-social', 'Social plans, hobbies, off-duty life', None, 12, 'member'),
            ('Questions', 'member-questions', 'Questions for fellow members', None, 13, 'member'),
        ]
        for name, slug, desc, expiry, order, section in member_categories:
            try:
                db.execute(
                    "INSERT INTO discussion_categories (name, slug, description, expiry_days, sort_order, section) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, slug, desc, expiry, order, section)
                )
            except Exception:
                pass  # Slug conflict, skip
        db.commit()

    # Create member_documents table if it doesn't exist
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS member_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                url TEXT NOT NULL,
                added_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception:
        pass

    # Seed initial document if table is empty
    try:
        doc_count = db.execute("SELECT COUNT(*) FROM member_documents").fetchone()[0]
        if doc_count == 0:
            db.execute("""
                INSERT INTO member_documents (title, description, url, added_by)
                VALUES (?, ?, ?, ?)
            """, ('Josh Reese Shift Coverage', 'Shift coverage tracking spreadsheet', 'https://docs.google.com/spreadsheets/d/1YIQnBuG7POQXiBgp2B831KpRV9rREZFzCz8HXS9AB_k/edit?usp=drivesdk', None))
            db.commit()
    except Exception:
        pass

    db.close()


# ========== PWA and Push Notifications Routes ==========

@app.route('/firebase-messaging-sw.js')
def firebase_sw():
    """Serve Firebase messaging service worker from root path"""
    return send_from_directory('static', 'firebase-messaging-sw.js', mimetype='application/javascript')


def send_push_notification(category, title, body, exclude_user_id=None):
    """Send push notification to all users with that category enabled."""
    if not _firebase_initialized:
        print(f"[PUSH STUB] Firebase not initialized. Would send: {title}: {body}")
        return
    
    db = get_db()
    try:
        # Build query to get tokens for users who have this category enabled
        query = """
            SELECT pt.token FROM push_tokens pt
            JOIN notification_preferences np ON pt.user_id = np.user_id
            WHERE np.category = ? AND np.enabled = 1
        """
        params = [category]
        if exclude_user_id:
            query += " AND pt.user_id != ?"
            params.append(exclude_user_id)
        
        tokens = [row['token'] for row in db.execute(query, params).fetchall()]
        
        if not tokens:
            return
        
        # Send to each token individually (handle invalid tokens)
        invalid_tokens = []
        for token in tokens:
            try:
                message = fcm_messaging.Message(
                    webpush=fcm_messaging.WebpushConfig(
                        data={
                            'title': title,
                            'body': body,
                        }
                    ),
                    token=token,
                )
                fcm_messaging.send(message)
            except Exception as e:
                err_str = str(e)
                if 'registration-token-not-registered' in err_str or 'invalid-registration-token' in err_str:
                    invalid_tokens.append(token)
                else:
                    print(f"[PUSH] Error sending to token: {e}")
        
        # Clean up invalid tokens
        if invalid_tokens:
            for token in invalid_tokens:
                db.execute("DELETE FROM push_tokens WHERE token = ?", [token])
            db.commit()
    
    except Exception as e:
        print(f"[PUSH] Error in send_push_notification: {e}")
    
    finally:
        db.close()


def send_push_to_user(user_id, title, body):
    """Send push notification to a specific user by user_id."""
    if not _firebase_initialized:
        print(f"[PUSH STUB] Would send to user {user_id}: {title}: {body}")
        return
    db = get_db()
    try:
        tokens = [row['token'] for row in db.execute(
            "SELECT token FROM push_tokens WHERE user_id = ?", (user_id,)
        ).fetchall()]
        if not tokens:
            return
        invalid_tokens = []
        for token in tokens:
            try:
                message = fcm_messaging.Message(
                    webpush=fcm_messaging.WebpushConfig(
                        data={
                            'title': title,
                            'body': body,
                        }
                    ),
                    token=token,
                )
                fcm_messaging.send(message)
            except Exception as e:
                if 'INVALID_ARGUMENT' in str(e) or 'NOT_FOUND' in str(e):
                    invalid_tokens.append(token)
        if invalid_tokens:
            for t in invalid_tokens:
                db.execute("DELETE FROM push_tokens WHERE token = ?", (t,))
            db.commit()
    except Exception as e:
        print(f"[PUSH ERROR] send_push_to_user: {e}")
    finally:
        db.close()


def notify_mentions(body, sender_user_id, context_label):
    """Parse @username mentions from body and send targeted push notifications."""
    import re
    mentions = re.findall(r'@(\w+)', body)
    if not mentions:
        return
    db = get_db()
    try:
        for username in set(mentions):  # deduplicate
            user = db.execute(
                "SELECT id FROM members WHERE LOWER(name) = LOWER(?)", (username,)
            ).fetchone()
            if user and user['id'] != sender_user_id:
                send_push_to_user(
                    user['id'],
                    '🔔 You were mentioned',
                    f"Someone mentioned you in {context_label}"
                )
    except Exception as e:
        print(f"[PUSH ERROR] notify_mentions: {e}")
    finally:
        db.close()


@app.route('/api/push/register', methods=['POST'])
def register_push_token():
    """Register a device token for push notifications"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    data = request.get_json()
    token = data.get('token', '').strip()
    
    if not token:
        return jsonify({'success': False, 'error': 'Token required'}), 400
    
    try:
        db = get_db()
        
        # Check if token already exists for this user
        existing = db.execute(
            "SELECT id FROM push_tokens WHERE user_id = ? AND token = ?",
            (session['user_id'], token)
        ).fetchone()
        
        if not existing:
            # Delete old tokens from the same FCM instance (same device/browser)
            instance_id = token.split(':')[0] if ':' in token else token[:20]
            db.execute(
                "DELETE FROM push_tokens WHERE user_id = ? AND token LIKE ?",
                (session['user_id'], instance_id + '%')
            )
            # Insert new token
            db.execute(
                "INSERT INTO push_tokens (user_id, token) VALUES (?, ?)",
                (session['user_id'], token)
            )
            # Seed default notification preferences for this user if not already set
            default_categories = [
                'events', 'meetings', 'member_chat',
                'member_discuss_general', 'member_discuss_union', 'member_discuss_social', 'member_discuss_questions',
                'family_events', 'family_community_events', 'family_chat', 'family_discuss', 'family_announcements'
            ]
            for cat in default_categories:
                exists = db.execute(
                    "SELECT id FROM notification_preferences WHERE user_id = ? AND category = ?",
                    (session['user_id'], cat)
                ).fetchone()
                if not exists:
                    db.execute(
                        "INSERT INTO notification_preferences (user_id, category, enabled) VALUES (?, ?, 1)",
                        (session['user_id'], cat)
                    )
            db.commit()
        
        db.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"[ERROR] Failed to register push token: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/push/preferences', methods=['GET', 'POST'])
def push_preferences():
    """Get or update notification preferences"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    db = get_db()
    
    if request.method == 'GET':
        # Return current preferences
        prefs = db.execute(
            "SELECT category, enabled FROM notification_preferences WHERE user_id = ?",
            (session['user_id'],)
        ).fetchall()
        db.close()
        
        prefs_dict = {p['category']: bool(p['enabled']) for p in prefs}
        return jsonify({'success': True, 'preferences': prefs_dict})
    
    elif request.method == 'POST':
        # Update preferences
        data = request.get_json()
        
        # Get all notification categories
        categories = [
            'events', 'meetings', 'member_chat',
            'member_discuss_general', 'member_discuss_union', 'member_discuss_social', 'member_discuss_questions',
            'family_events', 'family_chat', 'family_discuss', 'family_announcements',
            'family_community_events'
        ]
        
        try:
            for category in categories:
                enabled = data.get(category, True)
                
                # Check if preference exists
                existing = db.execute(
                    "SELECT id FROM notification_preferences WHERE user_id = ? AND category = ?",
                    (session['user_id'], category)
                ).fetchone()
                
                if existing:
                    # Update existing
                    db.execute(
                        "UPDATE notification_preferences SET enabled = ? WHERE user_id = ? AND category = ?",
                        (1 if enabled else 0, session['user_id'], category)
                    )
                else:
                    # Insert new
                    db.execute(
                        "INSERT INTO notification_preferences (user_id, category, enabled) VALUES (?, ?, ?)",
                        (session['user_id'], category, 1 if enabled else 0)
                    )
            
            db.commit()
            db.close()
            return jsonify({'success': True})
        except Exception as e:
            db.close()
            print(f"[ERROR] Failed to update preferences: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/members/get-the-app')
def member_get_app():
    """Member PWA install page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get user's current notification preferences
    prefs = db.execute(
        "SELECT category, enabled FROM notification_preferences WHERE user_id = ?",
        (session['user_id'],)
    ).fetchall()
    db.close()
    
    # Convert to dict with defaults
    preferences = {
        'events': True,
        'meetings': True,
        'member_chat': True,
        'member_discuss_general': True,
        'member_discuss_union': True,
        'member_discuss_social': True,
        'member_discuss_questions': True,
        'family_events': True,
        'family_community_events': True,
        'family_chat': True,
        'family_discuss': True,
        'family_announcements': True
    }
    
    for pref in prefs:
        preferences[pref['category']] = bool(pref['enabled'])
    
    return render_template('member_get_app.html',
        username=session.get('username', ''),
        role=session.get('role', 'member'),
        preferences=preferences)


@app.route('/family/get-the-app')
def family_get_app():
    """Family PWA install page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get user's current notification preferences
    prefs = db.execute(
        "SELECT category, enabled FROM notification_preferences WHERE user_id = ?",
        (session['user_id'],)
    ).fetchall()
    db.close()
    
    # Convert to dict with defaults
    preferences = {
        'family_events': True,
        'family_community_events': True,
        'family_chat': True,
        'family_discuss': True,
        'family_announcements': True
    }
    
    for pref in prefs:
        if pref['category'] in preferences:
            preferences[pref['category']] = bool(pref['enabled'])
    
    return render_template('family_get_app.html',
        username=session.get('username', ''),
        role=session.get('role', 'member'),
        preferences=preferences)


# Run migrations at module level so they execute on WSGI import (PythonAnywhere).
# NOTE: init_db() is NOT here — too slow (seeding). New tables go in run_migrations() instead.
# NOTE: start_background_tasks() is NOT here — WSGI may import app multiple times, causing duplicate threads.
run_migrations()

if __name__ == '__main__':
    update_agent_status('union-website-skeleton', 'done', 'Union website skeleton built successfully')
    print("🔥 Union Website starting on http://localhost:5002")
    app.run(host='0.0.0.0', port=5002, debug=False)
