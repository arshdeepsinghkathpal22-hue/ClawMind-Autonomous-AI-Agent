"""Parse simple schedule phrases like "tomorrow at 5 PM" or "every monday at 9am"."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]



@lru_cache(maxsize=1)
def local_tz():
    """The computer's timezone (an IANA zone when tzlocal can figure it out)."""
    try:
        from tzlocal import get_localzone

        return get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo


def local_now():
    return datetime.now(local_tz())


UNIT_MINUTES = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440, "week": 10080}


@dataclass
class Schedule:
    type: str  # once | daily | weekly | interval
    run_at: datetime | None = None  # timezone-aware, only for "once"
    hour: int | None = None
    minute: int | None = None
    day_of_week: int | None = None
    interval_minutes: int | None = None

    def describe(self):
        if self.type == "once":
            return "once on " + self.run_at.strftime("%a %d %b %Y at %H:%M")
        if self.type == "daily":
            return f"every day at {self.hour:02d}:{self.minute:02d}"
        if self.type == "weekly":
            return f"every {DAY_NAMES[self.day_of_week]} at {self.hour:02d}:{self.minute:02d}"
        n = self.interval_minutes
        for size, unit in ((1440, "day"), (60, "hour"), (1, "minute")):
            if n % size == 0:
                count = n // size
                return f"every {unit}" if count == 1 else f"every {count} {unit}s"


def _unit(word):
    word = word.rstrip("s")
    for key, minutes in UNIT_MINUTES.items():
        if word == key:
            return minutes
    if word in ("hrs", "mins"):
        return UNIT_MINUTES[word[:-1]]
    return None


def parse_clock(text):
    """'5pm', '5:30 pm', '17:00', 'noon' -> (hour, minute)."""
    s = text.strip().lower().replace(".", "")
    s = re.sub(r"\s*o'?clock$", "", s)
    if s in ("noon", "midday"):
        return 12, 0
    if s == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        raise ValueError(f"I couldn't understand the time '{text}'.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = m.group(3)
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError(f"'{text}' is not a valid time.")
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"'{text}' is not a valid time.")
    return hour, minute


def _take_day(s):
    """Pull a day word off the start or end of the phrase. Returns (day, rest)."""
    day_words = ["day after tomorrow", "tomorrow", "today", "tonight"]
    weekday = r"(?:on |next |this )?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b"

    for word in day_words:
        if s.startswith(word):
            return word, s[len(word):].strip()
        if s.endswith(" " + word):
            return word, s[: -len(word)].strip()

    m = re.match(weekday, s)
    if m:
        return m.group(1), s[m.end():].strip()
    m = re.search(r"\s" + weekday + r"$", s)
    if m:
        return m.group(1), s[: m.start()].strip()
    return None, s


def parse_when(text, now=None):
    """Turn a phrase into a Schedule. Raises ValueError with a friendly message."""
    now = now or local_now()
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Please say when the task should run.")

    # ISO date/time, e.g. 2026-10-06T17:00 or 2026-10-06 17:00
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        return _once(dt, now)
    except ValueError:
        pass

    s = re.sub(r"\s+", " ", raw.lower()).strip(" .!?,")
    s = re.sub(r"^at ", "", s)

    if s in ("hourly", "every hour"):
        return Schedule(type="interval", interval_minutes=60)

    m = re.fullmatch(r"every (\d+|an?|one|other) ?([a-z]+)", s)
    if m and _unit(m.group(2)):
        count = {"a": 1, "an": 1, "one": 1, "other": 2}.get(m.group(1)) or int(m.group(1))
        minutes = count * _unit(m.group(2))
        if minutes < 1 or minutes > 525_600:
            raise ValueError("Intervals must be between 1 minute and 1 year.")
        return Schedule(type="interval", interval_minutes=minutes)

    m = re.fullmatch(r"in (\d+|an?|one|half an) ?([a-z]+)", s)
    if m and _unit(m.group(2)):
        word = m.group(1)
        if word == "half an":
            minutes = 30
        else:
            count = 1 if word in ("a", "an", "one") else int(word)
            minutes = count * _unit(m.group(2))
        return _once(now + timedelta(minutes=minutes), now)

    m = re.fullmatch(r"(?:every ?day|daily|each day)(?: at)? (.+)", s)
    if m:
        hour, minute = parse_clock(m.group(1))
        return Schedule(type="daily", hour=hour, minute=minute)

    m = re.fullmatch(r"(?:every|each) ([a-z]+?)s?(?:(?: at)? (.+))?", s)
    if m and m.group(1) in WEEKDAYS:
        hour, minute = parse_clock(m.group(2)) if m.group(2) else (9, 0)
        return Schedule(type="weekly", day_of_week=WEEKDAYS[m.group(1)], hour=hour, minute=minute)

    day, rest = _take_day(s)
    rest = re.sub(r"^(at|by) ", "", rest).strip()

    if rest:
        hour, minute = parse_clock(rest)
    elif day == "tonight":
        hour, minute = 20, 0
    elif day:
        hour, minute = 9, 0
    else:
        raise ValueError(
            f"I couldn't understand '{raw}'. Try 'tomorrow at 5 PM', 'in 30 minutes', "
            "'every day at 9am', 'every monday at 10:00' or 'every 2 hours'."
        )

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day == "tomorrow":
        target += timedelta(days=1)
    elif day == "day after tomorrow":
        target += timedelta(days=2)
    elif day in WEEKDAYS:
        days_ahead = (WEEKDAYS[day] - now.weekday()) % 7
        target += timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(days=7)
    elif day in ("today", "tonight"):
        if target <= now:
            raise ValueError("That time has already passed today.")
    elif target <= now:
        target += timedelta(days=1)

    return _once(target, now)


def _once(dt, now):
    if dt <= now:
        raise ValueError("That time is in the past.")
    if dt - now > timedelta(days=730):
        raise ValueError("Tasks can be scheduled at most two years ahead.")
    return Schedule(type="once", run_at=dt)


def split_reminder(text, now=None):
    """'tomorrow at 5 PM to study DSA' -> ('study DSA', 'tomorrow at 5 PM')."""
    text = text.strip().rstrip(".!")
    tries = []

    m = re.match(r"^(?P<when>.+?)\s+to\s+(?P<what>.+)$", text, re.I)
    if m:
        tries.append((m.group("what"), m.group("when")))

    starters = r"(?:today|tonight|tomorrow|day after tomorrow|in|at|on|every|each|daily|next|this)"
    m = re.match(r"^(?:to\s+)?(?P<what>.+?)\s+(?P<when>" + starters + r"\b.+)$", text, re.I)
    if m:
        tries.append((m.group("what"), m.group("when")))

    last_error = None
    for what, when in tries:
        try:
            parse_when(when, now)
            return what.strip(), when.strip()
        except ValueError as exc:
            last_error = exc
    raise last_error or ValueError("Tell me what to remind you about and when, e.g. 'tomorrow at 5 PM to study DSA'.")
