"""
Understanding times like "tomorrow at 6 am", "in 10 minutes" or "friday 5:30 pm".

The AI model passes the user's own words; this code turns them into an exact
date and time, so a small model never has to do date arithmetic.
"""

import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
NAMED_TIMES = {"noon": time(12), "midday": time(12), "midnight": time(0), "morning": time(9),
               "afternoon": time(15), "evening": time(18), "tonight": time(20), "night": time(21)}
DEFAULT_TIME = time(9)  # "on Friday" with no time means 9 am


class WhenError(ValueError):
    """The text couldn't be understood as a time."""


def _month_number(text: str) -> int:
    return next(i for i, name in enumerate(MONTHS, start=1) if name.startswith(text[:3]))


def _take(pattern: str, text: str):
    """Find `pattern`, return (match, text with the match removed)."""
    match = re.search(pattern, text)
    if not match:
        return None, text
    return match, (text[:match.start()] + " " + text[match.end():]).strip()


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """Turn 'tomorrow at 6 am' (and many similar phrases) into a datetime in the future."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    original = text
    text = " " + text.lower().strip().strip(".!?") + " "
    text = text.replace(",", " ").replace(" o'clock", "").replace(" oclock", "")
    text = re.sub(r"\s+", " ", text)
    if re.search(r"\b(yesterday|ago|last)\b", text):
        raise WhenError(f"'{original}' is in the past - I can only schedule things for the future.")
    evening = bool(re.search(r"\b(tonight|evening|night|afternoon)\b", text))

    # 1. Relative times: "in 10 minutes", "after 2 hours", "10 mins from now".
    relative = re.search(r"(?:in|after)?\s*(\d+(?:\.\d+)?|an?|one|half an?)\s*"
                         r"(minute|min|hour|hr|day|week)s?\b(?:\s+from now)?", text)
    if relative and ("in " in text or "after " in text or "from now" in text or text.strip().startswith(relative.group(1))):
        amount_text = relative.group(1)
        amount = {"a": 1, "an": 1, "one": 1, "half a": 0.5, "half an": 0.5}.get(amount_text)
        amount = float(amount_text) if amount is None else amount
        unit = relative.group(2)
        delta = {"minute": timedelta(minutes=amount), "min": timedelta(minutes=amount),
                 "hour": timedelta(hours=amount), "hr": timedelta(hours=amount),
                 "day": timedelta(days=amount), "week": timedelta(weeks=amount)}[unit]
        return now + delta

    # 2. The date part.
    day = None
    weekday_given = False
    match, text = _take(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if match:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if day is None:
        match, text = _take(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", text)
        if match:  # day/month[/year], as used in India and most of the world
            year = int(match.group(3)) if match.group(3) else now.year
            year = year + 2000 if year < 100 else year
            day = date(year, int(match.group(2)), int(match.group(1)))
            if not match.group(3) and day < now.date():
                day = day.replace(year=day.year + 1)
    if day is None:
        match, text = _take(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH_RE}\b(?:\s+(\d{{4}}))?", text)
        if not match:
            match, text = _take(rf"\b{MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s+(\d{{4}}))?", text)
            if match:
                month, number, year = match.group(1), match.group(2), match.group(3)
        else:
            number, month, year = match.group(1), match.group(2), match.group(3)
        if match:
            day = date(int(year) if year else now.year, _month_number(month), int(number))
            if not year and day < now.date():
                day = day.replace(year=day.year + 1)
    if day is None:
        match, text = _take(r"\bday after tomorrow\b", text)
        if match:
            day = now.date() + timedelta(days=2)
    if day is None:
        match, text = _take(r"\b(tomorrow|tmrw|tmr)\b", text)
        if match:
            day = now.date() + timedelta(days=1)
    if day is None:
        match, text = _take(r"\b(today|tonight)\b", text)
        if match:
            day = now.date()
            if match.group(1) == "tonight" and not re.search(r"\d", text):
                text += " tonight"
    if day is None:
        match, text = _take(r"\b(next\s+)?(" + "|".join(WEEKDAYS) + r"|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", text)
        if match:
            name = match.group(2)
            target = next(i for i, w in enumerate(WEEKDAYS) if w.startswith(name[:3]))
            ahead = (target - now.weekday()) % 7
            if match.group(1) and ahead == 0:
                ahead = 7
            day = now.date() + timedelta(days=ahead)
            weekday_given = True

    # 3. The time part.
    clock = None
    match = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if not 1 <= hour <= 12 or minute > 59:
            raise WhenError(f"'{match.group(0).strip()}' isn't a valid time")
        pm = match.group(3).startswith("p")
        hour = (hour % 12) + (12 if pm else 0)
        clock = time(hour, minute)
    if clock is None:
        match = re.search(r"\b(\d{1,2})[:.](\d{2})\b", text)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                raise WhenError(f"'{match.group(0)}' isn't a valid time")
            clock = time(hour + 12 if evening and 1 <= hour < 12 else hour, minute)
    if clock is None:
        match = re.search(r"\bat\s+(\d{1,2})\b", text)
        if match and int(match.group(1)) <= 23:
            hour = int(match.group(1))
            clock = time(hour + 12 if evening and 1 <= hour < 12 else hour)
    if clock is None:
        for word, value in NAMED_TIMES.items():
            if re.search(rf"\b{word}\b", text):
                clock = value
                break

    if day is None and clock is None:
        raise WhenError(f"I couldn't understand the time '{original}'. "
                        "Try something like 'tomorrow at 6 am', 'in 30 minutes' or 'Friday 5 pm'.")

    if day is None:  # only a time: today, or tomorrow if that time has already passed
        moment = datetime.combine(now.date(), clock)
        if moment <= now:
            moment += timedelta(days=1)
        return moment

    moment = datetime.combine(day, clock or DEFAULT_TIME)
    if moment <= now and weekday_given:  # "monday" said on a Monday evening = next Monday
        moment += timedelta(days=7)
    if moment <= now:
        raise WhenError(f"{describe_time(moment, now)} has already passed.")
    return moment


def describe_time(moment: datetime, now: datetime | None = None) -> str:
    """'Tue 7 Oct 2025, 06:00 (in 9 hours)' - shown in approval boxes and confirmations."""
    now = now or datetime.now()
    text = moment.strftime("%a %d %b %Y, %I:%M %p").replace(" 0", " ")
    seconds = (moment - now).total_seconds()
    if seconds < 0:
        return text
    if seconds < 3600:
        amount, unit = max(1, round(seconds / 60)), "minute"
    elif seconds < 86400 * 2:
        amount, unit = round(seconds / 3600), "hour"
    else:
        amount, unit = round(seconds / 86400), "day"
    return f"{text} (in {amount} {unit}{'' if amount == 1 else 's'})"
