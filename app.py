"""
app.py
The Streamlit web interface for the TeamUp app.

Flow:
- Not logged in  -> Log in / Sign up screen
- Player         -> Events (host with slot booking + discounts, join, chat),
                    Venues (search, photos, offers), My Profile
- Venue owner    -> My Venues (bookings, price & hours, promotions, blocked hours,
                    photos, stats), Add Venue
"""

import re
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True
)

from database import (
    MAX_PHOTOS_PER_VENUE,
    init_db,
    create_account,
    get_account_by_email,
    get_profile,
    save_profile,
    add_venue,
    get_all_venues,
    update_venue_listing,
    get_venue_stats_map,
    add_event,
    create_booked_event,
    get_booked_hours,
    get_events,
    cancel_event_as_host,
    cancel_event_as_owner,
    create_block,
    get_blocks_map,
    remove_block,
    join_event,
    get_participants_map,
    post_message,
    get_messages_map,
    add_promotion,
    get_promotions_map,
    set_promotion_active,
    delete_promotion,
    add_venue_photo,
    get_all_photos_map,
    delete_venue_photo,
)
from auth import hash_password, verify_password, is_valid_email, clean_phone
from geocode import geocode_address, haversine_distance

# Must be the first Streamlit command in the script
st.set_page_config(page_title="TeamUp", page_icon="🏆")

SPORTS = ["Football", "Cricket", "Badminton", "Tennis", "Basketball"]
SPORT_ICONS = {
    "Football": "⚽",
    "Cricket": "🏏",
    "Badminton": "🏸",
    "Tennis": "🎾",
    "Basketball": "🏀",
}
SKILLS = ["Basic", "Medium", "Advanced"]

# Times are shown in this time zone (the server itself runs in UTC)
APP_TIMEZONE = "Asia/Kolkata"
BOOKING_WINDOW_DAYS = 30  # how far ahead a slot can be booked or blocked
MAX_UPLOAD_MB = 8  # biggest photo file we'll accept before shrinking it
CHAT_MESSAGES_SHOWN = 30
DAY_TYPES = {"all": "Every day", "weekdays": "Mon–Fri", "weekends": "Sat–Sun"}


@st.cache_resource
def setup_database():
    """Runs the table setup once per server start, not on every click."""
    init_db()
    return True


setup_database()


@st.cache_data(ttl=600, show_spinner=False)
def cached_photos_map():
    """
    Photos are big, so they're fetched once and remembered for 10 minutes.
    After an owner uploads or removes a photo we call cached_photos_map.clear().
    """
    return get_all_photos_map()


# ============================================================
# SMALL HELPERS
# ============================================================
def escape_md(text):
    """
    Stops text typed by users (names, messages, descriptions) from being turned
    into formatting like bold text, links or images.
    """
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<$])", r"\\\1", str(text))


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


def fmt_chat_time(timestamp):
    return timestamp.astimezone(ZoneInfo(APP_TIMEZONE)).strftime("%d %b, %I:%M %p")


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
    and when editing one. current = {"open", "close", "durations"} or None for defaults.
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


def get_free_starts(venue, booked_hours, booking_date, duration, today, now_hour):
    """
    Works out which start times are still free:
    inside opening hours, not overlapping a booked or blocked hour,
    and not already past today.
    """
    starts = []
    for start in range(venue["open"], venue["close"] - duration + 1):
        if booking_date == today and start <= now_hour:
            continue
        if any(hour in booked_hours for hour in range(start, start + duration)):
            continue
        starts.append(start)
    return starts


# ---------- Promotions ----------
def promo_is_live(promo, today):
    """A promotion counts as live if it's switched on and hasn't expired."""
    return promo["active"] and (promo["valid_until"] is None or promo["valid_until"] >= today)


def promo_applies(promo, booking_date, start_hour, duration):
    """Does this promotion cover a booking on this date, starting at this hour, this long?"""
    weekday = booking_date.weekday()  # Monday = 0 ... Sunday = 6
    if promo["day_type"] == "weekdays" and weekday >= 5:
        return False
    if promo["day_type"] == "weekends" and weekday < 5:
        return False
    if promo["valid_until"] is not None and booking_date > promo["valid_until"]:
        return False
    if promo["start_hour"] is not None and promo["end_hour"] is not None:
        # The whole booking must fall inside the discounted hours
        if start_hour < promo["start_hour"] or start_hour + duration > promo["end_hour"]:
            return False
    return True


