"""Keeps the connection up, indefinitely.

The router hands out a fixed session, fifteen minutes on this hotspot, and there
is no way to extend one. Signing in again while a session is open is the obvious
thing to try and it does not work: the router accepts the connection to
``/login`` and then never answers until the session has actually gone. So the
only strategy available is to know exactly when the session ends and sign in
again the moment it does.

The router publishes the countdown itself on ``/status``, which answers in about
15ms whether signed in or out. That number is better than any clock kept here: it
survives suspend, a closed lid and clock drift, and it comes from the device that
actually enforces the limit. So this asks ``/status`` on every pass and does what
the answer says. There is no local timer and no state carried between passes to
get out of step with the router.

The cost is a gap of a few seconds once per session, while the old session lapses
and the new sign-in goes through. That gap is inherent to a router that refuses
early renewal; it cannot be closed from here.
"""

from __future__ import annotations

import threading
import time

from . import error_text, network_probe, session_probe
from .activity_log import ActivityLog
from .login_engine import LoginEngine, LoginResult
from .portal_client import PortalClient
from .session_pacing import (
    BLIND_SLEEP_SECONDS,
    MAXIMUM_SLEEP,
    POUNCE_RETRY_SECONDS,
    POUNCE_SECONDS,
    RETRY_EVERY_SECONDS,
    nap_while_active,
)
from .settings import Settings
from .status_board import (
    ON_FALLBACK,
    SIGNED_IN,
    SIGNING_IN,
    STARTING,
    STOPPED,
    WRONG_NETWORK,
    StatusBoard,
    StatusFile,
    WatcherStatus,
)
from .wifi import WiFiJoiner


