"""The time for parts that keep times: now, and a time shown in the user's time zone.

Tests fix the clock with HUB_TEST_NOW, an ISO UTC time such as
2026-03-02T09:00:00Z. A time zone is an IANA name such as Europe/Paris. An
empty or unknown name means the system's time zone.
"""
import calendar
import datetime
import os
import time

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def epoch():
    """Seconds since 1970 in UTC."""
    fixed = os.environ.get("HUB_TEST_NOW")
    if fixed:
        return calendar.timegm(time.strptime(fixed, TIME_FORMAT))
    return time.time()


def stamp(seconds=None):
    """An ISO UTC time, now or at seconds."""
    return time.strftime(TIME_FORMAT, time.gmtime(epoch() if seconds is None else seconds))


def seconds(text):
    """An ISO UTC time from stamp() as seconds since 1970, or None."""
    try:
        return calendar.timegm(time.strptime(str(text), TIME_FORMAT))
    except ValueError:
        return None


def zone(name):
    """A tzinfo for the name, or None for the system's time zone."""
    if not name:
        return None
    try:
        import zoneinfo

        return zoneinfo.ZoneInfo(str(name))
    except (ImportError, ValueError, OSError, LookupError):
        return None


def local(seconds_at, zone_name=""):
    """A datetime at seconds_at in the named time zone, or in the system's."""
    found = zone(zone_name)
    moment = datetime.datetime.fromtimestamp(seconds_at, datetime.timezone.utc)
    return moment.astimezone(found) if found else moment.astimezone()


def hour_minute(seconds_at, zone_name=""):
    return local(seconds_at, zone_name).strftime("%H:%M")
