"""
database.py
All database setup and operations for the TeamUp app (PostgreSQL on Supabase).
The connection string is read from Streamlit secrets (DATABASE_URL).

Tables:
- accounts       : login details (name, email, phone, password hash, role)
- users          : a player's sport + skill level, linked to an account
- venues         : turfs/arenas, with a per-hour price and optional booking hours
- events         : games that players host
- participants   : who joined which game
- bookings       : a reserved time slot at a venue (linked to an event)
- booking_hours  : one row per booked hour. A uniqueness rule on
                   (venue, date, hour) is what makes double-booking impossible.
"""

import psycopg2
import streamlit as st


def get_connection():
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def run_query(sql, params=(), fetch=False):
    """
    Runs one SQL statement and always closes the connection afterwards.
    fetch=True returns all rows (for SELECT, or INSERT ... RETURNING).
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


# All table definitions in one list, run on a single connection.
# "IF NOT EXISTS" makes every statement safe to repeat, and the ALTER TABLE
# lines upgrade your existing tables without deleting any data.
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS accounts (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        phone TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'player',
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        sport TEXT NOT NULL,
        skill_level TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS venues (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT NOT NULL,
        capacity INTEGER,
        latitude DOUBLE PRECISION,
        longitude DOUBLE PRECISION,
        owner_name TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        host_name TEXT NOT NULL,
        sport TEXT NOT NULL,
        skill_level TEXT NOT NULL,
        max_players INTEGER NOT NULL,
        date_time TEXT NOT NULL,
        venue_id INTEGER,
        status TEXT DEFAULT 'active'
    )""",
    """CREATE TABLE IF NOT EXISTS participants (
        id SERIAL PRIMARY KEY,
        event_id INTEGER NOT NULL REFERENCES events (id),
        user_name TEXT NOT NULL
    )""",
    # Link existing tables to accounts
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS account_id INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS owner_id INTEGER",
    "ALTER TABLE events ADD COLUMN IF NOT EXISTS host_id INTEGER",
    "ALTER TABLE participants ADD COLUMN IF NOT EXISTS account_id INTEGER",
    # Venue price and schedule: hours are whole numbers 0-24, durations like '1,2'
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS open_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS close_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS price_per_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS durations TEXT",
    """CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        venue_id INTEGER NOT NULL REFERENCES venues (id),
        event_id INTEGER REFERENCES events (id),
        booked_by INTEGER,
        booking_date DATE NOT NULL,
        start_hour INTEGER NOT NULL,
        duration_hours INTEGER NOT NULL,
        price INTEGER,
        status TEXT NOT NULL DEFAULT 'confirmed',
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    # One row per booked hour. UNIQUE(...) is the double-booking guard.
    """CREATE TABLE IF NOT EXISTS booking_hours (
        id SERIAL PRIMARY KEY,
        booking_id INTEGER NOT NULL REFERENCES bookings (id) ON DELETE CASCADE,
        venue_id INTEGER NOT NULL,
        booking_date DATE NOT NULL,
        hour INTEGER NOT NULL,
        UNIQUE (venue_id, booking_date, hour)
    )""",
    # One player profile per account
    "CREATE UNIQUE INDEX IF NOT EXISTS users_account_id_key ON users (account_id)",
]


def init_db():
    """Creates/upgrades all tables. Safe to run repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for statement in SCHEMA_STATEMENTS:
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


# ---------- ACCOUNT FUNCTIONS ----------

