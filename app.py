"""
app.py
The Streamlit web interface for the TeamUp app.

Flow:
- Not logged in  -> Log in / Sign up screen
- Player         -> Events (host with slot booking, join), Venues (search), My Profile
- Venue owner    -> My Venues (price, hours, bookings, stats), Add Venue
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from database import (
    init_db,
    create_account,
    get_account_by_email,
    get_profile,
    save_profile,
    add_venue,
    get_venues,
    get_venues_by_owner,
    get_venue_price_map,
    get_venue_schedule_map,
    update_venue_listing,
    get_venue_stats,
    add_event,
    create_booked_event,
    get_booked_hours,
    get_events,
    get_events_by_venue,
    cancel_event_as_host,
    cancel_event_as_owner,
    join_event,
    get_participants_map,
)
from auth import hash_password, verify_password, is_valid_email, clean_phone
from geocode import geocode_address, haversine_distance

# Must be the first Streamlit command in the script
st.set_page_config(page_title="TeamUp", page_icon="🏆")

SPORTS = ["Football", "Cricket", "Badminton", "Tennis", "Basketball"]
SKILLS = ["Basic", "Medium", "Advanced"]

# Slots are shown in this time zone (the server itself runs in UTC)
APP_TIMEZONE = "Asia/Kolkata"
BOOKING_WINDOW_DAYS = 30  # how far ahead a slot can be booked


@st.cache_resource
def setup_database():
    """Runs the table setup once per server start, not on every click."""
    init_db()
    return True


setup_database()


# ============================================================
# SMALL HELPERS
# ============================================================
def fmt_hour(hour):
    """18 -> '6:00 PM', 0 or 24 -> '12:00 AM'"""
    hour = hour % 24
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:00 {suffix}"


def duration_label(hours):
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def slot_label(start_hour, duration):
    return f"{fmt_hour(start_hour)} – {fmt_hour(start_hour + duration)}"


def price_label(price):
    """How a venue's price is shown to people everywhere in the app."""
    if price is None:
        return "Price not listed"
    if price == 0:
        return "Free"
    return f"₹{price}/hour"


def show_flash():
    """Shows a one-time success message saved before a page refresh."""
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def validate_schedule(open_hour, close_hour, durations):
    """Returns an error message, or None if the schedule makes sense."""
    if close_hour <= open_hour:
        return "Closing time must be after opening time."
    if not durations:
        return "Pick at least one booking length."
    if min(durations) > close_hour - open_hour:
        return "The opening hours are shorter than the shortest booking length."
    return None


def price_input(key, current=None):
    """The per-hour price box, used when adding a venue and when editing one."""
    return st.number_input(
        "Price per hour (₹) — shown to players",
        min_value=0,
        max_value=100000,
        value=int(current) if current is not None else 500,
        step=50,
        help="Enter 0 if the venue is free to use.",
        key=key,
    )


def schedule_form(key_prefix, current=None):
    """
    The opening hours and booking length inputs, used both when adding a venue
    and when editing one. current = existing schedule dict, or None for defaults.
    Returns (open_hour, close_hour, durations).
    """
    cur = current or {"open": 6, "close": 22, "durations": [1, 2]}

    col_open, col_close = st.columns(2)
    with col_open:
        open_hour = st.selectbox(
            "Opens at", list(range(0, 24)), index=cur["open"],
            format_func=fmt_hour, key=f"{key_prefix}_open",
        )
    with col_close:
        close_hour = st.selectbox(
            "Closes at", list(range(1, 25)), index=cur["close"] - 1,
            format_func=fmt_hour, key=f"{key_prefix}_close",
        )
    durations = st.multiselect(
        "Booking lengths you offer", [1, 2, 3, 4],
        default=cur["durations"], format_func=duration_label,
        key=f"{key_prefix}_durations",
    )
    return open_hour, close_hour, durations


def get_free_starts(schedule, booked_hours, booking_date, duration, today, now_hour):
    """
    Works out which start times are still free:
    inside opening hours, not overlapping a booked hour, and not already past today.
    """
    starts = []
    for start in range(schedule["open"], schedule["close"] - duration + 1):
        if booking_date == today and start <= now_hour:
            continue
        if any(hour in booked_hours for hour in range(start, start + duration)):
            continue
        starts.append(start)
    return starts


def venue_card(name, location, capacity, price, online_booking, distance=None, owner=None):
    """One venue as shown to players, with the price up front."""
    st.markdown(f"**📍 {name}**")
    st.write(location + (f" · {distance:.1f} km away" if distance is not None else ""))
    details = f"💰 **{price_label(price)}** · 👥 capacity {capacity}"
    if online_booking:
        details += " · ⚡ online booking"
    st.write(details)
    if owner:
        st.caption(f"Owner: {owner}")


