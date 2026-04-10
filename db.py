import sqlite3
import os
import json
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'union.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    
    # Create members table
    db.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            status TEXT DEFAULT 'pending',
            badge_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Try to add role column if it doesn't exist (for backwards compatibility)
    try:
        db.execute("ALTER TABLE members ADD COLUMN role TEXT DEFAULT 'member'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add status column if it doesn't exist
    try:
        db.execute("ALTER TABLE members ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add badge_number column if it doesn't exist
    try:
        db.execute("ALTER TABLE members ADD COLUMN badge_number TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add linked_member_id column (for family accounts)
    try:
        db.execute("ALTER TABLE members ADD COLUMN linked_member_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add years_of_service column
    try:
        db.execute("ALTER TABLE members ADD COLUMN years_of_service INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add rank column
    try:
        db.execute("ALTER TABLE members ADD COLUMN rank TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add profile_photo column
    try:
        db.execute("ALTER TABLE members ADD COLUMN profile_photo TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add has_family_invite column for tracking pending invitations
    try:
        db.execute("ALTER TABLE members ADD COLUMN has_family_invite INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Try to add shift column (Phase 4 - Family Directory)
    try:
        db.execute("ALTER TABLE members ADD COLUMN shift TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Update all existing members to have status = 'active' if status is NULL
    db.execute("UPDATE members SET status = 'active' WHERE status IS NULL")
    db.commit()
    
    # Create family_invitations table
    db.execute("""
        CREATE TABLE IF NOT EXISTS family_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firefighter_id INTEGER NOT NULL,
            invitee_name TEXT NOT NULL,
            invitee_email TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            FOREIGN KEY (firefighter_id) REFERENCES members(id)
        )
    """)
    
    # Create updated events table with new schema
    db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            event_type TEXT DEFAULT 'member',
            signup_enabled INTEGER DEFAULT 0,
            supplies TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (created_by) REFERENCES members(id)
        )
    """)
    
    # Create event_signups table
    db.execute("""
        CREATE TABLE IF NOT EXISTS event_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            signed_up_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(event_id, member_id)
        )
    """)
    
    # Try to add visibility column to events (Phase 4)
    try:
        db.execute("ALTER TABLE events ADD COLUMN visibility TEXT DEFAULT 'public_homepage,member_portal'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Create store_items table
    db.execute("""
        CREATE TABLE IF NOT EXISTS store_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            sizes TEXT,
            store_type TEXT DEFAULT 'public',
            active INTEGER DEFAULT 1,
            image TEXT
        )
    """)
    
    # Create store_orders table
    db.execute("""
        CREATE TABLE IF NOT EXISTS store_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            item_name TEXT NOT NULL,
            size TEXT,
            quantity INTEGER DEFAULT 1,
            price REAL NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_phone TEXT,
            payment_method TEXT,
            order_status TEXT DEFAULT 'pending',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES store_items(id)
        )
    """)
    
    # Create bulletin_posts table
    db.execute("""
        CREATE TABLE IF NOT EXISTS bulletin_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            author_id INTEGER,
            pinned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (author_id) REFERENCES members(id)
        )
    """)
    
    # Create chat_messages table
    db.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (member_id) REFERENCES members(id)
        )
    """)
    
    # Create gallery_images table
    db.execute("""
        CREATE TABLE IF NOT EXISTS gallery_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            caption TEXT,
            category TEXT DEFAULT 'general',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # Create family_announcements table
    db.execute("""
        CREATE TABLE IF NOT EXISTS family_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            author_id INTEGER,
            visibility TEXT DEFAULT 'family_section',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (author_id) REFERENCES members(id)
        )
    """)
    
    # Try to add visibility column to family_announcements (Phase 4)
    try:
        db.execute("ALTER TABLE family_announcements ADD COLUMN visibility TEXT DEFAULT 'family_section'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Create photo_albums table
    db.execute("""
        CREATE TABLE IF NOT EXISTS photo_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (created_by) REFERENCES members(id)
        )
    """)
    
    # Create family_photos table
    db.execute("""
        CREATE TABLE IF NOT EXISTS family_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploader_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            caption TEXT,
            album_id INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (uploader_id) REFERENCES members(id),
            FOREIGN KEY (album_id) REFERENCES photo_albums(id)
        )
    """)
    
    # Create family_chat table
    db.execute("""
        CREATE TABLE IF NOT EXISTS family_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (member_id) REFERENCES members(id)
        )
    """)
    
    # Add columns to family_photos for backwards compatibility
    try:
        db.execute("ALTER TABLE family_photos ADD COLUMN album_id INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        db.execute("ALTER TABLE family_photos ADD COLUMN status TEXT DEFAULT 'pending'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Create discussion_categories table
    db.execute("""
        CREATE TABLE IF NOT EXISTS discussion_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT,
            expiry_days INTEGER DEFAULT NULL,
            sort_order INTEGER DEFAULT 0,
            section TEXT DEFAULT 'family'
        )
    """)
    
    # Create discussion_posts table
    db.execute("""
        CREATE TABLE IF NOT EXISTS discussion_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT DEFAULT NULL,
            archived INTEGER DEFAULT 0,
            locked INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            expiry_notified INTEGER DEFAULT 0,
            FOREIGN KEY (category_id) REFERENCES discussion_categories(id),
            FOREIGN KEY (author_id) REFERENCES members(id)
        )
    """)
    
    # Try to add expiry_notified column for tracking expiry notifications
    try:
        db.execute("ALTER TABLE discussion_posts ADD COLUMN expiry_notified INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Create discussion_comments table
    db.execute("""
        CREATE TABLE IF NOT EXISTS discussion_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            image_filename TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            hidden INTEGER DEFAULT 0,
            FOREIGN KEY (post_id) REFERENCES discussion_posts(id),
            FOREIGN KEY (author_id) REFERENCES members(id)
        )
    """)
    
    # Create discussion_reactions table
    db.execute("""
        CREATE TABLE IF NOT EXISTS discussion_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            comment_id INTEGER,
            member_id INTEGER NOT NULL,
            reaction_type TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES discussion_posts(id),
            FOREIGN KEY (comment_id) REFERENCES discussion_comments(id),
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(post_id, comment_id, member_id, reaction_type)
        )
    """)
    
    # Seed a test admin account if none exists
    existing = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    if existing == 0:
        db.execute("""
            INSERT INTO members (name, email, password, role, status)
            VALUES ('Reese Heaslet', 'reese@local3494.org', ?, 'super_admin', 'active')
        """, (generate_password_hash('test1234'),))
    else:
        # Upgrade reese@local3494.org to super_admin with active status
        db.execute("""
            UPDATE members 
            SET role = 'super_admin', status = 'active' 
            WHERE email = 'reese@local3494.org'
        """)
    
    # Seed gallery images if empty
    gallery_count = db.execute("SELECT COUNT(*) FROM gallery_images").fetchone()[0]
    if gallery_count == 0:
        gallery_data = [
            ('hero.jpg', 'Davis Firefighters serving the community', 'general'),
            ('honor-guard.jpg', 'Local 3494 Honor Guard', 'honor-guard'),
            ('honor-guard-1.jpg', 'Honor Guard at ceremony', 'honor-guard'),
            ('honor-guard-2.jpg', 'Honor Guard detail', 'honor-guard'),
            ('crab-feast.jpg', 'Annual Crab Feast', 'events'),
            ('crab-feast-1.jpg', 'Crab Feast with Thriving Pink Foundation', 'events'),
            ('crab-feast-2.jpg', 'Crab Feast guests', 'events'),
            ('crab-feast-3.jpg', 'Crab Feast auction', 'events'),
            ('turkey-baskets.jpg', 'Annual Thanksgiving Turkey Basket Drive', 'events'),
            ('turkey-1.jpg', 'Turkey basket distribution', 'events'),
            ('turkey-2.jpg', 'Community turkey drive', 'events'),
            ('turkey-3.jpg', 'Thanksgiving community giving', 'events'),
        ]
        for filename, caption, category in gallery_data:
            db.execute("""
                INSERT INTO gallery_images (filename, caption, category)
                VALUES (?, ?, ?)
            """, (filename, caption, category))
    
    # Seed store items if empty
    items_count = db.execute("SELECT COUNT(*) FROM store_items").fetchone()[0]
    if items_count == 0:
        store_data = [
            ('Short Sleeve Tee', 'Friends of Local 3494 short sleeve t-shirt', 25.0, json.dumps(['S','M','L','XL','XXL']), 'public'),
            ('Long Sleeve Tee', 'Friends of Local 3494 long sleeve t-shirt', 30.0, json.dumps(['S','M','L','XL','XXL']), 'public'),
            ('Snapback Hat', 'Local 3494 snapback hat', 25.0, None, 'public'),
            ('Fitted Hat', 'Local 3494 fitted hat', 25.0, None, 'public'),
            ('Crewneck Sweatshirt', 'Friends of Local 3494 crewneck sweatshirt', 40.0, json.dumps(['S','M','L','XL','XXL']), 'public'),
            ('Wives of Local 3494 Tee', 'Exclusive t-shirt for firefighter spouses', 25.0, json.dumps(['S','M','L','XL','XXL']), 'family'),
            ('Kids Apparel', 'Kids clothing and accessories', 20.0, json.dumps(['XS','S','M','L']), 'family'),
        ]
        for name, description, price, sizes, store_type in store_data:
            db.execute("""
                INSERT INTO store_items (name, description, price, sizes, store_type)
                VALUES (?, ?, ?, ?, ?)
            """, (name, description, price, sizes, store_type))
    
    # Seed public events if empty
    events_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if events_count == 0:
        events_data = [
            ('BBQ for NAMI', '2026-04-06', '3:00 PM', 'St. Martins Church', 'BBQ for the National Alliance on Mental Illness. Serving chicken and burgers for approximately 70 people.', 'public'),
            ('BBQ for YOLO Democrats', '2026-04-07', 'TBD', 'Location TBD', 'Barbecuing and slicing meat for 100-120 people.', 'public'),
            ('Workers Day BBQ', '2026-05-01', 'TBD', 'Central Park, Davis', 'BBQ at Central Park with fellow Davis Labor groups for Workers Day.', 'public'),
            ('BBQ for Davis Rotary Club', '2026-05-18', 'TBD', 'Central Park, Davis', 'Barbecuing for the Davis Rotary Club, serving approximately 700-1000 people.', 'public'),
        ]
        for title, event_date, event_time, location, description, event_type in events_data:
            db.execute("""
                INSERT INTO events (title, event_date, event_time, location, description, event_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, event_date, event_time, location, description, event_type))
    
    # Seed discussion categories if empty
    cats_count = db.execute("SELECT COUNT(*) FROM discussion_categories").fetchone()[0]
    if cats_count == 0:
        categories = [
            ('General Discussion', 'general', 'General conversation for families', None, 1, 'family'),
            ('Buy / Sell / Give Away', 'buy-sell', 'Items for sale, trade, or free to good home', 60, 2, 'family'),
            ('Baby / Kids Items', 'baby-kids', 'Baby gear, kids clothes, toys', 60, 3, 'family'),
            ('A Shift Families', 'a-shift', 'For families of A Shift firefighters', None, 4, 'family'),
            ('B Shift Families', 'b-shift', 'For families of B Shift firefighters', None, 5, 'family'),
            ('C Shift Families', 'c-shift', 'For families of C Shift firefighters', None, 6, 'family'),
            ('Recommendations & Resources', 'recommendations', 'Local recommendations, resources, tips', None, 7, 'family'),
        ]
        for name, slug, desc, expiry, order, section in categories:
            db.execute("INSERT INTO discussion_categories (name, slug, description, expiry_days, sort_order, section) VALUES (?, ?, ?, ?, ?, ?)",
                       (name, slug, desc, expiry, order, section))
    
    # Seed default "General Photos" album if empty
    albums_count = db.execute("SELECT COUNT(*) FROM photo_albums").fetchone()[0]
    if albums_count == 0:
        db.execute("INSERT INTO photo_albums (name, description, status) VALUES ('General Photos', 'General family photos', 'approved')")
    
    # Create contact_messages table
    db.execute("""
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            preferred_contact TEXT DEFAULT 'email',
            subject TEXT,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            viewed_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # Create password_reset_tokens table
    db.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (member_id) REFERENCES members(id)
        )
    """)
    
    # Create push_tokens table for PWA notifications
    db.execute("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES members(id)
        )
    """)
    
    # Create notification_preferences table for PWA notification categories
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
    
    # Create member_photos table
    db.execute("""
        CREATE TABLE IF NOT EXISTS member_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploader_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            caption TEXT,
            album_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (uploader_id) REFERENCES members(id),
            FOREIGN KEY (album_id) REFERENCES member_photo_albums(id)
        )
    """)
    
    # Create member_photo_albums table
    db.execute("""
        CREATE TABLE IF NOT EXISTS member_photo_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (created_by) REFERENCES members(id)
        )
    """)
    
    # Create photo_comments table
    db.execute("""
        CREATE TABLE IF NOT EXISTS photo_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            photo_type TEXT NOT NULL,
            commenter_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (commenter_id) REFERENCES members(id)
        )
    """)
    
    db.execute('''CREATE TABLE IF NOT EXISTS general_meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        meeting_time TEXT,
        location TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    db.execute('''CREATE TABLE IF NOT EXISTS family_community_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        event_date TEXT NOT NULL,
        event_time TEXT,
        location TEXT,
        description TEXT,
        created_by INTEGER,
        user_type TEXT DEFAULT 'family',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES members(id)
    )''')

    db.execute('''CREATE TABLE IF NOT EXISTS family_event_rsvps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        user_type TEXT DEFAULT 'family',
        rsvp_status TEXT DEFAULT 'going',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(event_id, user_type, user_id),
        FOREIGN KEY (event_id) REFERENCES family_community_events(id),
        FOREIGN KEY (user_id) REFERENCES members(id)
    )''')

    db.execute('''CREATE TABLE IF NOT EXISTS event_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (event_id) REFERENCES events(id),
        FOREIGN KEY (author_id) REFERENCES members(id)
    )''')

    # Create member_documents table
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

    # Seed initial document if table is empty
    doc_count = db.execute("SELECT COUNT(*) FROM member_documents").fetchone()[0]
    if doc_count == 0:
        db.execute("""
            INSERT INTO member_documents (title, description, url, added_by)
            VALUES (?, ?, ?, ?)
        """, ('Josh Reese Shift Coverage', 'Shift coverage tracking spreadsheet', 'https://docs.google.com/spreadsheets/d/1YIQnBuG7POQXiBgp2B831KpRV9rREZFzCz8HXS9AB_k/edit?usp=drivesdk', None))

    db.commit()
    db.close()
