"""
geocode.py
Turns a human-readable address into latitude/longitude coordinates (geocoding),
and calculates the distance between two coordinates.
Uses OpenStreetMap's free Nominatim service - no API key needed.
"""

import requests
from math import radians, sin, cos, sqrt, atan2

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_address(address):
    """
    Converts an address string into (latitude, longitude).
    Returns None if the address can't be found or the service fails.

    Nominatim's usage policy requires a descriptive User-Agent header
    and asks for no more than ~1 request/second - totally fine for
    a small prototype with a handful of testers.
    """
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
    }
    headers = {
        "User-Agent": "TeamUpApp/1.0 (personal learning project)"
    }

    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        return (lat, lon)
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculates the distance in kilometers between two lat/lon points.
    This is the standard formula for distance between two points
    on a sphere (the Earth) - the same math any mapping app uses
    under the hood for straight-line distance.
    """
    R = 6371  # Earth's radius in km

    lat1_r, lon1_r, lat2_r, lon2_r = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c