from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

import httpx

RESY_SEARCH_URL = "https://api.resy.com/3/venuesearch/search"

# Embedded web-client key (same as Resy front-end bundle); override with RESY_API_KEY.
_DEFAULT_RESY_WEB_API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"


@dataclass(frozen=True)
class ResyVenueHit:
    name: str
    venue_id: str
    url_slug: str
    city_url_slug: str
    location_shortcode: str


def _resy_api_key() -> str:
    return (os.environ.get("RESY_API_KEY") or _DEFAULT_RESY_WEB_API_KEY).strip()


def search_nyc_venues(query: str, *, per_page: int = 5) -> List[ResyVenueHit]:
    """
    Search Resy venues around NYC and return matching venues.

    Uses the same client API key as Resy's web frontend. This does not authenticate a user.
    """
    q = query.strip()
    if not q:
        return []

    # NYC center; good enough for "search NYC by default".
    geo = {"latitude": 40.7128, "longitude": -74.0060}

    headers = {
        "Authorization": f'ResyAPI api_key="{_resy_api_key()}"',
        "Origin": "https://resy.com",
        "Referer": "https://resy.com/",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; reservation-notifier/0.1)",
    }
    payload = {"query": q, "per_page": per_page, "types": ["venue"], "geo": geo}

    with httpx.Client(timeout=30.0) as client:
        r = client.post(RESY_SEARCH_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    hits = data.get("search", {}).get("hits") or []
    out: List[ResyVenueHit] = []
    for h in hits:
        try:
            loc_code = str(h["location"]["code"])
            city_slug = str(h["location"]["url_slug"])
            if loc_code.lower() != "ny" and city_slug.lower() != "new-york-ny":
                continue
            out.append(
                ResyVenueHit(
                    name=str(h["name"]),
                    venue_id=str(h["id"]["resy"]),
                    url_slug=str(h["url_slug"]),
                    city_url_slug=city_slug,
                    location_shortcode=loc_code,
                )
            )
        except Exception:
            continue
    return out

