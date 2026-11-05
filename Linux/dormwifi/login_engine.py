"""Logs into the hotspot.

Three steps. The router's login page carries a CHAP challenge; the portal in
front of it hands out the account to use; the router accepts that account hashed
against the challenge. The portal's own web page does all this in JavaScript
after showing an advert, and this does the same calls directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from . import error_text, session_probe
from .activity_log import ActivityLog
from .chap import ParseError, hash_password, plain_md5
from .html_scraper import form_action, query_items, value_identified_by, value_named
from .network_probe import has_internet
from .portal_client import PortalClient
from .settings import Settings

LoginKind = Literal["loggedIn", "alreadyActive", "invalidPassword", "sessionLimit", "failed"]


@dataclass(frozen=True)
class LoginResult:
    """How a login attempt ended."""

    kind: LoginKind
    reason: str = ""

    @property
    def is_connected(self) -> bool:
        return self.kind in ("loggedIn", "alreadyActive")

    @property
    def summary(self) -> str:
        if self.kind == "loggedIn":
            return "Logged in."
        if self.kind == "alreadyActive":
            return "Already connected, session still open."
        if self.kind == "invalidPassword":
            return "The router rejected the password."
        if self.kind == "sessionLimit":
            return "Too many sessions open on this account."
        return f"Failed: {self.reason}"


class EngineError(Exception):
    """A step of the sign-in that could not be done, in plain words."""

    def __init__(self, description: str) -> None:
        self.description = description
        super().__init__(description)

    @classmethod
    def gateway_unreachable(cls, attempts: int) -> EngineError:
        return cls(f"The router did not answer after {attempts} attempts.")

    @classmethod
    def gateway_not_a_login_page(cls) -> EngineError:
        return cls("The router answered, but not with a hotspot login page.")

    @classmethod
    def no_portal_url(cls) -> EngineError:
        return cls(
            "The router's login page names no portal, and no fallback is set. Run "
            "'dormwifi discover' to read the page and find the address, then set it with "
            "'dormwifi config set portal_fallback_url <address>'."
        )

    @classmethod
    def portal_rejected(cls, step: str, status: int) -> EngineError:
        return cls(f"The portal answered {step} with HTTP {status}.")

    @classmethod
    def no_login_form(cls) -> EngineError:
        return cls("The portal's reply carried no login form.")

    @classmethod
    def bad_url(cls, text: str) -> EngineError:
        return cls(f"Could not build a URL from: {text}")


@dataclass(frozen=True)
class GatewayPage:
    chap_id: str | None
    chap_challenge: str | None
    portal_url: str


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    dst: str
    popup: str


class LoginEngine:
    """One login attempt, against one settings snapshot."""

    #: `/login` takes 1.37s to first byte even when it is working, while every
    #: other endpoint on this router answers in about 12ms. A tight timeout turns
    #: a working sign-in into a failure.
    GATEWAY_TIMEOUT = 8.0
    GATEWAY_ATTEMPTS = 3

    def __init__(self, settings: Settings, log: ActivityLog) -> None:
        self.settings = settings
        self.log = log

    # MARK: - The whole sequence

    def run(self, client: PortalClient | None = None) -> LoginResult:
        """Sign in, unless a session is already open.

        Asking ``/status`` first is not an optimisation, it is the only safe
        order. While a session is open this router accepts the connection to
        ``/login`` and then never answers, so a sign-in attempt in that state
        cannot succeed: it can only stall until the timeout and be reported as a
        failure. The caller then treats a perfectly healthy connection as broken
        and starts tearing it down.

        Never sign out here either. Ending the session puts the router in the
        same non-answering state with no way back in. ``logout`` is a separate
        command for when the user actually wants to leave.
        """
        client = client or PortalClient()

        answer = session_probe.status(self.settings.gateway_ip, client)
        if answer.kind == "active":
            left = (
                f", {error_text.duration(answer.info.remaining)} left"
                if answer.info is not None and answer.info.remaining is not None
                else ""
            )
            self.log.info(f"Already signed in{left}.")
            return LoginResult("alreadyActive")

        if answer.kind == "unreachable":
            # Nothing is listening on this address. Asking it for a login page
            # three times over cannot change that, and the answer the caller
            # needs is "you are not on that network", not "the login failed".
            self.log.failure(
                f"The router at {self.settings.gateway_ip} did not answer: {answer.why}"
            )
            return LoginResult("failed", f"No hotspot router at {self.settings.gateway_ip}")

        try:
            self.log.info("Step 1 of 3: asking the router for its login page")
            gateway = self.fetch_gateway(client)

            self.log.info("Step 2 of 3: collecting an account from the portal")
            credentials = self.fetch_credentials(gateway.portal_url, client)
            self.log.info(f"Portal issued the account {credentials.username}")

            self.log.info("Step 3 of 3: signing in to the router")
            return self.sign_in(gateway, credentials, client)
        except EngineError as error:
            self.log.failure(error.description)
            return LoginResult("failed", error.description)
        except Exception as error:  # noqa: BLE001 - reported, never raised at the caller
            reason = error_text.short(error)
            self.log.failure(reason)
            return LoginResult("failed", reason)

    def sign_out(self, client: PortalClient | None = None) -> None:
        """End the current session, so a fresh one can be started."""
        client = client or PortalClient()
        target = (
            self.settings.logout_url
            if self.settings.logout_url
            else f"http://{self.settings.gateway_ip}/logout"
        )
        try:
            client.get(target, timeout=5)
        except Exception:  # noqa: BLE001 - logging out is best effort
            pass

    # MARK: - Step 1

    def fetch_gateway(self, client: PortalClient) -> GatewayPage:
        if not self.settings.gateway_ip:
            raise EngineError.bad_url(self.settings.gateway_ip)

        query = urlencode({"dst": self.settings.destination_url}, quote_via=quote)
        url = f"http://{self.settings.gateway_ip}/login?{query}"

        page = None
        answered_with_another_page = False

        for attempt in range(1, self.GATEWAY_ATTEMPTS + 1):
            try:
                response = client.get(url, timeout=self.GATEWAY_TIMEOUT)
                if response.is_success and _looks_like_login_page(response.body):
                    page = response
                    break
                if response.is_success:
                    answered_with_another_page = True
                    self.log.warning(
                        f"Router answered attempt {attempt} with a page that is not a "
                        "hotspot login, retrying"
                    )
                else:
                    self.log.warning(
                        f"Router answered attempt {attempt} with HTTP "
                        f"{response.status_code}, retrying"
                    )
            except Exception as error:  # noqa: BLE001 - each attempt is independent
                self.log.warning(
                    f"Router did not answer attempt {attempt}: {error_text.short(error)}"
                )
            if attempt < self.GATEWAY_ATTEMPTS:
                time.sleep(1)

        # Answering with the wrong page and not answering at all send the reader
        # to look in completely different places, so they are reported
        # separately.
        if page is None:
            raise (
                EngineError.gateway_not_a_login_page()
                if answered_with_another_page
                else EngineError.gateway_unreachable(self.GATEWAY_ATTEMPTS)
            )

        chap_id = value_identified_by("chap-id", page.body)
        chap_challenge = value_identified_by("chap-challenge", page.body)

        if chap_id is not None and chap_challenge is not None:
            self.log.info("Router issued a CHAP challenge")
        else:
            self.log.warning("Router issued no CHAP challenge; will send a plain hash")

        portal_url = self.portal_url_from(page.body)
        self.log.info(f"Portal is at {urlsplit(portal_url).hostname or portal_url}")

        # Say out loud where that address came from. When the router names its
        # own portal we are reading the truth off the page. When it does not we
        # are using a guess from the settings file. If the next step fails, this
        # line is the difference between suspecting the portal and suspecting the
        # address we invented for it.
        if form_action(page.body) is None:
            self.log.warning(
                f"The router at {self.settings.gateway_ip} named no portal, so this is "
                "falling back to portal_fallback_url. That address has only been confirmed "
                "on one router of this hotspot, so if the next step fails, suspect it first."
            )

        return GatewayPage(chap_id=chap_id, chap_challenge=chap_challenge, portal_url=portal_url)

    def portal_url_from(self, html: str) -> str:
        """Where to send the browser next, with every hidden field carried along."""
        action = form_action(html)
        items = query_items(html)

        if action:
            base = self._resolve(action)
        elif self.settings.portal_fallback_url:
            base = self.settings.portal_fallback_url
            if urlsplit(base).scheme == "":
                raise EngineError.bad_url(self.settings.portal_fallback_url)
        else:
            raise EngineError.no_portal_url()

        parts = urlsplit(base)
        existing = parse_qsl(parts.query, keep_blank_values=True)
        merged = existing + items
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(merged, quote_via=quote, safe=""),
                parts.fragment,
            )
        )

    def _resolve(self, action: str) -> str:
        """Turn the login form's `action` into a URL that can actually be fetched.

        Only an action that carries its own scheme is the portal naming itself.
        Everything else belongs to the router and is resolved against its login
        page, a bare `alogin.html` included. Treating every action that did not
        begin with a slash as absolute produced a URL with no scheme at all,
        which failed several steps later as an unexplained "unsupported URL".
        """
        # `//host/path` means "whatever scheme this page used", which is HTTP.
        if action.startswith("//"):
            return f"http:{action}"

        if urlsplit(action).scheme:
            return action

        login_page = f"http://{self.settings.gateway_ip}/login"
        return urljoin(login_page, action)

    # MARK: - Step 2

    def fetch_credentials(self, portal_url: str, client: PortalClient) -> Credentials:
        # The first visit sets the cookies every later call depends on, and can
        # redirect to whichever host actually serves the portal.
        #
        # Five seconds, not ten. This request goes out at the exact instant the
        # old session lapsed, which is the worst moment to ask the router to pass
        # traffic to a host out on the internet, and it does sometimes hang.
        # Retrying is free and works; waiting is what costs the user.
        landing = client.get(portal_url, timeout=5)
        final_url = landing.final_url

        parts = urlsplit(final_url)
        base_url = urlunsplit((parts.scheme, parts.netloc, "", "", ""))

        endpoints = self.settings.portal_endpoints

        def endpoint(path: str) -> str:
            return urljoin(base_url + "/", path)

        # Hand the portal an empty object; it replies with the context that every
        # later call has to echo back.
        verify = client.post_json(
            endpoint(endpoints.verify_url), {}, referer=final_url, origin=base_url, timeout=10
        )
        if not verify.is_success:
            raise EngineError.portal_rejected("VerifyUrl", verify.status_code)
        context = verify.json()

        customer_response = client.post_json(
            endpoint(endpoints.get_customer),
            context,
            referer=final_url,
            origin=base_url,
            timeout=10,
        )
        if customer_response.is_success:
            context = customer_response.json()

        customer = _dig(context, "captiveContext", "customer")
        if customer is None:
            customer = {}

        campaign = client.post_json(
            endpoint(endpoints.get_campaign_html),
            {
                "captiveContextDTO": context,
                "updateCustomerFlag": False,
                "customer": customer,
            },
            referer=final_url,
            origin=base_url,
            timeout=10,
        )
        if not campaign.is_success:
            raise EngineError.portal_rejected("GetCampaignHtml", campaign.status_code)

        payload = campaign.json()
        form = self.authentication_form(payload)
        if form is None:
            raise EngineError.no_login_form()

        username = value_named("username", form)
        password = value_named("password", form)
        if username is None or password is None:
            raise EngineError.no_login_form()

        # Telling the portal its advert was seen. It was not, but the portal
        # holds the account open based on this, so it is part of the flow.
        self._send_analytics(context, base_url, client)

        return Credentials(
            username=username,
            password=password,
            dst=value_named("dst", form) or self.settings.destination_url,
            popup=value_named("popup", form) or "false",
        )

    def authentication_form(self, payload: object) -> str | None:
        """The portal nests the login form at one of three depths, so all are tried."""
        paths = (
            ("captiveContextDTO", "captiveContext", "contentAuthenForm"),
            ("captiveContext", "contentAuthenForm"),
            ("contentAuthenForm",),
        )
        for path in paths:
            found = _dig(payload, *path)
            if isinstance(found, str) and found:
                return found
        return None

    def _send_analytics(self, context: object, base_url: str, client: PortalClient) -> None:
        url = urljoin(base_url + "/", self.settings.portal_endpoints.analytic)
        try:
            client.post_json(
                url,
                {
                    "captiveContextDTO": context,
                    "analyticType": "View",
                    "viewIndex": 1,
                },
                referer=None,
                origin=base_url,
                timeout=5,
            )
        except Exception:  # noqa: BLE001 - the advert was not really watched anyway
            pass

    # MARK: - Step 3

    def sign_in(
        self, gateway: GatewayPage, credentials: Credentials, client: PortalClient
    ) -> LoginResult:
        url = f"http://{self.settings.gateway_ip}/login"

        if gateway.chap_id is not None and gateway.chap_challenge is not None:
            try:
                password = hash_password(
                    credentials.password, gateway.chap_id, gateway.chap_challenge
                )
            except ParseError as error:
                # The old code quietly fell back to a plain hash here, which the
                # router always rejects, and reported "invalid password".
                self.log.failure(f"Could not read the router's challenge: {error.description}")
                return LoginResult("failed", f"Unreadable CHAP challenge: {error.description}")
        else:
            password = plain_md5(credentials.password)

        response = client.post_form(
            url,
            fields=[
                ("username", credentials.username),
                ("password", password),
                ("dst", credentials.dst),
                # What the portal's own form asked for, not a fixed guess.
                ("popup", credentials.popup),
            ],
            referer=None,
            origin=None,
            timeout=15,
        )

        return self.interpret_sign_in(response, client)

    def interpret_sign_in(self, response, client: PortalClient) -> LoginResult:
        body = response.body.lower()

        if "invalid password" in body or "invalid username" in body:
            return LoginResult("invalidPassword")
        if "session limit" in body or "too many" in body:
            return LoginResult("sessionLimit")

        # Landing on the connectivity check page means the router let us out.
        if "captive.apple.com" in response.final_url or "<title>success</title>" in body:
            return LoginResult("loggedIn")

        # Otherwise ask the router directly rather than guessing from the page.
        answer = session_probe.status(self.settings.gateway_ip, client)
        if answer.kind == "active":
            return LoginResult("loggedIn")
        if answer.kind == "expired":
            return LoginResult("failed", "The router still shows us as signed out.")

        # Last resort: is there actually internet?
        if has_internet(timeout=2.0):
            return LoginResult("loggedIn")
        return LoginResult("failed", "Signed in with no confirmation, and there is still no internet.")


def _looks_like_login_page(body: str) -> bool:
    return "chap-id" in body or "username" in body.lower()


def _dig(payload: object, *path: str) -> object | None:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
