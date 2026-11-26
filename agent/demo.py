"""
Public demo: a web page where anyone with an access code can try the agent without
connecting a mailbox. The endpoints live in main.py; the rules live here.

- DEMO_ACCESS_CODES="mili:300,guest:20" turns the demo on. Each pair is code:limit,
  and the limit is how many drafts that code can ask for per UTC day. Without the
  variable the demo is off.
- The count is kept in memory: it starts again from zero with every deploy or restart.
- Neither the codes nor the text of the emails are ever logged or stored.
"""

import hmac
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

DEFAULT_MODEL = "claude-sonnet-5-5"
PAGE = Path(__file__).with_name("demo.html")

# Input limits, in characters. The page shows the same numbers.
MAX_SENDER = 200
MAX_SUBJECT = 300
MAX_BODY = 6000
MAX_BUSINESS_INFO = 20000
MAX_CODE = 200

NOT_AVAILABLE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo not available</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5}</style>
</head><body>
<h1>Demo not available</h1>
<p>The demo is turned off right now.</p>
<p lang="es">La demo no est&aacute; disponible en este momento.</p>
</body></html>"""


class DemoRequest(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_CODE)]
    business_type: Literal["store", "freelance", "hr"]
    sender: str = Field("", max_length=MAX_SENDER)
    subject: str = Field("", max_length=MAX_SUBJECT)
    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_BODY)
    ]
    business_info: str = Field("", max_length=MAX_BUSINESS_INFO)


def access_codes() -> dict[str, int]:
    """The codes in DEMO_ACCESS_CODES, as {code: drafts per day}. Entries that aren't
    code:number are skipped, so a typo can't open the demo to everybody."""
    codes = {}
    for entry in os.environ.get("DEMO_ACCESS_CODES", "").split(","):
        code, _, limit = entry.strip().rpartition(":")
        if code and limit.strip().isdecimal():
            codes[code.strip()] = int(limit)
    return codes


def demo_model() -> str:
    return os.environ.get("DEMO_MODEL") or DEFAULT_MODEL


def find_code(given: str, codes: dict[str, int]) -> str | None:
    """The configured code equal to `given`, or None. compare_digest takes the same time
    whether the code is right or wrong, and every code is checked (no early exit), so the
    response time doesn't reveal anything. It's given bytes because it only takes ASCII
    strings: a code with an "é" is just wrong, not an error."""
    found = None
    for code in codes:
        if hmac.compare_digest(
            given.encode("utf-8", "surrogatepass"), code.encode("utf-8", "surrogatepass")
        ):
            found = code
    return found


def today() -> date:
    return datetime.now(timezone.utc).date()


# (code, UTC date) -> drafts used. Old days are dropped on each use, so it doesn't grow.
_used: dict[tuple[str, date], int] = {}


def used_today(code: str) -> int:
    return _used.get((code, today()), 0)


def reserve(code: str, limit: int) -> int | None:
    """Takes one use of the code for today and returns how many are left, or None if
    there are none. It's taken before calling Claude, so requests that arrive together
    can't go over the limit; release() gives it back if the call fails."""
    day = today()
    for key in [key for key in _used if key[1] != day]:
        del _used[key]
    if _used.get((code, day), 0) >= limit:
        return None
    _used[(code, day)] = _used.get((code, day), 0) + 1
    return limit - _used[(code, day)]


def release(code: str) -> None:
    """A failed draft doesn't count against the visitor."""
    key = (code, today())
    if _used.get(key, 0) > 0:
        _used[key] -= 1
