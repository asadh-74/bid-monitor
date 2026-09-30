"""Web services serve saved records; scraping requires an explicit opt-in."""
import os


def collectors_enabled():
    return os.getenv("ENABLE_LOCAL_SCRAPING", "false").strip().lower() in ("1", "true", "yes")
