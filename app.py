"""
app.py
The Streamlit web interface for the TeamUp app.
Tabs: Create Profile, Venues, Events, Owner Dashboard.
"""

import streamlit as st
from database import (
    init_db,
    add_user,
    get_users,
    add_venue,
    get_venues,
    get_venues_by_owner,
    add_event,
    get_events,
    get_events_by_venue,
    cancel_event,
    join_event,
    get_participants,
    has_joined,
)
from geocode import geocode_address, haversine_distance

init_db()

st.title("🏆 TeamUp")

tab1, tab2, tab3, tab4 = st.tabs(["Create Profile", "Venues", "Events", "Owner Dashboard"])

# ============================================================
# TAB 1: CREATE PROFILE
# ============================================================
with tab1:
    st.subheader("Create your player profile")

    name = st.text_input("Your name")
    sport = st.selectbox(
        "Primary sport",
        ["Football", "Cricket", "Badminton", "Tennis", "Basketball"]
    )
    skill = st.selectbox("Skill level", ["Basic", "Medium", "Advanced"])

    if st.button("Save Profile"):
        if name:
            add_user(name, sport, skill)
            st.success(f"Profile created for {name}!")
        else:
            st.error("Please enter your name")

    st.subheader("All players")
    users = get_users()
    if not users:
        st.caption("No profiles yet.")
    for u in users:
        st.write(f"👤 {u[0]} — {u[1]} ({u[2]})")


# ============================================================
# TAB 2: VENUES (add + location search)
# ============================================================
with tab2:
    st.subheader("Add a venue")

    venue_owner = st.text_input("Owner name (that's you, the turf/venue owner)")
    venue_name = st.text_input("Venue name (e.g. Green Park Turf)")
    venue_location = st.text_input("Location / address (be specific - e.g. include city)")
    venue_capacity = st.number_input("Max capacity", min_value=2, max_value=100, value=20)

    if st.button("Save Venue"):
        if venue_owner and venue_name and venue_location:
            with st.spinner("Looking up location..."):
                coords = geocode_address(venue_location)
            lat, lon = coords if coords else (None, None)
            add_venue(venue_name, venue_location, venue_capacity, lat, lon, venue_owner)
            if coords:
                st.success(f"Venue '{venue_name}' added and located on the map!")
            else:
                st.warning(
                    "Venue saved, but we couldn't pinpoint that address on the map "
                    "(try adding city/area name). It won't appear in 'near me' searches yet."
                )
        else:
            st.error("Please fill in owner name, venue name, and location")

    st.divider()
    st.subheader("Find venues near you")

    my_location = st.text_input("Your location (e.g. street/area, city)")
    radius_km = st.slider("Search radius (km)", min_value=1, max_value=50, value=5)

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
                venues = get_venues()
                nearby = []

                for v in venues:
                    v_id, v_name, v_loc, v_cap, v_lat, v_lon, v_owner = v
                    if v_lat is None or v_lon is None:
                        continue
                    distance = haversine_distance(my_lat, my_lon, v_lat, v_lon)
                    if distance <= radius_km:
                        nearby.append((distance, v_name, v_loc, v_cap))

                nearby.sort(key=lambda x: x[0])

                if not nearby:
                    st.info(f"No venues found within {radius_km}km.")
                else:
                    for distance, v_name, v_loc, v_cap in nearby:
                        st.write(f"📍 **{v_name}** — {v_loc} ({distance:.1f} km away, capacity {v_cap})")

    st.divider()
    st.subheader("All venues")
    venues = get_venues()
    if not venues:
        st.caption("No venues added yet.")
    for v in venues:
        st.write(f"📍 **{v[1]}** — {v[2]} (capacity: {v[3]}) — owner: {v[6] or 'unknown'}")