# ============================================================
# LOG IN / SIGN UP
# ============================================================
def start_session(account):
    """Remembers who is logged in (account = id, name, email, phone, hash, role)."""
    st.session_state["user"] = {
        "id": account[0],
        "name": account[1],
        "email": account[2],
        "role": account[5],
    }


def show_auth_screen():
    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Log in"):
            account = get_account_by_email(email.strip().lower())
            # Same message for "no such email" and "wrong password" on purpose,
            # so nobody can use this form to discover which emails are registered.
            if account and verify_password(password, account[4]):
                start_session(account)
                st.rerun()
            else:
                st.error("Incorrect email or password.")

    with tab_signup:
        role_label = st.radio(
            "I am a...", ["Player", "Venue owner"], horizontal=True, key="signup_role"
        )
        role = "owner" if role_label == "Venue owner" else "player"

        name = st.text_input("Full name", key="signup_name")
        email = st.text_input("Email", key="signup_email")
        phone_label = "Phone number (required)" if role == "owner" else "Phone number (optional)"
        phone = st.text_input(phone_label, key="signup_phone")
        password = st.text_input("Password (min 8 characters)", type="password", key="signup_password")
        confirm = st.text_input("Confirm password", type="password", key="signup_confirm")

        if st.button("Create account"):
            email_clean = email.strip().lower()
            phone_clean = clean_phone(phone) if phone.strip() else ""

            if not name.strip():
                st.error("Please enter your name.")
            elif not is_valid_email(email_clean):
                st.error("Please enter a valid email address.")
            elif phone.strip() and phone_clean is None:
                st.error("Phone number should have 10-15 digits (a leading + is fine).")
            elif role == "owner" and not phone_clean:
                st.error("Venue owners need to add a phone number.")
            elif len(password) < 8:
                st.error("Password must be at least 8 characters.")
            elif password != confirm:
                st.error("Passwords don't match.")
            else:
                account_id = create_account(
                    name.strip(), email_clean, phone_clean, hash_password(password), role
                )
                if account_id is None:
                    st.error("An account with this email already exists. Try logging in.")
                else:
                    # Log the new user straight in
                    start_session((account_id, name.strip(), email_clean, phone_clean, "", role))
                    st.rerun()


# ============================================================
# PLAYER: MY PROFILE
# ============================================================
def show_profile(user):
    st.subheader("My player profile")
    saved = get_profile(user["id"])  # (sport, skill_level) or None

    sport = st.selectbox(
        "Primary sport",
        SPORTS,
        index=SPORTS.index(saved[0]) if saved and saved[0] in SPORTS else 0,
    )
    skill = st.selectbox(
        "Skill level",
        SKILLS,
        index=SKILLS.index(saved[1]) if saved and saved[1] in SKILLS else 0,
    )

    if st.button("Save profile"):
        save_profile(user["id"], user["name"], sport, skill)
        st.success("Profile saved!")

    if not saved:
        st.info("Save your profile once, then you can join games.")


# ============================================================
# PLAYER: VENUES (search nearby + list)
# ============================================================
def show_venues_for_players():
    st.subheader("Find venues near you")

    my_location = st.text_input("Your location (e.g. street/area, city)")
    radius_km = st.slider("Search radius (km)", min_value=1, max_value=50, value=5)
    prices = get_venue_price_map()
    schedules = get_venue_schedule_map()

    if st.button("Search nearby venues"):
        if not my_location:
            st.error("Please enter your location")
        else:
            with st.spinner("Finding your coordinates..."):
                my_coords = geocode_address(my_location)

            if not my_coords:
                st.error("Couldn't find that location. Try being more specific.")
            else:
                my_lat, my_lon = my_coords
                nearby = []
                for v in get_venues():
                    v_id, v_name, v_loc, v_cap, v_lat, v_lon, v_owner = v
                    if v_lat is None or v_lon is None:
                        continue  # venue couldn't be located on the map
                    distance = haversine_distance(my_lat, my_lon, v_lat, v_lon)
                    if distance <= radius_km:
                        nearby.append((distance, v_id, v_name, v_loc, v_cap))

                nearby.sort(key=lambda x: x[0])  # closest first

                if not nearby:
                    st.info(f"No venues found within {radius_km}km.")
                for distance, v_id, v_name, v_loc, v_cap in nearby:
                    venue_card(
                        v_name, v_loc, v_cap, prices.get(v_id),
                        v_id in schedules, distance=distance,
                    )

    st.divider()
    st.subheader("All venues")
    venues = get_venues()
    if not venues:
        st.caption("No venues added yet.")
    for v in venues:
        v_id, v_name, v_loc, v_cap, v_lat, v_lon, v_owner = v
        venue_card(v_name, v_loc, v_cap, prices.get(v_id), v_id in schedules, owner=v_owner or "unknown")


