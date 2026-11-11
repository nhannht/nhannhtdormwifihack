"""When to look at the router, and for how long to look away.

These numbers are facts about the router, not about the machine watching it, so
every watcher and the phone read them from here. They were measured on
the hotspot: a session cannot be renewed early, ``/login`` starts answering the
instant the old one lapses, and ``/status`` answers in about 15ms whether signed
in or not.
"""

from __future__ import annotations

#: How often to retry while the router is refusing, in seconds. Deliberately
#: impatient: ``/login`` starts answering the instant the old session goes, and
#: every attempt before that is free.
RETRY_EVERY_SECONDS = 2

#: Stop sleeping and start watching closely once the session has this many
#: seconds left, so the sign-in goes in as soon as the old one lapses.
POUNCE_SECONDS = 5

#: How long to keep trying after a lapse before giving up on this pass.
POUNCE_RETRY_SECONDS = 90

#: Never sleep longer than this, so a session that vanishes early is noticed and
#: stopping stays responsive.
MAXIMUM_SLEEP = 60

#: What to assume is left when the router reports an open session without saying
#: how long. Short on purpose: guessing high means missing the lapse.
BLIND_SLEEP_SECONDS = 30


def nap_while_active(seconds_left: int) -> int:
    """How long to sleep while a session is open with `seconds_left` to run.

    Long naps while there is time, then short ones on the boundary. Never past
    the moment the session ends, because the sign-in has to go in within a
    couple of seconds of the lapse to keep the gap short.
    """
    if seconds_left <= POUNCE_SECONDS:
        return RETRY_EVERY_SECONDS
    return max(min(seconds_left - POUNCE_SECONDS, MAXIMUM_SLEEP), 1)