# ============================================================
# TAB 3: EVENTS (HOST + JOIN)
# ============================================================
with tab3:
    st.subheader("Host a new game")

    venues = get_venues()
    venue_options = {v[0]: v[1] for v in venues}

    host_name = st.text_input("Your name (host)")
    event_sport = st.selectbox(
        "Sport",
        ["Football", "Cricket", "Badminton", "Tennis", "Basketball"],
        key="event_sport"
    )
    event_skill = st.selectbox(
        "Skill level needed",
        ["Any", "Basic", "Medium", "Advanced"],
        key="event_skill"
    )
    max_players = st.number_input("Max players", min_value=2, max_value=30, value=10)
    date_time = st.text_input("Date & time (e.g. Sat 6pm)")

    if venue_options:
        selected_venue_id = st.selectbox(
            "Venue",
            options=list(venue_options.keys()),
            format_func=lambda vid: venue_options[vid],
            key="event_venue"
        )
    else:
        selected_venue_id = None
        st.info("No venues added yet — add one in the Venues tab first, or leave this event venue-less for now.")

    if st.button("Create Event"):
        if host_name and date_time:
            add_event(host_name, event_sport, event_skill, max_players, date_time, selected_venue_id)
            st.success("Event created!")
        else:
            st.error("Please fill in host name and date/time")

    st.divider()
    st.subheader("Upcoming games")

    events = get_events()
    all_users = get_users()

    user_names = [u[0] for u in all_users]
    user_skill_map = {u[0]: u[2] for u in all_users}
    venue_lookup = {v[0]: (v[1], v[2]) for v in venues}

    if not events:
        st.caption("No events yet. Host one above!")

    for event in events:
        event_id, e_host, e_sport, e_skill, e_max, e_datetime, e_venue_id, e_status = event
        joined = get_participants(event_id)

        venue_name, venue_location = venue_lookup.get(e_venue_id, ("No venue set", ""))

        if e_status == "cancelled":
            st.markdown(f"### ⚽ ~~{e_sport} ({e_skill}) hosted by {e_host}~~ ❌ CANCELLED")
        else:
            st.markdown(f"### ⚽ {e_sport} ({e_skill}) hosted by {e_host}")

        st.write(f"📍 {venue_name}" + (f" — {venue_location}" if venue_location else ""))
        st.write(f"🕒 {e_datetime}  |  👥 {len(joined)}/{e_max} joined")
        if joined:
            st.caption("Joined: " + ", ".join(joined))

        if e_status == "cancelled":
            st.caption("This event has been cancelled.")
        else:
            # --- Host cancel option ---
            with st.expander("Are you the host? Cancel this event"):
                confirm_host_name = st.text_input(
                    "Enter your name to confirm you're the host",
                    key=f"host_confirm_{event_id}"
                )
                if st.button("Cancel this event", key=f"host_cancel_{event_id}"):
                    if confirm_host_name.strip().lower() == e_host.strip().lower():
                        cancel_event(event_id)
                        st.success("Event cancelled.")
                        st.rerun()
                    else:
                        st.error("Name doesn't match the host on record — can't cancel.")

            # --- Join section ---
            if not user_names:
                st.info("Create a profile first to join events.")
            else:
                picker_key = f"picker_{event_id}"
                join_key = f"join_{event_id}"

                selected_name = st.selectbox("Join as", user_names, key=picker_key)

                if st.button("Join Event", key=join_key):
                    if has_joined(event_id, selected_name):
                        st.warning(f"{selected_name} already joined this event.")
                    elif len(joined) >= e_max:
                        st.error("This event is full.")
                    else:
                        player_skill = user_skill_map[selected_name]
                        if e_skill != "Any" and player_skill != e_skill:
                            st.warning(
                                f"⚠️ Skill mismatch: this event needs {e_skill} level, "
                                f"your profile is {player_skill}. Joining anyway."
                            )
                        join_event(event_id, selected_name)
                        st.success(f"{selected_name} joined!")
                        st.rerun()

        st.divider()


# ============================================================
# TAB 4: OWNER DASHBOARD
# ============================================================
with tab4:
    st.subheader("Manage your venues")
    st.caption(
        "Basic version for now — type the same owner name you used when adding "
        "your venue(s) to see and manage bookings. A real login system comes later."
    )

    owner_lookup_name = st.text_input("Your owner name", key="owner_lookup")

    if st.button("View my venues"):
        st.session_state["owner_dashboard_name"] = owner_lookup_name

    active_owner = st.session_state.get("owner_dashboard_name")

    if active_owner:
        my_venues = get_venues_by_owner(active_owner)

        if not my_venues:
            st.info(f"No venues found for owner '{active_owner}'.")
        else:
            for v in my_venues:
                v_id, v_name, v_loc, v_cap, v_lat, v_lon, v_owner = v
                st.markdown(f"### 📍 {v_name}")
                st.write(f"{v_loc} — capacity {v_cap}")

                bookings = get_events_by_venue(v_id)
                if not bookings:
                    st.caption("No bookings at this venue yet.")
                else:
                    for booking in bookings:
                        b_id, b_host, b_sport, b_skill, b_max, b_datetime, _, b_status = booking
                        joined_count = len(get_participants(b_id))
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            label = "❌ CANCELLED — " if b_status == "cancelled" else ""
                            st.write(
                                f"{label}⚽ {b_sport} ({b_skill}) — hosted by {b_host} — "
                                f"{b_datetime} — {joined_count}/{b_max} joined"
                            )
                        with col2:
                            if b_status == "cancelled":
                                st.caption("Cancelled")
                            else:
                                if st.button("Cancel", key=f"cancel_{b_id}"):
                                    cancel_event(b_id)
                                    st.success("Event cancelled.")
                                    st.rerun()
                st.divider()