class SessionScheduler:
    """The watcher: one pass at a time, until stopped."""

    #: How long the internet may be gone before the safety net fires, in seconds.
    INTERNET_GRACE_SECONDS = 15

    #: How long to leave the hotspot alone after falling back, in seconds, and
    #: the ceiling that wait grows to.
    #:
    #: Without this the two networks flap: on the phone hotspot the dorm router is
    #: unreachable, so we join the dorm hotspot, which fails, so the safety net
    #: puts us back on the phone, where the dorm router is unreachable again. Two
    #: joins a cycle, forever, each dropping the link for half a minute. Doubling
    #: the wait after every failure and only clearing it on a session that
    #: actually works is what stops that.
    FIRST_BACKOFF_SECONDS = 120
    LONGEST_BACKOFF_SECONDS = 1800

    #: How many times in a row the router has to ignore us before we conclude
    #: this machine is on the wrong network and rejoin.
    #:
    #: Not one. A single timeout means nothing: the router gets busy, the radio
    #: hiccups, and this machine roams between access points. Rejoining on one
    #: missed reply would drop a working link for no reason, which is the exact
    #: failure this whole class exists to avoid.
    UNREACHABLE_BEFORE_REJOINING = 3

    def __init__(
        self,
        settings: Settings,
        log: ActivityLog,
        board: StatusBoard | None = None,
        status_file: StatusFile | None = None,
    ) -> None:
        self.settings = settings
        self.log = log
        self._board = board
        self._status_file = status_file
        self._cancel = threading.Event()
        self._wifi = WiFiJoiner(settings, log, cancel=self._cancel)
        self._status = WatcherStatus(network=settings.wifi_name)

        #: When the internet went away, or None while it is working.
        self._offline_since: float | None = None
        #: Don't touch the dorm hotspot until this moment.
        self._leave_hotspot_alone_until: float | None = None
        self._backoff_seconds = self.FIRST_BACKOFF_SECONDS

    # MARK: - Control

    def stop(self) -> None:
        """Ask the loop to finish what it is doing and return."""
        self._cancel.set()

    @property
    def stopped(self) -> bool:
        return self._cancel.is_set()

    @property
    def _hotspot_name(self) -> str:
        """The name to put in a sentence about the network.

        An install that has not been told which network to use would otherwise
        log "it will simply have no internet" and "probably not on .", which read
        as a bug in the sentence rather than as a missing setting.
        """
        return self.settings.wifi_name or "the configured network"

    # MARK: - The loop

    def run(self) -> None:
        self.log.info(
            "Watching. This router allows one "
            f"{error_text.duration(self.settings.session_timeout_seconds)} session at a time "
            "and will not renew one early, so expect a gap of a few seconds once per session."
        )

        self._announce_hotspot()
        self._announce_safety_net()
        self._report(STARTING)

        unreachable_in_a_row = 0

        while not self._cancel.is_set():
            # The safety net goes first, before anything is asked of the hotspot.
            # Its whole job is to make sure a failed experiment costs fifteen
            # seconds of internet rather than an evening of it.
            if self._safety_net_fired():
                unreachable_in_a_row = 0
                continue

            state, info = self._current_session()

            if state == "active":
                unreachable_in_a_row = 0

                # A session that actually works clears the debt from every
                # earlier failure, so the next problem starts from a short wait
                # again rather than a long one.
                self._leave_hotspot_alone_until = None
                self._backoff_seconds = self.FIRST_BACKOFF_SECONDS

                self._status.has_internet = True
                self._status.expires_at = (
                    time.time() + info.remaining if info is not None and info.remaining is not None else None
                )
                self._report(
                    SIGNED_IN,
                    router=self.settings.resolving_gateway().gateway_ip,
                )

                # The router's own number, not a clock kept here.
                left = (
                    info.remaining
                    if info is not None and info.remaining is not None
                    else BLIND_SLEEP_SECONDS
                )
                nap = nap_while_active(left)
                if left > POUNCE_SECONDS:
                    self.log.info(
                        f"Signed in, {error_text.duration(left)} left. Next check in {nap}s."
                    )
                # Past the boundary the nap is short on its own, so the loop keeps
                # looking closely until the router says the session ended.
                self._sleep(nap)

            elif state == "signedOut":
                unreachable_in_a_row = 0
                self._report(SIGNING_IN)
                self._sign_in_and_wait()

            else:
                unreachable_in_a_row += 1

                if unreachable_in_a_row < self.UNREACHABLE_BEFORE_REJOINING:
                    self.log.info(
                        f"Router did not answer ({info}). Looking again, {unreachable_in_a_row} "
                        f"of {self.UNREACHABLE_BEFORE_REJOINING}."
                    )
                    self._sleep(self._check_delay)
                    continue

                # Persistently silent. The hotspot router always answers when we
                # are on the hotspot, so this means we are somewhere else: a
                # phone hotspot, a cable, the wrong wireless network. Whether that
                # other network has working internet is beside the point, because
                # being online is not the same as being where we are meant to be.
                #
                # Unless we are serving out a wait after a failed attempt. Then
                # sitting still is the entire point: the last try cost fifteen
                # seconds offline and repeating it immediately would cost that
                # again, on a loop.
                if (
                    self._leave_hotspot_alone_until is not None
                    and time.monotonic() < self._leave_hotspot_alone_until
                ):
                    left = max(int(self._leave_hotspot_alone_until - time.monotonic()), 0)
                    self.log.info(
                        f"Not on {self._hotspot_name}, but the last attempt failed, so "
                        f"leaving it alone for another {error_text.duration(left)}."
                    )
                    unreachable_in_a_row = 0
                    self._sleep(min(left, MAXIMUM_SLEEP))
                    continue

                self.log.warning(
                    f"The router has ignored {unreachable_in_a_row} checks in a row, so this "
                    f"machine is probably not on {self._hotspot_name}."
                )
                self._report(WRONG_NETWORK, detail=info)
                self._wifi.join_configured_network()
                self._offline_since = None
                unreachable_in_a_row = 0

        self._report(STOPPED)
        self.log.info("Stopped watching.")

    # MARK: - The safety net

    def _announce_hotspot(self) -> None:
        """Say at startup which network this is watching for, or that none is set.

        An empty `wifi_name` is the default, and it makes the join step a no-op:
        three unanswered checks in a row then log "probably not on the configured
        network" forever, which sounds like a network fault and is really a
        missing setting.
        """
        if self.settings.wifi_name:
            self.log.info(f"Watching for {self.settings.wifi_name}.")
            return

        self.log.warning(
            "No hotspot is set (wifi_name is empty), so this will never join a network: it "
            "will only sign in when this machine is already on the hotspot. Set it with "
            "'dormwifi config set wifi_name \"<name>\"', or pick one in the window."
        )

    def _announce_safety_net(self) -> None:
        """Say at startup whether there is a floor under this or not."""
        if not self.settings.fallback_wifi_name:
            self.log.warning(
                "No fallback network is set, so if this machine cannot get onto "
                f"{self._hotspot_name} it will simply have no internet. Set "
                "fallback_wifi_name to a saved network, a phone's hotspot for instance, to put "
                "a floor under that."
            )
            return

        from .wifi import saved_networks  # local: a subprocess call, only needed here

        # A network this machine has not saved cannot be rejoined from a command
        # line, so a net naming one is decorative and worse than none: it would
        # be trusted while not being there.
        if self.settings.fallback_wifi_name not in saved_networks():
            self.log.failure(
                f"{self.settings.fallback_wifi_name} is not one of this machine's saved "
                "networks, so it cannot be rejoined automatically and the safety net will not "
                "work. Join it by hand once with 'Automatically connect' ticked, then start this "
                "again."
            )
            return

        self.log.success(
            f"Safety net on: if the internet stays down for {self.INTERNET_GRACE_SECONDS}s, "
            f"this joins {self.settings.fallback_wifi_name} instead."
        )

    def _safety_net_fired(self) -> bool:
        """Did the net have to catch us? True means the loop should start over.

        The clock measures unexplained downtime. Joining a network takes the link
        away for about half a minute all by itself, so the two join paths restart
        the count rather than being punished for the gap they just caused. A
        sign-in does not restart it: a sign-in that is working restores the
        connection in a couple of seconds, so if fifteen of them pass with no
        internet, signing in is not working.
        """
        if not self.settings.fallback_wifi_name:
            return False

        if self._still_online():
            self._offline_since = None
            self._status.has_internet = True
            return False

        self._status.has_internet = False
        since = self._offline_since if self._offline_since is not None else time.monotonic()
        self._offline_since = since

        down = int(time.monotonic() - since)
        if down < self.INTERNET_GRACE_SECONDS:
            return False

        self.log.warning(
            f"No internet for {down}s. Falling back to {self.settings.fallback_wifi_name} so "
            "this machine is not left offline."
        )

        joined = self._wifi.join_fallback_network()
        self._offline_since = None

        if joined:
            self.log.success(
                f"On {self.settings.fallback_wifi_name}. You have internet again."
            )
            self._status.has_internet = True
            self._report(ON_FALLBACK, detail=self.settings.fallback_wifi_name)
        else:
            self.log.failure(f"Could not get onto {self.settings.fallback_wifi_name} either.")

        # Either way, stop hammering the hotspot that just failed us.
        self._leave_hotspot_alone_until = time.monotonic() + self._backoff_seconds
        self.log.info(
            f"Leaving {self._hotspot_name} alone for "
            f"{error_text.duration(self._backoff_seconds)}."
        )
        self._backoff_seconds = min(self._backoff_seconds * 2, self.LONGEST_BACKOFF_SECONDS)

        return True

    def _still_online(self) -> bool:
        """Is the connection actually working?

        Asked twice, because the probe gives false negatives often enough to
        cause pointless network changes.
        """
        if network_probe.has_internet(timeout=1.5):
            return True
        self._sleep(0.5)
        return network_probe.has_internet(timeout=2.0)

    # MARK: - Signing in

    def _sign_in_and_wait(self) -> None:
        """Sign in, and keep trying briefly while the router is still refusing.

        ``/login`` only starts answering once the old session has actually gone,
        so the first attempt on a boundary can still be refused. Retrying for a
        short window costs nothing and closes the gap to a few seconds.

        This deliberately reports nothing back to the loop. The loop asks the
        router what the truth is on its next pass rather than believing a return
        value from here, so there is no way for the two to get out of step.
        """
        deadline = time.monotonic() + POUNCE_RETRY_SECONDS
        started = time.monotonic()

        while not self._cancel.is_set():
            if self._sign_in().is_connected:
                return

            if time.monotonic() >= deadline:
                self.log.failure(
                    f"Could not sign in within {POUNCE_RETRY_SECONDS}s. Still trying."
                )
                return

            # Hand control back once the safety net's patience is up. Retrying in
            # here for a minute and a half while the net sits unused outside the
            # loop would defeat the point of having one: the whole promise is
            # fifteen seconds offline, not ninety.
            if (
                self.settings.fallback_wifi_name
                and int(time.monotonic() - started) >= self.INTERNET_GRACE_SECONDS
            ):
                self.log.warning(
                    f"Still not signed in after {int(time.monotonic() - started)}s. Handing over."
                )
                return

            self._sleep(RETRY_EVERY_SECONDS)

    def _current_session(self) -> tuple[str, str | session_probe.SessionInfo | None]:
        """What the router says about the session, reduced to what the loop needs."""
        # Resolved every time. Switching networks changes which router the portal
        # is on, and one settings file has to work on all of them.
        active = self.settings.resolving_gateway()
        if not active.gateway_ip:
            return ("unreachable", "this machine has no network at all")

        self._status.router_ip = active.gateway_ip

        answer = session_probe.status(active.gateway_ip, PortalClient())
        if answer.kind == "active":
            return ("active", answer.info)
        if answer.kind == "expired":
            return ("signedOut", None)
        if answer.kind == "unknown":
            # The router answered but named no session. That is what it serves at
            # the moment a session lapses, so this is a sign-out, not a lost
            # router. Reading it as unreachable meant rejoining the wireless
            # network at every session boundary, which costs about eight seconds
            # and a dropped link for no reason at all.
            return ("signedOut", None)
        return ("unreachable", answer.why)

    def _sign_in(self) -> LoginResult:
        active = self.settings.resolving_gateway_waiting_up_to(10)

        if not active.gateway_ip:
            self.log.warning("This machine has no network to sign in on.")
            return LoginResult("failed", "No network")

        # Always say which router is being tried. Without it, "nothing is
        # listening there" names no address and cannot be acted on: the usual
        # cause is being on a different network altogether, such as a phone
        # hotspot, and the address is the only thing that reveals that.
        self.log.info(f"Router is {active.gateway_ip}.")

        started = time.monotonic()
        result = LoginEngine(active, self.log).run()
        took = time.monotonic() - started
        self.log.info(f"{result.summary} ({took:.1f}s)")

        return result

    # MARK: - Timing

    @property
    def _check_delay(self) -> int:
        return min(max(self.settings.check_interval_secs, 1), 60)

    def _sleep(self, seconds: float) -> None:
        """Sleep, but wake up the moment we are asked to stop."""
        self._cancel.wait(max(seconds, 0))

    def _report(
        self,
        connection: str,
        detail: str = "",
        router: str | None = None,
    ) -> None:
        """Tell whoever is displaying the state what just changed."""
        self._status.connection = connection
        if connection not in (ON_FALLBACK, WRONG_NETWORK):
            self._status.detail = ""
        elif detail:
            self._status.detail = detail
        if router:
            self._status.router_ip = router

        if self._board is not None:
            self._board.publish(self._status)
        if self._status_file is not None:
            self._status_file.write(self._status)
