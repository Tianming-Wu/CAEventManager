# -*- coding: utf-8 -*-
"""Self check-in: rotating on-screen token + a short-lived "presence" cookie.

The threat this closes: the service is published publicly, so an absent member
could otherwise check themselves in from home.  The big screen therefore shows a
QR whose token is *time sliced* - it is only valid for the 10 second window it
was rendered in (plus the previous one, for slow phones and slow wifis).

The token is handed out only through an admin-authenticated endpoint, because a
public one would let anybody outside the room mint the same token.  Consequence:
the machine that projects the screen must have an admin session - which is
naturally true when the operator projects from their own laptop.

Once a phone presents a valid token it is given a signed presence cookie, so the
member can take their time typing their student id without racing the clock.
"""

import hashlib
import hmac
import time

WINDOW = 10.0          # seconds per token window
TOLERANCE = 1          # accept the previous window too
PRESENCE_TTL = 15 * 60  # how long a verified phone may take to fill the form
COOKIE_NAME = "laffey_lot_presence"


def _secret(path_secret):
    return hmac.new(
        str(path_secret).encode("utf-8"), b"checkin-presence-v1", hashlib.sha256
    ).digest()


def _token(secret, window):
    return hmac.new(secret, str(window).encode("ascii"), hashlib.sha256).hexdigest()[:16]


def current(path_secret, now=None):
    """(token, epoch_seconds_when_it_expires) for the window covering *now*."""
    secret = _secret(path_secret)
    now = time.time() if now is None else now
    window = int(now // WINDOW)
    return _token(secret, window), (window + 1) * WINDOW


def verify(path_secret, token, now=None):
    if not token or not isinstance(token, str):
        return False
    secret = _secret(path_secret)
    now = time.time() if now is None else now
    window = int(now // WINDOW)
    supplied = token.strip()
    for w in range(window - TOLERANCE, window + TOLERANCE + 1):
        if hmac.compare_digest(_token(secret, w), supplied):
            return True
    return False


# --------------------------------------------------------------------------- #
# presence cookie: "<issued_at>.<signature>"
# --------------------------------------------------------------------------- #
def _sign(path_secret, issued):
    return hmac.new(
        _secret(path_secret), ("presence|%d" % int(issued)).encode("ascii"), hashlib.sha256
    ).hexdigest()[:32]


def issue_presence(path_secret, now=None):
    issued = int(time.time() if now is None else now)
    return "%d.%s" % (issued, _sign(path_secret, issued))


def check_presence(path_secret, value, now=None):
    if not value or "." not in value:
        return False
    issued_raw, _, sig = value.partition(".")
    try:
        issued = int(issued_raw)
    except ValueError:
        return False
    now = time.time() if now is None else now
    if issued > now + 60 or now - issued > PRESENCE_TTL:
        return False
    return hmac.compare_digest(_sign(path_secret, issued), sig)
