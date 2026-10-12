"""
database.py
Handles all database setup and operations for the TeamUp app.
Now uses PostgreSQL hosted on Supabase, so data persists permanently
(unlike the old local SQLite file, which reset on every redeploy).

The connection string is read from Streamlit secrets (DATABASE_URL),
so no password is ever written in the code itself.
"""

import psycopg2
import streamlit as st


def get_connection():
    """
    Opens a connection to the Supabase Postgres database.
    DATABASE_URL comes from .streamlit/secrets.toml locally,
    and from the app's Secrets settings on Streamlit Cloud.
    """
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def run_query(sql, params=(), fetch=False):
    """
    Small helper that runs one SQL statement and cleans up after itself.
    - fetch=False: for INSERT/UPDATE/CREATE (saves changes, returns nothing)
    - fetch=True:  for SELECT (returns all rows as a list of tuples)
    The connection is always closed, even if the query fails.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


def init_db():
    """
    Creates all tables if they don't already exist.
    Safe to call every time the app starts - it never wipes existing data.
    Note the Postgres differences from SQLite:
    SERIAL = auto-incrementing id, DOUBLE PRECISION = decimal number.
    """
    run_query("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            sport TEXT NOT NULL,
            skill_level TEXT NOT NULL
        )
    """)

    run_query("""
        CREATE TABLE IF NOT EXISTS venues (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            capacity INTEGER,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            owner_name TEXT
        )
    """)

    run_query("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            host_name TEXT NOT NULL,
            sport TEXT NOT NULL,
            skill_level TEXT NOT NULL,
            max_players INTEGER NOT NULL,
            date_time TEXT NOT NULL,
            venue_id INTEGER,
            status TEXT DEFAULT 'active'
        )
    """)

    run_query("""
        CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events (id),
            user_name TEXT NOT NULL
        )
    """)


# ---------- USER FUNCTIONS ----------

def add_user(name, sport, skill_level):
    run_query(
        "INSERT INTO users (name, sport, skill_level) VALUES (%s, %s, %s)",
        (name, sport, skill_level)
    )


def get_users():
    """Returns (name, sport, skill_level) for every user."""
    return run_query("SELECT name, sport, skill_level FROM users", fetch=True)


# ---------- VENUE FUNCTIONS ----------

def add_venue(name, location, capacity, latitude=None, longitude=None, owner_name=None):
    run_query(
        """INSERT INTO venues (name, location, capacity, latitude, longitude, owner_name)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (name, location, capacity, latitude, longitude, owner_name)
    )


def get_venues():
    """Returns (id, name, location, capacity, latitude, longitude, owner_name)."""
    return run_query("""
        SELECT id, name, location, capacity, latitude, longitude, owner_name
        FROM venues
    """, fetch=True)


def get_venues_by_owner(owner_name):
    """Returns venues belonging to a specific owner name (case-insensitive match)."""
    return run_query("""
        SELECT id, name, location, capacity, latitude, longitude, owner_name
        FROM venues
        WHERE LOWER(owner_name) = LOWER(%s)
    """, (owner_name,), fetch=True)


# ---------- EVENT FUNCTIONS ----------

def add_event(host_name, sport, skill_level, max_players, date_time, venue_id):
    run_query(
        """INSERT INTO events (host_name, sport, skill_level, max_players, date_time, venue_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (host_name, sport, skill_level, max_players, date_time, venue_id)
    )


def get_events():
    """Returns (id, host_name, sport, skill_level, max_players, date_time, venue_id, status)."""
    return run_query("""
        SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status
        FROM events
        ORDER BY id
    """, fetch=True)


def get_events_by_venue(venue_id):
    """Returns all events (any status) booked at a specific venue."""
    return run_query("""
        SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status
        FROM events
        WHERE venue_id = %s
        ORDER BY id
    """, (venue_id,), fetch=True)


def cancel_event(event_id):
    """Marks an event as cancelled instead of deleting it, so history is preserved."""
    run_query("UPDATE events SET status = 'cancelled' WHERE id = %s", (event_id,))


# ---------- PARTICIPANT (JOIN) FUNCTIONS ----------

def join_event(event_id, user_name):
    run_query(
        "INSERT INTO participants (event_id, user_name) VALUES (%s, %s)",
        (event_id, user_name)
    )


def get_participants(event_id):
    """Returns a list of user names who have joined a specific event."""
    rows = run_query(
        "SELECT user_name FROM participants WHERE event_id = %s",
        (event_id,), fetch=True
    )
    return [r[0] for r in rows]


def has_joined(event_id, user_name):
    return user_name in get_participants(event_id)