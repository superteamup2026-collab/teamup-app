"""
database.py
All database setup and operations for the TeamUp app (PostgreSQL on Supabase).
The connection string is read from Streamlit secrets (DATABASE_URL).

Tables:
- accounts         : login details (name, email, phone, password hash, role)
- users            : a player's sport + skill level, linked to an account
- venues           : turfs/arenas: price, optional booking hours, description
- events           : games that players host
- participants     : who joined which game
- bookings         : a reserved slot at a venue. status 'confirmed' = a player's
                     booking, 'blocked' = hours the owner has taken off the market
- booking_hours    : one row per booked/blocked hour. A uniqueness rule on
                     (venue, date, hour) is what makes double-booking impossible.
- messages         : chat messages inside an event
- venue_promotions : discounts an owner offers
- venue_photos     : venue pictures (stored compressed inside the database)

Permission checks (is this really the host? the owner?) live in the SQL itself,
so they can't be bypassed from the screen.
"""

import psycopg2
import streamlit as st

MAX_PHOTOS_PER_VENUE = 5


def get_connection():
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def run_query(sql, params=(), fetch=False):
    """
    Runs one SQL statement and always closes the connection afterwards.
    fetch=True returns all rows (for SELECT, or INSERT ... RETURNING).
    fetch=False returns how many rows were changed (used for yes/no checks).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        result = cursor.fetchall() if fetch else cursor.rowcount
        conn.commit()
        return result
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
    # Venue price, schedule and description: hours are whole numbers 0-24,
    # durations are stored like '1,2'
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS open_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS close_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS price_per_hour INTEGER",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS durations TEXT",
    "ALTER TABLE venues ADD COLUMN IF NOT EXISTS description TEXT",
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
    # A short reason for blocked hours (e.g. "pitch maintenance")
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS note TEXT",
    # One row per booked or blocked hour. UNIQUE(...) is the double-booking guard.
    """CREATE TABLE IF NOT EXISTS booking_hours (
        id SERIAL PRIMARY KEY,
        booking_id INTEGER NOT NULL REFERENCES bookings (id) ON DELETE CASCADE,
        venue_id INTEGER NOT NULL,
        booking_date DATE NOT NULL,
        hour INTEGER NOT NULL,
        UNIQUE (venue_id, booking_date, hour)
    )""",
    # Chat inside an event
    """CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        event_id INTEGER NOT NULL REFERENCES events (id),
        account_id INTEGER NOT NULL,
        sender_name TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS messages_event_idx ON messages (event_id)",
    # Owner discounts. start_hour/end_hour empty = the whole day.
    # day_type is 'all', 'weekdays' or 'weekends'.
    """CREATE TABLE IF NOT EXISTS venue_promotions (
        id SERIAL PRIMARY KEY,
        venue_id INTEGER NOT NULL REFERENCES venues (id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        discount_percent INTEGER NOT NULL,
        day_type TEXT NOT NULL DEFAULT 'all',
        start_hour INTEGER,
        end_hour INTEGER,
        valid_until DATE,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    # Venue pictures, already resized and compressed by the app
    """CREATE TABLE IF NOT EXISTS venue_photos (
        id SERIAL PRIMARY KEY,
        venue_id INTEGER NOT NULL REFERENCES venues (id) ON DELETE CASCADE,
        image BYTEA NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
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
              price_per_hour, open_hour, close_hour, durations, description):
    """
    For venues without online booking, pass open_hour=None, close_hour=None
    and durations=[].
    """
    run_query(
        """INSERT INTO venues (name, location, capacity, latitude, longitude, owner_id, owner_name,
                               price_per_hour, open_hour, close_hour, durations, description)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (name, location, capacity, latitude, longitude, owner_id, owner_name,
         price_per_hour, open_hour, close_hour, _durations_text(durations), description or None),
    )


def get_all_venues():
    """
    Returns every venue as a dictionary (one query for everything the app needs).
    "online_booking" is True when the owner has set up opening hours and booking lengths.
    """
    rows = run_query(
        """SELECT id, name, location, capacity, latitude, longitude, owner_id, owner_name,
                  price_per_hour, open_hour, close_hour, durations, description
           FROM venues ORDER BY id""",
        fetch=True,
    )
    venues = []
    for (venue_id, name, location, capacity, lat, lon, owner_id, owner_name,
         price, open_hour, close_hour, durations_text, description) in rows:
        durations = [int(d) for d in durations_text.split(",") if d] if durations_text else []
        venues.append({
            "id": venue_id,
            "name": name,
            "location": location,
            "capacity": capacity,
            "lat": lat,
            "lon": lon,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "price": price,
            "open": open_hour,
            "close": close_hour,
            "durations": durations,
            "online_booking": open_hour is not None and close_hour is not None and bool(durations),
            "description": description or "",
        })
    return venues


def update_venue_listing(venue_id, owner_id, price_per_hour, open_hour, close_hour,
                         durations, description):
    """
    Updates a venue's price, description and booking schedule ONLY if it belongs
    to this owner. To switch online booking off, pass open_hour=None,
    close_hour=None and durations=[].
    """
    run_query(
        """UPDATE venues
           SET price_per_hour = %s, open_hour = %s, close_hour = %s,
               durations = %s, description = %s
           WHERE id = %s AND owner_id = %s""",
        (price_per_hour, open_hour, close_hour, _durations_text(durations),
         description or None, venue_id, owner_id),
    )


def get_venue_stats_map(owner_id):
    """
    Returns {venue_id: (confirmed bookings, hours booked, booked value in rupees)}
    for all of one owner's venues, in a single query. Blocked hours don't count.
    """
    rows = run_query(
        """SELECT b.venue_id, COUNT(*), COALESCE(SUM(b.duration_hours), 0), COALESCE(SUM(b.price), 0)
           FROM bookings b
           JOIN venues v ON v.id = b.venue_id
           WHERE v.owner_id = %s AND b.status = 'confirmed'
           GROUP BY b.venue_id""",
        (owner_id,),
        fetch=True,
    )
    return {venue_id: (count, hours, value) for venue_id, count, hours, value in rows}


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
    Returns True on success, or False if someone else already booked (or the owner
    blocked) one of those hours: the uniqueness rule rejects it and everything is
    rolled back.
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
    """Returns the set of hours already taken (booked or blocked) at a venue on a date."""
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


# ---------- BLOCKED HOURS (owner takes hours off the market) ----------

def create_block(venue_id, owner_id, booking_date, start_hour, duration, note):
    """
    Blocks hours at a venue (maintenance, private use...).
    Returns "ok", "taken" (some of those hours are already booked or blocked),
    or "denied" (this account doesn't own the venue).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM venues WHERE id = %s AND owner_id = %s", (venue_id, owner_id)
        )
        if cursor.fetchone() is None:
            return "denied"

        cursor.execute(
            """INSERT INTO bookings (venue_id, event_id, booked_by, booking_date,
                                     start_hour, duration_hours, price, status, note)
               VALUES (%s, NULL, %s, %s, %s, %s, NULL, 'blocked', %s) RETURNING id""",
            (venue_id, owner_id, booking_date, start_hour, duration, note or None),
        )
        booking_id = cursor.fetchone()[0]

        for hour in range(start_hour, start_hour + duration):
            cursor.execute(
                """INSERT INTO booking_hours (booking_id, venue_id, booking_date, hour)
                   VALUES (%s, %s, %s, %s)""",
                (booking_id, venue_id, booking_date, hour),
            )

        conn.commit()
        return "ok"
    except psycopg2.IntegrityError:
        conn.rollback()
        return "taken"
    finally:
        conn.close()


def get_blocks_map(owner_id, from_date):
    """
    Returns {venue_id: [(block_id, date, start_hour, duration, note), ...]}
    for one owner's upcoming blocks.
    """
    rows = run_query(
        """SELECT b.venue_id, b.id, b.booking_date, b.start_hour, b.duration_hours, b.note
           FROM bookings b
           JOIN venues v ON v.id = b.venue_id
           WHERE v.owner_id = %s AND b.status = 'blocked' AND b.booking_date >= %s
           ORDER BY b.booking_date, b.start_hour""",
        (owner_id, from_date),
        fetch=True,
    )
    result = {}
    for venue_id, block_id, booking_date, start_hour, duration, note in rows:
        result.setdefault(venue_id, []).append((block_id, booking_date, start_hour, duration, note))
    return result


def remove_block(block_id, owner_id):
    """Un-blocks hours ONLY if the block belongs to one of this owner's venues."""
    # The hour rows disappear automatically (ON DELETE CASCADE), freeing the slots.
    run_query(
        """DELETE FROM bookings
           WHERE id = %s AND status = 'blocked'
             AND venue_id IN (SELECT id FROM venues WHERE owner_id = %s)""",
        (block_id, owner_id),
    )


# ---------- PARTICIPANT (JOIN) FUNCTIONS ----------

def join_event(event_id, account_id, user_name):
    """
    Joins a game. Returns True if it worked, or False if the game is full,
    cancelled, or this account has already joined.
    """
    changed = run_query(
        """INSERT INTO participants (event_id, account_id, user_name)
           SELECT %s, %s, %s
           WHERE NOT EXISTS (SELECT 1 FROM participants WHERE event_id = %s AND account_id = %s)
             AND EXISTS (
                 SELECT 1 FROM events e
                 WHERE e.id = %s AND e.status = 'active'
                   AND (SELECT COUNT(*) FROM participants WHERE event_id = e.id) < e.max_players
             )""",
        (event_id, account_id, user_name, event_id, account_id, event_id),
    )
    return changed == 1


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


# ---------- CHAT ----------

def post_message(event_id, account_id, sender_name, body):
    """
    Posts a chat message. Only the host or a joined player of an ACTIVE event
    can post (checked here in SQL). Returns True if it was saved.
    """
    changed = run_query(
        """INSERT INTO messages (event_id, account_id, sender_name, body)
           SELECT %s, %s, %s, %s
           WHERE EXISTS (
               SELECT 1 FROM events e
               WHERE e.id = %s AND e.status = 'active'
                 AND (e.host_id = %s
                      OR EXISTS (SELECT 1 FROM participants p
                                 WHERE p.event_id = e.id AND p.account_id = %s))
           )""",
        (event_id, account_id, sender_name, body, event_id, account_id, account_id),
    )
    return changed == 1


def get_messages_map(event_ids):
    """
    Returns {event_id: [(account_id, sender_name, body, created_at), ...]}
    oldest first, for the given events (one query for all of them).
    """
    if not event_ids:
        return {}
    rows = run_query(
        """SELECT event_id, account_id, sender_name, body, created_at
           FROM messages WHERE event_id = ANY(%s) ORDER BY id""",
        (list(event_ids),),
        fetch=True,
    )
    result = {}
    for event_id, account_id, sender_name, body, created_at in rows:
        result.setdefault(event_id, []).append((account_id, sender_name, body, created_at))
    return result


# ---------- PROMOTIONS ----------

def add_promotion(venue_id, owner_id, title, discount_percent, day_type,
                  start_hour, end_hour, valid_until):
    """Adds a discount to a venue ONLY if the venue belongs to this owner. Returns True if saved."""
    changed = run_query(
        """INSERT INTO venue_promotions
               (venue_id, title, discount_percent, day_type, start_hour, end_hour, valid_until)
           SELECT %s, %s, %s, %s, %s::int, %s::int, %s::date
           WHERE EXISTS (SELECT 1 FROM venues WHERE id = %s AND owner_id = %s)""",
        (venue_id, title, discount_percent, day_type, start_hour, end_hour, valid_until,
         venue_id, owner_id),
    )
    return changed == 1


def get_promotions_map():
    """
    Returns {venue_id: [promotion dictionaries]} for every venue, in one query.
    The app decides which ones are currently live (active and not expired).
    """
    rows = run_query(
        """SELECT id, venue_id, title, discount_percent, day_type,
                  start_hour, end_hour, valid_until, active
           FROM venue_promotions ORDER BY id""",
        fetch=True,
    )
    result = {}
    for (promo_id, venue_id, title, percent, day_type,
         start_hour, end_hour, valid_until, active) in rows:
        result.setdefault(venue_id, []).append({
            "id": promo_id,
            "title": title,
            "discount_percent": percent,
            "day_type": day_type,
            "start_hour": start_hour,
            "end_hour": end_hour,
            "valid_until": valid_until,
            "active": active,
        })
    return result


def set_promotion_active(promo_id, owner_id, active):
    """Pauses or resumes a promotion ONLY if it is on one of this owner's venues."""
    run_query(
        """UPDATE venue_promotions SET active = %s
           WHERE id = %s AND venue_id IN (SELECT id FROM venues WHERE owner_id = %s)""",
        (active, promo_id, owner_id),
    )


def delete_promotion(promo_id, owner_id):
    """Removes a promotion ONLY if it is on one of this owner's venues."""
    run_query(
        """DELETE FROM venue_promotions
           WHERE id = %s AND venue_id IN (SELECT id FROM venues WHERE owner_id = %s)""",
        (promo_id, owner_id),
    )


# ---------- PHOTOS ----------

def add_venue_photo(venue_id, owner_id, image_bytes):
    """
    Saves a photo ONLY if the venue belongs to this owner and the venue is below
    the photo limit. Returns True if it was saved.
    """
    changed = run_query(
        """INSERT INTO venue_photos (venue_id, image)
           SELECT %s, %s
           WHERE EXISTS (SELECT 1 FROM venues WHERE id = %s AND owner_id = %s)
             AND (SELECT COUNT(*) FROM venue_photos WHERE venue_id = %s) < %s""",
        (venue_id, psycopg2.Binary(image_bytes), venue_id, owner_id,
         venue_id, MAX_PHOTOS_PER_VENUE),
    )
    return changed == 1


def get_all_photos_map():
    """Returns {venue_id: [(photo_id, image_bytes), ...]} for every venue, in one query."""
    rows = run_query(
        "SELECT id, venue_id, image FROM venue_photos ORDER BY id",
        fetch=True,
    )
    result = {}
    for photo_id, venue_id, image in rows:
        result.setdefault(venue_id, []).append((photo_id, bytes(image)))
    return result


def delete_venue_photo(photo_id, owner_id):
    """Removes a photo ONLY if it is on one of this owner's venues."""
    run_query(
        """DELETE FROM venue_photos
           WHERE id = %s AND venue_id IN (SELECT id FROM venues WHERE owner_id = %s)""",
        (photo_id, owner_id),
    )