def best_promo(promos, today, booking_date, start_hour, duration):
    """The biggest live discount that applies to this booking, or None."""
    matches = [
        p for p in promos
        if promo_is_live(p, today) and promo_applies(p, booking_date, start_hour, duration)
    ]
    return max(matches, key=lambda p: p["discount_percent"]) if matches else None


def discounted_total(price_per_hour, duration, promo):
    """Total price for the booking, with the promotion's discount taken off."""
    total = price_per_hour * duration
    if promo:
        total = round(total * (100 - promo["discount_percent"]) / 100)
    return total


def promo_text(promo):
    """One-line description of an offer, e.g. 'Early bird — 20% off · Mon–Fri · 6:00 AM – 10:00 AM'."""
    parts = [f"🏷️ **{escape_md(promo['title'])}** — {promo['discount_percent']}% off"]
    parts.append(DAY_TYPES[promo["day_type"]])
    if promo["start_hour"] is not None and promo["end_hour"] is not None:
        parts.append(f"{fmt_hour(promo['start_hour'])} – {fmt_hour(promo['end_hour'])}")
    else:
        parts.append("all day")
    if promo["valid_until"] is not None:
        parts.append(f"until {promo['valid_until'].strftime('%d %b')}")
    return " · ".join(parts)


# ---------- Photos ----------
def prepare_photo(uploaded_file):
    """
    Shrinks an uploaded picture to at most 1000 pixels and saves it as a compressed
    JPEG (this also strips hidden data like GPS location from the file).
    Returns the bytes, or None if the file is too big or isn't a readable image.
    """
    if uploaded_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        return None
    try:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)  # fixes sideways phone photos
        image = image.convert("RGB")
        image.thumbnail((1000, 1000))
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


# ---------- Venue card (what players see) ----------
def venue_card(venue, promos, photos, today, distance=None, show_owner=False):
    """One venue as shown to players: photo, price up front, offers."""
    with st.container(border=True):
        if photos:
            st.image(photos[0][1])
        st.markdown(f"#### 📍 {escape_md(venue['name'])}")
        st.markdown(
            escape_md(venue["location"])
            + (f" · {distance:.1f} km away" if distance is not None else "")
        )
        details = f"💰 **{price_label(venue['price'])}** · 👥 capacity {venue['capacity']}"
        if venue["online_booking"]:
            details += " · ⚡ online booking"
        st.markdown(details)
        if venue["description"]:
            st.markdown(escape_md(venue["description"]).replace("\n", "  \n"))
        for promo in promos:
            if promo_is_live(promo, today):
                st.markdown(promo_text(promo))
        if len(photos) > 1:
            with st.expander(f"📷 More photos ({len(photos) - 1})"):
                for _photo_id, image_bytes in photos[1:]:
                    st.image(image_bytes)
        if show_owner and venue["owner_name"]:
            st.caption(f"Owner: {venue['owner_name']}")


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

        if st.button("Log in", type="primary"):
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

        if st.button("Create account", type="primary"):
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

    if st.button("Save profile", type="primary"):
        save_profile(user["id"], user["name"], sport, skill)
        st.success("Profile saved!")

    if not saved:
        st.info("Save your profile once, then you can join games.")


# ============================================================
# PLAYER: VENUES (search nearby + list)
# ============================================================
def show_venues_for_players():
    venues = get_all_venues()
    promos_map = get_promotions_map()
    photos_map = cached_photos_map()
    today = datetime.now(ZoneInfo(APP_TIMEZONE)).date()

    st.subheader("Find venues near you")

    my_location = st.text_input("Your location (e.g. street/area, city)")
    radius_km = st.slider("Search radius (km)", min_value=1, max_value=50, value=5)

    if st.button("Search nearby venues", type="primary"):
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
                for venue in venues:
                    if venue["lat"] is None or venue["lon"] is None:
                        continue  # venue couldn't be located on the map
                    distance = haversine_distance(my_lat, my_lon, venue["lat"], venue["lon"])
                    if distance <= radius_km:
                        nearby.append((distance, venue))

                nearby.sort(key=lambda pair: pair[0])  # closest first

                if not nearby:
                    st.info(f"No venues found within {radius_km}km.")
                for distance, venue in nearby:
                    venue_card(
                        venue, promos_map.get(venue["id"], []),
                        photos_map.get(venue["id"], []), today, distance=distance,
                    )

    st.divider()
    st.subheader("All venues")
    if not venues:
        st.caption("No venues added yet.")
    for venue in venues:
        venue_card(
            venue, promos_map.get(venue["id"], []),
            photos_map.get(venue["id"], []), today, show_owner=True,
        )