# ============================================================
# PLAYER: EVENTS (host + join)
# ============================================================
def show_events(user):
    show_flash()

    venues = get_venues()
    venue_options = {v[0]: v[1] for v in venues}  # id -> name
    venue_lookup = {v[0]: (v[1], v[2]) for v in venues}  # id -> (name, address)
    price_map = get_venue_price_map()
    schedules = get_venue_schedule_map()

    now = datetime.now(ZoneInfo(APP_TIMEZONE))
    today = now.date()

    def venue_option_label(vid):
        """Dropdown text: venue name plus its price, so the cost is clear before choosing."""
        if vid is None:
            return "No venue (public place / own arrangement)"
        if vid in price_map:
            return f"{venue_options[vid]} — {price_label(price_map[vid])}"
        return venue_options[vid]

    # ---------- Host a game ----------
    st.subheader("Host a new game")
    event_sport = st.selectbox("Sport", SPORTS, key="event_sport")
    event_skill = st.selectbox("Skill level needed", ["Any"] + SKILLS, key="event_skill")
    max_players = st.number_input("Max players", min_value=2, max_value=30, value=10)

    # None = no venue (public park, or any place the host arranges themselves)
    selected_venue_id = st.selectbox(
        "Venue",
        options=[None] + list(venue_options.keys()),
        format_func=venue_option_label,
        key="event_venue",
    )
    schedule = schedules.get(selected_venue_id) if selected_venue_id else None

    # Defaults, filled in below depending on whether the venue takes online bookings
    date_time = ""
    booking_date = None
    start_hour = None
    duration = None
    free_starts = []

    if schedule:
        # --- Venue with online booking: pick a real, free slot ---
        st.markdown(
            f"**Book a slot** — open {fmt_hour(schedule['open'])} to {fmt_hour(schedule['close'])}, "
            f"₹{schedule['price']}/hour"
        )
        booking_date = st.date_input(
            "Date",
            value=today,
            min_value=today,
            max_value=today + timedelta(days=BOOKING_WINDOW_DAYS),
            key=f"event_date_{selected_venue_id}",
        )
        duration = st.selectbox(
            "How long?",
            schedule["durations"],
            format_func=duration_label,
            key=f"event_duration_{selected_venue_id}",
        )
        booked_hours = get_booked_hours(selected_venue_id, booking_date)
        free_starts = get_free_starts(schedule, booked_hours, booking_date, duration, today, now.hour)

        if free_starts:
            # The nonce changes after each booking so the slot list refreshes cleanly
            nonce = st.session_state.get("booking_nonce", 0)
            start_hour = st.selectbox(
                "Time slot",
                free_starts,
                format_func=lambda s: slot_label(s, duration),
                key=f"event_slot_{selected_venue_id}_{booking_date}_{duration}_{nonce}",
            )
            st.info(
                f"💰 ₹{schedule['price'] * duration} total "
                f"(₹{schedule['price']}/hour). Payment is arranged directly with the venue for now."
            )
        else:
            st.warning("No free slots for that date and length. Try another date or duration.")
    else:
        # --- Public place, or a venue without online booking: type the time ---
        date_time = st.text_input("Date & time (e.g. Sat 6pm)")
        if selected_venue_id:
            listed = price_map.get(selected_venue_id)
            price_note = f" Listed price: {price_label(listed)}." if listed is not None else ""
            st.caption(
                "This venue hasn't set up online booking yet, so enter a time "
                f"and confirm it with the venue directly.{price_note}"
            )

    if st.button("Create event"):
        if schedule:
            if start_hour is None:
                st.error("Please pick a time slot first.")
            else:
                time_text = f"{booking_date.strftime('%a %d %b')}, {slot_label(start_hour, duration)}"
                booked = create_booked_event(
                    user["id"], user["name"], event_sport, event_skill, max_players,
                    selected_venue_id, booking_date, start_hour, duration,
                    schedule["price"] * duration, time_text,
                )
                if booked:
                    st.session_state["booking_nonce"] = st.session_state.get("booking_nonce", 0) + 1
                    st.session_state["flash"] = f"Event created and slot booked: {time_text}"
                    st.rerun()
                else:
                    st.error("Sorry, someone just booked that slot. Please pick another one.")
        else:
            if date_time.strip():
                add_event(
                    user["id"], user["name"], event_sport, event_skill,
                    max_players, date_time.strip(), selected_venue_id,
                )
                st.session_state["flash"] = "Event created!"
                st.rerun()
            else:
                st.error("Please enter a date and time.")

    st.divider()
    st.subheader("Upcoming games")

    events = get_events()
    participants_map = get_participants_map()
    profile = get_profile(user["id"])  # (sport, skill) or None

    if not events:
        st.caption("No events yet. Host one above!")

    for event in events:
        event_id, e_host, e_sport, e_skill, e_max, e_datetime, e_venue_id, e_status, e_host_id = event
        joined = participants_map.get(event_id, [])  # [(account_id, name), ...]
        joined_names = [n for _, n in joined]
        already_joined = any(a == user["id"] for a, _ in joined)
        venue_name, venue_location = venue_lookup.get(e_venue_id, ("No venue set", ""))
        venue_price = f" · {price_label(price_map[e_venue_id])}" if e_venue_id in price_map else ""

        # --- Event header ---
        if e_status == "cancelled":
            st.markdown(f"### ⚽ ~~{e_sport} ({e_skill}) hosted by {e_host}~~ ❌ CANCELLED")
        else:
            st.markdown(f"### ⚽ {e_sport} ({e_skill}) hosted by {e_host}")

        st.write(f"📍 {venue_name}" + (f" — {venue_location}" if venue_location else "") + venue_price)
        st.write(f"🕒 {e_datetime}  |  👥 {len(joined)}/{e_max} joined")
        if joined_names:
            st.caption("Joined: " + ", ".join(joined_names))

        # --- Actions ---
        if e_status == "cancelled":
            st.caption("This event has been cancelled.")
        else:
            # Only the host sees the cancel button (and the database double-checks it)
            if e_host_id == user["id"]:
                if st.button("Cancel my event", key=f"host_cancel_{event_id}"):
                    cancel_event_as_host(event_id, user["id"])
                    st.rerun()

            if already_joined:
                st.success("✅ You've joined this game")
            elif len(joined) >= e_max:
                st.caption("This event is full.")
            elif not profile:
                st.info("Save your player profile (My Profile tab) to join games.")
            else:
                # Skill mismatch is a heads-up, not a block
                if e_skill != "Any" and profile[1] != e_skill:
                    st.caption(
                        f"⚠️ This game is for {e_skill} players and your profile is "
                        f"{profile[1]}. You can still join."
                    )
                if st.button("Join event", key=f"join_{event_id}"):
                    join_event(event_id, user["id"], user["name"])
                    st.rerun()

        st.divider()


