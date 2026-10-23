import requests
import json
import re
from datetime import datetime

# District Calendar (Meetings)
EVENTS_API_URL = "https://thrillshare-cmsv2.services.thrillshare.com/api/v2/s/249568/events"

def fetch_events():
    """Fetches events from the Apptegy Events API."""
    print("Fetching events from Apptegy API...")
    try:
        response = requests.get(EVENTS_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        events = data.get('events', [])
        print(f"Found {len(events)} events.")
        return events
    except Exception as e:
        print(f"Error fetching events: {e}")
        return []

def parse_event_date(event):
    """Extracts the start date from an event object."""
    start_at = event.get('start_at')
    if not start_at:
        return None
    # Usually YYYY-MM-DDTHH:MM:SS...
    return start_at.split('T')[0]

def get_event_mapping(events):
    """Creates a mapping of date_slug to the list of that day's event details (a day can hold
    several, e.g. a committee meeting and a board workshop)."""
    mapping = {}
    for event in events:
        date_slug = parse_event_date(event)
        if not date_slug:
            continue
        
        # Capture basic info for now
        mapping.setdefault(date_slug, []).append({
            'title': event.get('title'),
            'location': (event.get('venue') or '').strip() or None,
            'id': event.get('id')
        })
    return mapping

if __name__ == "__main__":
    events = fetch_events()
    mapping = get_event_mapping(events)
    print(json.dumps(mapping, indent=2))