def create_account(name, email, phone, password_hash, role):
    """Creates a new account and returns its id, or None if the email is taken."""
    try:
        rows = run_query(
            """INSERT INTO accounts (name, email, phone, password_hash, role)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (name, email, phone, password_hash, role),
            fetch=True,
        )
        return rows[0][0]
    except psycopg2.IntegrityError:
        return None  # the UNIQUE rule on email rejected a duplicate


def get_account_by_email(email):
    """Returns (id, name, email, phone, password_hash, role) or None."""
    rows = run_query(
        "SELECT id, name, email, phone, password_hash, role FROM accounts WHERE email = %s",
        (email,),
        fetch=True,
    )
    return rows[0] if rows else None


# ---------- PLAYER PROFILE FUNCTIONS ----------

def get_profile(account_id):
    """Returns (sport, skill_level) for this account, or None if not saved yet."""
    rows = run_query(
        "SELECT sport, skill_level FROM users WHERE account_id = %s",
        (account_id,),
        fetch=True,
    )
    return rows[0] if rows else None


def save_profile(account_id, name, sport, skill_level):
    """Creates the player's profile, or updates it if it already exists."""
    run_query(
        """INSERT INTO users (account_id, name, sport, skill_level)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (account_id)
           DO UPDATE SET name = EXCLUDED.name,
                         sport = EXCLUDED.sport,
                         skill_level = EXCLUDED.skill_level""",
        (account_id, name, sport, skill_level),
    )


# ---------- VENUE FUNCTIONS ----------

def _durations_text(durations):
    """[2, 1] -> '1,2'. An empty list becomes None, meaning online booking is off."""
    return ",".join(str(d) for d in sorted(durations)) if durations else None


def add_venue(name, location, capacity, latitude, longitude, owner_id, owner_name,
              price_per_hour, open_hour, close_hour, durations):
    """
    price_per_hour is always set. For venues without online booking, pass
    open_hour=None, close_hour=None and durations=[].
    """
    run_query(
        """INSERT INTO venues (name, location, capacity, latitude, longitude, owner_id, owner_name,
                               price_per_hour, open_hour, close_hour, durations)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (name, location, capacity, latitude, longitude, owner_id, owner_name,
         price_per_hour, open_hour, close_hour, _durations_text(durations)),
    )


def get_venues():
    """Returns (id, name, location, capacity, latitude, longitude, owner_name)."""
    return run_query(
        """SELECT id, name, location, capacity, latitude, longitude, owner_name
           FROM venues ORDER BY id""",
        fetch=True,
    )


def get_venues_by_owner(owner_id):
    """Returns the venues that belong to one owner account (same columns as get_venues)."""
    return run_query(
        """SELECT id, name, location, capacity, latitude, longitude, owner_name
           FROM venues WHERE owner_id = %s ORDER BY id""",
        (owner_id,),
        fetch=True,
    )


def get_venue_price_map():
    """Returns {venue_id: price_per_hour} for every venue whose owner has listed a price."""
    rows = run_query(
        "SELECT id, price_per_hour FROM venues WHERE price_per_hour IS NOT NULL",
        fetch=True,
    )
    return {venue_id: price for venue_id, price in rows}


def get_venue_schedule_map():
    """
    Returns {venue_id: {"open", "close", "price", "durations"}} for venues that
    have online booking switched on. Other venues are left out.
    """
    rows = run_query(
        """SELECT id, open_hour, close_hour, price_per_hour, durations
           FROM venues
           WHERE open_hour IS NOT NULL AND close_hour IS NOT NULL
             AND durations IS NOT NULL AND durations <> ''""",
        fetch=True,
    )
    result = {}
    for venue_id, open_hour, close_hour, price, durations in rows:
        result[venue_id] = {
            "open": open_hour,
            "close": close_hour,
            "price": price or 0,
            "durations": [int(d) for d in durations.split(",") if d],
        }
    return result


def update_venue_listing(venue_id, owner_id, price_per_hour, open_hour, close_hour, durations):
    """
    Updates a venue's price and booking schedule ONLY if it belongs to this owner.
    To switch online booking off, pass open_hour=None, close_hour=None, durations=[].
    """
    run_query(
        """UPDATE venues
           SET price_per_hour = %s, open_hour = %s, close_hour = %s, durations = %s
           WHERE id = %s AND owner_id = %s""",
        (price_per_hour, open_hour, close_hour, _durations_text(durations), venue_id, owner_id),
    )


def get_venue_stats(venue_id):
    """Returns (confirmed bookings, total hours booked, total booked value in rupees)."""
    rows = run_query(
        """SELECT COUNT(*), COALESCE(SUM(duration_hours), 0), COALESCE(SUM(price), 0)
           FROM bookings WHERE venue_id = %s AND status = 'confirmed'""",
        (venue_id,),
        fetch=True,
    )
    return rows[0]


# ---------- EVENT FUNCTIONS ----------

def add_event(host_id, host_name, sport, skill_level, max_players, date_time, venue_id):
    """A plain event with a typed time and no slot booking (parks, or venues without online booking)."""
    run_query(
        """INSERT INTO events (host_id, host_name, sport, skill_level, max_players, date_time, venue_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (host_id, host_name, sport, skill_level, max_players, date_time, venue_id),
    )


def create_booked_event(host_id, host_name, sport, skill_level, max_players,
                        venue_id, booking_date, start_hour, duration, price, date_time_text):
    """
    Creates an event AND reserves its time slot, all-or-nothing.
    Returns True on success, or False if someone else already booked one of
    those hours (the uniqueness rule rejects it and everything is rolled back).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO events (host_id, host_name, sport, skill_level, max_players, date_time, venue_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (host_id, host_name, sport, skill_level, max_players, date_time_text, venue_id),
        )
        event_id = cursor.fetchone()[0]

        cursor.execute(
            """INSERT INTO bookings (venue_id, event_id, booked_by, booking_date,
                                     start_hour, duration_hours, price)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (venue_id, event_id, host_id, booking_date, start_hour, duration, price),
        )
        booking_id = cursor.fetchone()[0]

        # One row per hour. A clash on any hour raises IntegrityError.
        for hour in range(start_hour, start_hour + duration):
            cursor.execute(
                """INSERT INTO booking_hours (booking_id, venue_id, booking_date, hour)
                   VALUES (%s, %s, %s, %s)""",
                (booking_id, venue_id, booking_date, hour),
            )

        conn.commit()
        return True
    except psycopg2.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_booked_hours(venue_id, booking_date):
    """Returns the set of hours already taken at a venue on a given date."""
    rows = run_query(
        "SELECT hour FROM booking_hours WHERE venue_id = %s AND booking_date = %s",
        (venue_id, booking_date),
        fetch=True,
    )
    return {r[0] for r in rows}


def get_events():
    """Returns (id, host_name, sport, skill_level, max_players, date_time, venue_id, status, host_id)."""
    return run_query(
        """SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status, host_id
           FROM events ORDER BY id""",
        fetch=True,
    )


def get_events_by_venue(venue_id):
    """Returns (id, host_name, sport, skill_level, max_players, date_time, venue_id, status)."""
    return run_query(
        """SELECT id, host_name, sport, skill_level, max_players, date_time, venue_id, status
           FROM events WHERE venue_id = %s ORDER BY id""",
        (venue_id,),
        fetch=True,
    )


def _cancel_event(event_id, condition_sql, condition_params):
    """
    Cancels an event if the permission condition matches, and frees its booked slot.
    Everything happens in one transaction. The condition is always one of two fixed
    strings written below, never user input.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE events SET status = 'cancelled' WHERE id = %s AND " + condition_sql,
            (event_id, *condition_params),
        )
        if cursor.rowcount == 1:  # permission check passed
            cursor.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE event_id = %s", (event_id,)
            )
            # Deleting the hour rows is what makes the slot bookable again
            cursor.execute(
                """DELETE FROM booking_hours
                   WHERE booking_id IN (SELECT id FROM bookings WHERE event_id = %s)""",
                (event_id,),
            )
        conn.commit()
    finally:
        conn.close()


def cancel_event_as_host(event_id, host_id):
    """Cancels an event ONLY if this account is its host."""
    _cancel_event(event_id, "host_id = %s", (host_id,))


def cancel_event_as_owner(event_id, owner_id):
    """Cancels an event ONLY if it is at a venue owned by this account."""
    _cancel_event(
        event_id,
        "venue_id IN (SELECT id FROM venues WHERE owner_id = %s)",
        (owner_id,),
    )


# ---------- PARTICIPANT (JOIN) FUNCTIONS ----------

def join_event(event_id, account_id, user_name):
    run_query(
        "INSERT INTO participants (event_id, account_id, user_name) VALUES (%s, %s, %s)",
        (event_id, account_id, user_name),
    )


def get_participants_map():
    """
    One query for everyone who joined anything.
    Returns {event_id: [(account_id, user_name), ...]}
    """
    rows = run_query(
        "SELECT event_id, account_id, user_name FROM participants ORDER BY id",
        fetch=True,
    )
    result = {}
    for event_id, account_id, user_name in rows:
        result.setdefault(event_id, []).append((account_id, user_name))
    return result