# ============================================================
# OWNER: ADD VENUE
# ============================================================
def show_add_venue(user):
    st.subheader("Add a venue")

    venue_name = st.text_input("Venue name (e.g. Green Park Turf)")
    venue_location = st.text_input("Location / address (be specific - include city)")
    venue_capacity = st.number_input("Max capacity", min_value=2, max_value=100, value=20)

    st.markdown("**Pricing**")
    price = price_input("new_venue_price")

    st.markdown("**Online booking** (optional)")
    booking_on = st.checkbox(
        "Let players book time slots online", value=True, key="new_venue_booking",
        help="Turn this off if you only want to list your venue and price for now.",
    )
    open_hour, close_hour, durations = None, None, []
    if booking_on:
        st.caption("Players can book slots inside these hours.")
        open_hour, close_hour, durations = schedule_form("new_venue")

    if st.button("Save venue"):
        schedule_error = validate_schedule(open_hour, close_hour, durations) if booking_on else None

        if not venue_name.strip() or not venue_location.strip():
            st.error("Please fill in the venue name and location.")
        elif schedule_error:
            st.error(schedule_error)
        else:
            with st.spinner("Looking up location..."):
                coords = geocode_address(venue_location)
            lat, lon = coords if coords else (None, None)
            add_venue(
                venue_name.strip(), venue_location.strip(), venue_capacity,
                lat, lon, user["id"], user["name"],
                price, open_hour, close_hour, durations,
            )
            if coords:
                st.success(f"Venue '{venue_name}' added and located on the map!")
            else:
                st.warning(
                    "Venue saved, but we couldn't pinpoint that address on the map "
                    "(try adding the area and city). It won't show up in 'near me' searches yet."
                )


