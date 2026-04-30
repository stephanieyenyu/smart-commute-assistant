from app.integrations.Maps_client import geocode_address, estimate_transit_minutes, estimate_walking_minutes

# This file proxies to the new implementation in app.integrations.Maps_client
# to prevent breaking existing code that imports from app.google_maps.
