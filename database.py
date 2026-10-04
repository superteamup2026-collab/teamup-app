"""
database.py
Handles all database setup and operations for the TeamUp app.
Uses SQLite - a lightweight database stored as a single local file (sportsapp.db).
"""

import sqlite3


def get_connection():
    conn = sqlite3.connect("sportsapp.db", check_same_thread=False)
    return conn


def init_db():
    """
    Creates all required tables if they don't already exist.
    Also runs small migrations (adding new columns) safely, so it
    won't break on a database file you've already been using.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sport TEXT NOT NULL,
            skill_level TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            capacity INTEGER,
            latitude REAL,
            longitude REAL,
            owner_name TEXT
        )
    """)

    for column, col_type in [("latitude", "REAL"), ("longitude", "REAL"), ("owner_name", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE venues ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT NOT NULL,
            sport TEXT NOT NULL,
            skill_level TEXT NOT NULL,
            max_players INTEGER NOT NULL,
            date_time TEXT NOT NULL,
            venue_id INTEGER,
            status TEXT DEFAULT 'active'
        )
    """)

    for column, col_type in [("venue_id", "INTEGER"), ("status", "TEXT DEFAULT 'active'")]:
        try:
            cursor.execute(f"ALTER TABLE events ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events (id)
        )
    """)

    conn.commit()
    conn.close()


# ---------- USER FUNCTIONS ----------

def add_user(name, sport, skill_level):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (name, sport, skill_level) VALUES (?, ?, ?)",
        (name, sport, skill_level)
    )
    conn.commit()
    conn.close()


def get_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, sport, skill_level FROM users")
    rows = cursor.fetchall()
    conn.close()
    return rows


# ---------- VENUE FUNCTIONS ----------

def add_venue(name, location, capacity, latitude=None, longitude=None, owner_name=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO venues (name, location, capacity, latitude, longitude, owner_name)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, location, capacity, latitude, longitude, owner_name)
    )
    conn.commit()
    conn.close()


def get_venues():
    """Returns (id, name, location, capacity, latitude, longitude, owner_name)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, location, capacity, latitude, longitude, owner_name
        FROM venues
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_venues_by_owner(owner_name):
    """Returns venues belonging to a specific owner name (case-insensitive match)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, location, capacity, latitude, longitude, owner_name
        FROM venues
        WHERE LOWER(owner_name) = LOWER(?)
    """, (owner_name,))
    rows = cursor.fetchall()
    conn.close()
    return rows


# ---------- EVENT FUNCTIONS ----------

def add_event(host_name, sport, skill_level, max_players, date_time, venue_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO events (host_name, sport, skill_level, max_players, date_time, venue_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (host_name, sport, skill_level, max_players, date_time, venue_id)
    )
    conn.commit()
    conn.close()


def get_events():
    """Returns (id, host_name, sport, skill_level, max_players, date_time, venue_id, status)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status
        FROM events
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_events_by_venue(venue_id):
    """Returns all events (any status) booked at a specific venue."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status
        FROM events
        WHERE venue_id = ?
    """, (venue_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def cancel_event(event_id):
    """Marks an event as cancelled instead of deleting it, so history is preserved."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE events SET status = 'cancelled' WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


# ---------- PARTICIPANT (JOIN) FUNCTIONS ----------

def join_event(event_id, user_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO participants (event_id, user_name) VALUES (?, ?)",
        (event_id, user_name)
    )
    conn.commit()
    conn.close()


def get_participants(event_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_name FROM participants WHERE event_id = ?", (event_id,))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


def has_joined(event_id, user_name):
    return user_name in get_participants(event_id)