# ============================================================
# PLAYER: EVENTS (host + join + chat)
# ============================================================
def show_events(user):
    show_flash()

    venues = get_all_venues()
    venue_by_id = {v["id"]: v for v in venues}
    promos_map = get_promotions_map()

    now = datetime.now(ZoneInfo(APP_TIMEZONE))
    today = now.date()

    def venue_option_label(venue_id):
        """Dropdown text: venue name plus its price, so the cost is clear before choosing."""
        if venue_id is None:
            return "No venue (public place / own arrangement)"
        venue = venue_by_id[venue_id]
        if venue["price"] is not None:
            return f"{venue['name']} — {price_label(venue['price'])}"
        return venue["name"]

    # ---------- Host a game ----------
    st.subheader("Host a new game")
    event_sport = st.selectbox("Sport", SPORTS, key="event_sport")
    event_skill = st.selectbox("Skill level needed", ["Any"] + SKILLS, key="event_skill")
    max_players = st.number_input("Max players", min_value=2, max_value=30, value=10)

    # None = no venue (public park, or any place the host arranges themselves)
    selected_venue_id = st.selectbox(
        "Venue",
        options=[None] + [v["id"] for v in venues],
        format_func=venue_option_label,
        key="event_venue",
    )
    selected_venue = venue_by_id.get(selected_venue_id)
    online = selected_venue is not None and selected_venue["online_booking"]
    venue_promos = promos_map.get(selected_venue_id, []) if selected_venue else []

    # Filled in below depending on whether the venue takes online bookings
    date_time = ""
    booking_date = None
    duration = None
    start_hour = None
    chosen_promo = None
    total_price = None

    if online:
        # --- Venue with online booking: pick a real, free slot ---
        base_price = selected_venue["price"] or 0
        st.markdown(
            f"**Book a slot** — open {fmt_hour(selected_venue['open'])} to "
            f"{fmt_hour(selected_venue['close'])}, {price_label(selected_venue['price'])}"
        )
        for promo in venue_promos:
            if promo_is_live(promo, today):
                st.markdown(promo_text(promo))

        booking_date = st.date_input(
            "Date",
            value=today,
            min_value=today,
            max_value=today + timedelta(days=BOOKING_WINDOW_DAYS),
            key=f"event_date_{selected_venue_id}",
        )
        duration = st.selectbox(
            "How long?",
            selected_venue["durations"],
            format_func=duration_label,
            key=f"event_duration_{selected_venue_id}",
        )
        booked_hours = get_booked_hours(selected_venue_id, booking_date)
        free_starts = get_free_starts(
            selected_venue, booked_hours, booking_date, duration, today, now.hour
        )

        def slot_option(start):
            """Slot text, with the discount shown when one applies."""
            promo = best_promo(venue_promos, today, booking_date, start, duration)
            text = slot_label(start, duration)
            return text + (f"  ·  🏷️ {promo['discount_percent']}% off" if promo else "")

        if free_starts:
            # The nonce changes after each booking so the slot list refreshes cleanly
            nonce = st.session_state.get("booking_nonce", 0)
            start_hour = st.selectbox(
                "Time slot",
                free_starts,
                format_func=slot_option,
                key=f"event_slot_{selected_venue_id}_{booking_date}_{duration}_{nonce}",
            )
            chosen_promo = best_promo(venue_promos, today, booking_date, start_hour, duration)
            total_price = discounted_total(base_price, duration, chosen_promo)
            if chosen_promo:
                st.info(
                    f"💰 ₹{total_price} total instead of ₹{base_price * duration} — "
                    f"{chosen_promo['discount_percent']}% off with “{chosen_promo['title']}”. "
                    "Payment is arranged directly with the venue for now."
                )
            else:
                st.info(
                    f"💰 ₹{total_price} total ({price_label(selected_venue['price'])}). "
                    "Payment is arranged directly with the venue for now."
                )
        else:
            st.warning("No free slots for that date and length. Try another date or duration.")
    else:
        # --- Public place, or a venue without online booking: type the time ---
        date_time = st.text_input("Date & time (e.g. Sat 6pm)")
        if selected_venue:
            st.caption(
                "This venue hasn't set up online booking yet, so enter a time and "
                f"confirm it with the venue directly. Listed price: {price_label(selected_venue['price'])}."
            )

    if st.button("Create event", type="primary"):
        if online:
            if start_hour is None:
                st.error("Please pick a time slot first.")
            else:
                time_text = f"{booking_date.strftime('%a %d %b')}, {slot_label(start_hour, duration)}"
                booked = create_booked_event(
                    user["id"], user["name"], event_sport, event_skill, max_players,
                    selected_venue_id, booking_date, start_hour, duration,
                    total_price, time_text,
                )
                if booked:
                    st.session_state["booking_nonce"] = st.session_state.get("booking_nonce", 0) + 1
                    st.session_state["flash"] = f"Event created and slot booked: {time_text}"
                    st.rerun()
                else:
                    st.error("Sorry, that slot just became unavailable. Please pick another one.")
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

    # Active games first, newest first; cancelled ones sink to the bottom
    events = sorted(get_events(), key=lambda e: (e[7] == "cancelled", -e[0]))
    participants_map = get_participants_map()
    profile = get_profile(user["id"])  # (sport, skill) or None

    def can_chat(event):
        event_id, status, host_id = event[0], event[7], event[8]
        if status != "active":
            return False
        return host_id == user["id"] or any(a == user["id"] for a, _ in participants_map.get(event_id, []))

    # One query fetches the chat for every game this person can chat in
    messages_map = get_messages_map([e[0] for e in events if can_chat(e)])

    if not events:
        st.caption("No events yet. Host one above!")

    for event in events:
        event_id, e_host, e_sport, e_skill, e_max, e_datetime, e_venue_id, e_status, e_host_id = event
        joined = participants_map.get(event_id, [])  # [(account_id, name), ...]
        joined_names = [n for _, n in joined]
        already_joined = any(a == user["id"] for a, _ in joined)
        venue = venue_by_id.get(e_venue_id)
        icon = SPORT_ICONS.get(e_sport, "🏅")

        with st.container(border=True):
            # --- Event header ---
            title = f"{icon} {e_sport} ({e_skill}) hosted by {escape_md(e_host)}"
            if e_status == "cancelled":
                st.markdown(f"### ~~{title}~~ ❌ CANCELLED")
            else:
                st.markdown(f"### {title}")

            if venue:
                st.markdown(
                    f"📍 {escape_md(venue['name'])} — {escape_md(venue['location'])}"
                    f" · {price_label(venue['price'])}"
                )
            else:
                st.markdown("📍 No venue set")
            st.markdown(f"🕒 {escape_md(e_datetime)}  |  👥 {len(joined)}/{e_max} joined")
            if joined_names:
                st.caption("Joined: " + ", ".join(escape_md(n) for n in joined_names))

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
                    if st.button("Join event", key=f"join_{event_id}", type="primary"):
                        if join_event(event_id, user["id"], user["name"]):
                            st.rerun()
                        else:
                            st.error("Couldn't join. The game may be full or cancelled.")

                # --- Chat (host + joined players only) ---
                if can_chat(event):
                    # The label stays constant so the box doesn't snap shut when messages arrive
                    with st.expander("💬 Chat", expanded=st.session_state.get("chat_open") == event_id):
                        messages = messages_map.get(event_id, [])
                        if not messages:
                            st.caption("No messages yet. Say hi 👋")
                        for m_account, m_name, m_body, m_time in messages[-CHAT_MESSAGES_SHOWN:]:
                            who = "You" if m_account == user["id"] else escape_md(m_name)
                            st.markdown(
                                f"**{who}** · {fmt_chat_time(m_time)}  \n"
                                + escape_md(m_body).replace("\n", "  \n")
                            )

                        with st.form(f"chat_form_{event_id}", clear_on_submit=True):
                            text = st.text_input(
                                "Message", max_chars=500,
                                placeholder="Type a message...",
                                label_visibility="collapsed",
                            )
                            col_send, col_refresh = st.columns(2)
                            sent = col_send.form_submit_button("Send", type="primary")
                            refreshed = col_refresh.form_submit_button("🔄 Refresh")

                        if sent and text.strip():
                            post_message(event_id, user["id"], user["name"], text.strip())
                            st.session_state["chat_open"] = event_id
                            st.rerun()
                        elif refreshed or sent:
                            st.session_state["chat_open"] = event_id
                            st.rerun()