# ============================================================
# OWNER: MY VENUES (price, hours, bookings, stats)
# ============================================================
def show_my_venues(user):
    show_flash()
    st.subheader("My venues & bookings")

    my_venues = get_venues_by_owner(user["id"])
    participants_map = get_participants_map()
    prices = get_venue_price_map()
    schedules = get_venue_schedule_map()

    if not my_venues:
        st.info("You haven't added a venue yet. Use the 'Add Venue' tab.")
        return

    for v in my_venues:
        v_id, v_name, v_loc, v_cap, v_lat, v_lon, v_owner = v
        price = prices.get(v_id)
        sched = schedules.get(v_id)

        st.markdown(f"### 📍 {v_name}")
        st.write(f"{v_loc} — capacity {v_cap}")

        # --- Price up front, so owners see exactly what players see ---
        st.markdown(f"#### 💰 {price_label(price)}")
        if price is None:
            st.warning("No price listed yet, so players can't see what this venue costs. Add it below.")

        # --- Quick stats ---
        count, hours, value = get_venue_stats(v_id)
        col1, col2, col3 = st.columns(3)
        col1.metric("Bookings", count)
        col2.metric("Hours booked", hours)
        col3.metric("Booked value", f"₹{value}")
        st.caption("Booked value is the listed price of confirmed slots. Payments aren't collected through the app yet.")

        # --- Booking summary ---
        if sched:
            lengths = ", ".join(duration_label(d) for d in sched["durations"])
            st.write(f"🕒 Online booking: {fmt_hour(sched['open'])} – {fmt_hour(sched['close'])} · {lengths}")
        else:
            st.caption("Online booking is off for this venue. Players see the price and arrange a time with the host.")

        # --- Editor ---
        with st.expander("Edit price & booking hours", expanded=price is None):
            new_price = price_input(f"price_{v_id}", price)
            booking_on = st.checkbox(
                "Let players book time slots online",
                value=sched is not None,
                key=f"booking_on_{v_id}",
            )
            new_open, new_close, new_durations = None, None, []
            if booking_on:
                new_open, new_close, new_durations = schedule_form(f"sched_{v_id}", sched)

            if st.button("Save changes", key=f"save_listing_{v_id}"):
                error = validate_schedule(new_open, new_close, new_durations) if booking_on else None
                if error:
                    st.error(error)
                else:
                    update_venue_listing(
                        v_id, user["id"], new_price, new_open, new_close, new_durations
                    )
                    st.session_state["flash"] = f"Saved changes for {v_name}."
                    st.rerun()

        # --- Bookings ---
        st.markdown("**Bookings**")
        bookings = get_events_by_venue(v_id)
        if not bookings:
            st.caption("No bookings at this venue yet.")
        for booking in bookings:
            b_id, b_host, b_sport, b_skill, b_max, b_datetime, _, b_status = booking
            joined_count = len(participants_map.get(b_id, []))
            label = "❌ CANCELLED — " if b_status == "cancelled" else ""
            st.write(
                f"{label}⚽ {b_sport} ({b_skill}) — hosted by {b_host} — "
                f"{b_datetime} — {joined_count}/{b_max} joined"
            )
            if b_status != "cancelled":
                if st.button("Cancel this booking", key=f"owner_cancel_{b_id}"):
                    cancel_event_as_owner(b_id, user["id"])
                    st.rerun()
        st.divider()


# ============================================================
# MAIN: decide what to show
# ============================================================
st.title("🏆 TeamUp")
st.caption("🚧 Test version — this is an early prototype, so things may change. Thanks for trying it!")

user = st.session_state.get("user")

if not user:
    show_auth_screen()
    st.stop()  # nothing below runs until someone is logged in

col_name, col_logout = st.columns([3, 1])
with col_name:
    role_text = "Venue owner" if user["role"] == "owner" else "Player"
    st.write(f"Signed in as **{user['name']}** ({role_text})")
with col_logout:
    if st.button("Log out"):
        st.session_state.pop("user", None)
        st.rerun()

# Players and owners see different menus
if user["role"] == "owner":
    sections = ["My Venues", "Add Venue"]
else:
    sections = ["Events", "Venues", "My Profile"]

section = st.radio("Menu", sections, horizontal=True, label_visibility="collapsed")
st.divider()

if section == "My Venues":
    show_my_venues(user)
elif section == "Add Venue":
    show_add_venue(user)
elif section == "Events":
    show_events(user)
elif section == "Venues":
    show_venues_for_players()
elif section == "My Profile":
    show_profile(user)