# ============================================================
# OWNER: ADD VENUE
# ============================================================
def show_add_venue(user):
    st.subheader("Add a venue")

    venue_name = st.text_input("Venue name (e.g. Green Park Turf)")
    venue_location = st.text_input("Location / address (be specific - include city)")
    venue_capacity = st.number_input("Max capacity", min_value=2, max_value=100, value=20)
    description = st.text_area(
        "Short description (shown to players)",
        max_chars=200,
        placeholder="e.g. Floodlit 7-a-side turf, free parking, changing rooms",
    )

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

    if st.button("Save venue", type="primary"):
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
                price, open_hour, close_hour, durations, description.strip(),
            )
            if coords:
                st.success(
                    f"Venue '{venue_name}' added and located on the map! "
                    "Open My Venues to add photos and offers."
                )
            else:
                st.warning(
                    "Venue saved, but we couldn't pinpoint that address on the map "
                    "(try adding the area and city). It won't show up in 'near me' searches yet."
                )


# ============================================================
# OWNER: MY VENUES
# ============================================================
def show_my_venues(user):
    show_flash()
    st.subheader("My venues & bookings")

    today = datetime.now(ZoneInfo(APP_TIMEZONE)).date()
    my_venues = [v for v in get_all_venues() if v["owner_id"] == user["id"]]

    if not my_venues:
        st.info("You haven't added a venue yet. Use the 'Add Venue' tab.")
        return

    all_events = get_events()
    participants_map = get_participants_map()
    stats_map = get_venue_stats_map(user["id"])
    promos_map = get_promotions_map()
    blocks_map = get_blocks_map(user["id"], today)
    photos_map = cached_photos_map()

    for venue in my_venues:
        v_id = venue["id"]
        promos = promos_map.get(v_id, [])
        photos = photos_map.get(v_id, [])
        blocks = blocks_map.get(v_id, [])

        with st.container(border=True):
            # --- Header: what players see first ---
            if photos:
                st.image(photos[0][1])
            st.markdown(f"### 📍 {escape_md(venue['name'])}")
            st.markdown(f"{escape_md(venue['location'])} — capacity {venue['capacity']}")
            st.markdown(f"#### 💰 {price_label(venue['price'])}")
            if venue["price"] is None:
                st.warning("No price listed yet, so players can't see what this venue costs. Add it under Price & hours.")
            if venue["description"]:
                st.markdown(escape_md(venue["description"]).replace("\n", "  \n"))

            count, hours, value = stats_map.get(v_id, (0, 0, 0))
            col1, col2, col3 = st.columns(3)
            col1.metric("Bookings", count)
            col2.metric("Hours booked", hours)
            col3.metric("Booked value", f"₹{value}")
            st.caption("Booked value is the price of confirmed slots after discounts. Payments aren't collected through the app yet.")

            if venue["online_booking"]:
                lengths = ", ".join(duration_label(d) for d in venue["durations"])
                st.markdown(
                    f"🕒 Online booking: {fmt_hour(venue['open'])} – {fmt_hour(venue['close'])} · {lengths}"
                )
            else:
                st.caption("Online booking is off. Players see your price and arrange a time with the host.")

            tab_book, tab_price, tab_promo, tab_block, tab_photo = st.tabs(
                ["Bookings", "Price & hours", "Offers", "Block hours", "Photos"]
            )

            # ---------- Bookings ----------
            with tab_book:
                bookings = sorted(
                    [e for e in all_events if e[6] == v_id], key=lambda e: -e[0]
                )
                if not bookings:
                    st.caption("No bookings at this venue yet.")
                for b in bookings:
                    b_id, b_host, b_sport, b_skill, b_max, b_datetime, _venue, b_status, _host_id = b
                    joined_count = len(participants_map.get(b_id, []))
                    icon = SPORT_ICONS.get(b_sport, "🏅")
                    label = "❌ CANCELLED — " if b_status == "cancelled" else ""
                    st.markdown(
                        f"{label}{icon} {b_sport} ({b_skill}) — hosted by {escape_md(b_host)} — "
                        f"{escape_md(b_datetime)} — {joined_count}/{b_max} joined"
                    )
                    if b_status != "cancelled":
                        if st.button("Cancel this booking", key=f"owner_cancel_{b_id}"):
                            cancel_event_as_owner(b_id, user["id"])
                            st.rerun()

            # ---------- Price & hours ----------
            with tab_price:
                new_price = price_input(f"price_{v_id}", venue["price"])
                new_description = st.text_area(
                    "Short description (shown to players)",
                    value=venue["description"],
                    max_chars=200,
                    key=f"desc_{v_id}",
                )
                booking_on = st.checkbox(
                    "Let players book time slots online",
                    value=venue["online_booking"],
                    key=f"booking_on_{v_id}",
                )
                new_open, new_close, new_durations = None, None, []
                if booking_on:
                    current = (
                        {"open": venue["open"], "close": venue["close"], "durations": venue["durations"]}
                        if venue["online_booking"] else None
                    )
                    new_open, new_close, new_durations = schedule_form(f"sched_{v_id}", current)

                if st.button("Save changes", key=f"save_listing_{v_id}", type="primary"):
                    error = validate_schedule(new_open, new_close, new_durations) if booking_on else None
                    if error:
                        st.error(error)
                    else:
                        update_venue_listing(
                            v_id, user["id"], new_price, new_open, new_close,
                            new_durations, new_description.strip(),
                        )
                        st.session_state["flash"] = f"Saved changes for {venue['name']}."
                        st.rerun()

            # ---------- Offers (promotions) ----------
            with tab_promo:
                st.caption(
                    "Offers are shown to players on your listing. Discounts are applied "
                    "automatically to slots booked in the app."
                )
                if not promos:
                    st.caption("No offers yet.")
                for promo in promos:
                    live = promo_is_live(promo, today)
                    status_note = "" if live else "  _(paused or expired)_"
                    st.markdown(promo_text(promo) + status_note)
                    col_a, col_b = st.columns(2)
                    with col_a:
                        label = "Pause" if promo["active"] else "Resume"
                        if st.button(label, key=f"promo_toggle_{promo['id']}"):
                            set_promotion_active(promo["id"], user["id"], not promo["active"])
                            st.rerun()
                    with col_b:
                        if st.button("Remove", key=f"promo_delete_{promo['id']}"):
                            delete_promotion(promo["id"], user["id"])
                            st.rerun()

                st.markdown("**Add an offer**")
                promo_title = st.text_input(
                    "Offer name", max_chars=60, placeholder="e.g. Early bird", key=f"promo_title_{v_id}"
                )
                promo_percent = st.slider(
                    "Discount (%)", min_value=5, max_value=90, value=20, step=5, key=f"promo_pct_{v_id}"
                )
                promo_days = st.selectbox(
                    "Applies on", list(DAY_TYPES.keys()), format_func=lambda k: DAY_TYPES[k],
                    key=f"promo_days_{v_id}",
                )
                limit_hours = st.checkbox("Only for certain hours", key=f"promo_limit_{v_id}")
                promo_start, promo_end = None, None
                if limit_hours:
                    col_s, col_e = st.columns(2)
                    with col_s:
                        promo_start = st.selectbox(
                            "From", list(range(0, 24)), index=6,
                            format_func=fmt_hour, key=f"promo_from_{v_id}",
                        )
                    with col_e:
                        promo_end = st.selectbox(
                            "To", list(range(1, 25)), index=9,
                            format_func=fmt_hour, key=f"promo_to_{v_id}",
                        )
                has_end_date = st.checkbox("Ends on a date", key=f"promo_has_end_{v_id}")
                promo_until = None
                if has_end_date:
                    promo_until = st.date_input(
                        "Valid until", value=today + timedelta(days=30),
                        min_value=today, key=f"promo_until_{v_id}",
                    )

                if st.button("Add offer", key=f"promo_add_{v_id}", type="primary"):
                    if not promo_title.strip():
                        st.error("Please give the offer a name.")
                    elif limit_hours and promo_end <= promo_start:
                        st.error("The 'To' time must be after the 'From' time.")
                    else:
                        add_promotion(
                            v_id, user["id"], promo_title.strip(), promo_percent,
                            promo_days, promo_start, promo_end, promo_until,
                        )
                        st.session_state["flash"] = "Offer added. Players can see it now."
                        st.rerun()

            # ---------- Block hours ----------
            with tab_block:
                if not venue["online_booking"]:
                    st.caption("Turn on online booking (Price & hours tab) to block hours.")
                else:
                    st.caption(
                        "Take hours off the market for maintenance or private use. "
                        "Players won't be able to book them."
                    )
                    block_date = st.date_input(
                        "Date", value=today, min_value=today,
                        max_value=today + timedelta(days=BOOKING_WINDOW_DAYS),
                        key=f"block_date_{v_id}",
                    )
                    block_start = st.selectbox(
                        "From", list(range(venue["open"], venue["close"])),
                        format_func=fmt_hour, key=f"block_from_{v_id}",
                    )
                    block_end = st.selectbox(
                        "To", list(range(block_start + 1, venue["close"] + 1)),
                        format_func=fmt_hour, key=f"block_to_{v_id}",
                    )
                    block_note = st.text_input(
                        "Reason (optional)", max_chars=80,
                        placeholder="e.g. Pitch maintenance", key=f"block_note_{v_id}",
                    )
                    if st.button("Block these hours", key=f"block_add_{v_id}", type="primary"):
                        result = create_block(
                            v_id, user["id"], block_date, block_start,
                            block_end - block_start, block_note.strip(),
                        )
                        if result == "ok":
                            st.session_state["flash"] = "Hours blocked."
                            st.rerun()
                        elif result == "taken":
                            st.error(
                                "Some of those hours are already booked or blocked. "
                                "Cancel the booking first, or pick different hours."
                            )
                        else:
                            st.error("Couldn't block those hours.")

                st.markdown("**Upcoming blocked hours**")
                if not blocks:
                    st.caption("Nothing blocked.")
                for block_id, b_date, b_start, b_duration, b_note in blocks:
                    note_text = f" — {escape_md(b_note)}" if b_note else ""
                    st.markdown(
                        f"🚧 {b_date.strftime('%a %d %b')}, {slot_label(b_start, b_duration)}{note_text}"
                    )
                    if st.button("Unblock", key=f"unblock_{block_id}"):
                        remove_block(block_id, user["id"])
                        st.rerun()

            # ---------- Photos ----------
            with tab_photo:
                st.caption(
                    f"Up to {MAX_PHOTOS_PER_VENUE} photos. The first one is the cover "
                    "players see on your listing."
                )
                for photo_id, image_bytes in photos:
                    st.image(image_bytes, width=260)
                    if st.button("Remove photo", key=f"photo_delete_{photo_id}"):
                        delete_venue_photo(photo_id, user["id"])
                        cached_photos_map.clear()
                        st.rerun()

                if len(photos) < MAX_PHOTOS_PER_VENUE:
                    nonce = st.session_state.get("photo_nonce", 0)
                    files = st.file_uploader(
                        "Add photos", type=["jpg", "jpeg", "png"],
                        accept_multiple_files=True, key=f"photo_upload_{v_id}_{nonce}",
                    )
                    if files and st.button("Upload photos", key=f"photo_save_{v_id}", type="primary"):
                        added, skipped = 0, 0
                        for uploaded in files:
                            data = prepare_photo(uploaded)
                            if data is not None and add_venue_photo(v_id, user["id"], data):
                                added += 1
                            else:
                                skipped += 1
                        cached_photos_map.clear()
                        st.session_state["photo_nonce"] = nonce + 1  # empties the uploader
                        message = f"{added} photo(s) added."
                        if skipped:
                            message += (
                                f" {skipped} skipped (too large, not a readable image, "
                                "or the photo limit was reached)."
                            )
                        st.session_state["flash"] = message
                        st.rerun()
                else:
                    st.info("Photo limit reached. Remove one to add another.")


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
    st.markdown(f"Signed in as **{escape_md(user['name'])}** ({role_text})")
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
