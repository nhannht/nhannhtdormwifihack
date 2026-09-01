import Foundation

/// How a login attempt ended.
public enum LoginResult: Sendable, Equatable {
    /// Logged in during this attempt.
    case loggedIn
    /// A session was already open, so nothing needed doing.
    case alreadyActive
    /// The router rejected the password the portal handed us.
    case invalidPassword
    /// The account is at its limit of open sessions.
    case sessionLimit
    case failed(String)

    public var isConnected: Bool {
        self == .loggedIn || self == .alreadyActive
    }

    public var summary: String {
        switch self {
        case .loggedIn: "Logged in."
        case .alreadyActive: "Already connected, session still open."
        case .invalidPassword: "The router rejected the password."
        case .sessionLimit: "Too many sessions open on this account."
        case .failed(let reason): "Failed: \(reason)"
        }
    }
}

/// Logs into the hotspot.
///
/// Three steps. The router's login page carries a CHAP challenge; the portal in
/// front of it hands out the account to use; the router accepts that account
/// hashed against the challenge. The portal's own web page does all this in
/// JavaScript after showing an advert, and this does the same calls directly.
public struct LoginEngine: Sendable {

    private let settings: Settings
    private let log: ActivityLog

    public init(settings: Settings, log: ActivityLog) {
        self.settings = settings
        self.log = log
    }

    public enum EngineError: Error, CustomStringConvertible {
        case gatewayUnreachable(attempts: Int)
        case gatewayNotALoginPage
        case noPortalURL
        case portalRejected(step: String, status: Int)
        case noLoginForm
        case badURL(String)

        public var description: String {
            switch self {
            case .gatewayUnreachable(let attempts):
                return "The router did not answer after \(attempts) attempts."
            case .gatewayNotALoginPage:
                return "The router answered, but not with a hotspot login page."
            case .noPortalURL:
                #if os(iOS)
                    return """
                        The router's login page names no portal, and no fallback is set. \
                        Type the portal address into Settings.
                        """
                #else
                    return """
                        The router's login page names no portal, and no fallback is set. \
                        Set portal_fallback_url in the settings file.
                        """
                #endif
            case .portalRejected(let step, let status):
                return "The portal answered \(step) with HTTP \(status)."
            case .noLoginForm:
                return "The portal's reply carried no login form."
            case .badURL(let text):
                return "Could not build a URL from: \(text)"
            }
        }
    }

    // MARK: - The whole sequence

    /// Sign in, unless a session is already open.
    ///
    /// Asking `/status` first is not an optimisation, it is the only safe order.
    /// While a session is open this router accepts the connection to `/login`
    /// and then never answers, so a sign-in attempt in that state cannot
    /// succeed: it can only stall until the timeout and be reported as a
    /// failure. The caller then treats a perfectly healthy connection as broken
    /// and starts tearing it down.
    ///
    /// There used to be a `force` flag that skipped this check, meant to renew a
    /// session early. It could never work here, because early renewal is exactly
    /// the request this router refuses to answer. What it did instead was turn
    /// every reconnection into: rejoin the wireless network, which resumes the
    /// existing session because the router keys sessions on MAC address, then
    /// ask `/login`, which stalls because that session is open, then call it a
    /// failure and rejoin again. That loop is what kept dropping the connection.
    ///
    /// Never sign out here either. Ending the session puts the router in the
    /// same non-answering state with no way back in. `logout` is a separate
    /// command for when the user actually wants to leave.
    public func run(using injectedClient: PortalClient? = nil) async -> LoginResult {
        let client = injectedClient ?? PortalClient()

        switch await SessionProbe.status(gatewayIP: settings.gatewayIP, client: client) {
        case .active(let info):
            let left = info.remaining.map { ", \(ErrorText.duration($0)) left" } ?? ""
            await log.info("Already signed in\(left).")
            return .alreadyActive

        case .unreachable(let why):
            // Nothing is listening on this address. Asking it for a login page
            // three times over cannot change that, and the answer the caller
            // needs is "you are not on that network", not "the login failed".
            await log.failure("The router at \(settings.gatewayIP) did not answer: \(why)")
            return .failed("No hotspot router at \(settings.gatewayIP)")

        case .expired, .unknown:
            break  // Signed out, and `/login` will answer. Carry on below.
        }

        do {
            await log.info("Step 1 of 3: asking the router for its login page")
            let gateway = try await fetchGateway(using: client)

            await log.info("Step 2 of 3: collecting an account from the portal")
            let credentials = try await fetchCredentials(from: gateway.portalURL, using: client)
            await log.info("Portal issued the account \(credentials.username)")

            await log.info("Step 3 of 3: signing in to the router")
            return try await signIn(gateway: gateway, credentials: credentials, using: client)
        } catch let error as EngineError {
            await log.failure(error.description)
            return .failed(error.description)
        } catch {
            let reason = ErrorText.short(for: error)
            await log.failure(reason)
            return .failed(reason)
        }
    }

    /// End the current session, so a fresh one can be started.
    public func signOut(using client: PortalClient? = nil) async {
        let client = client ?? PortalClient()
        let target =
            settings.logoutURL.isEmpty
            ? "http://\(settings.gatewayIP)/logout"
            : settings.logoutURL

        guard let url = URL(string: target) else { return }
        _ = try? await client.get(url, timeout: 5)
    }

    // MARK: - Step 1

    struct GatewayPage: Sendable {
        let chapID: String?
        let chapChallenge: String?
        let portalURL: URL
    }

    func fetchGateway(using client: PortalClient) async throws -> GatewayPage {
        guard var components = URLComponents(string: "http://\(settings.gatewayIP)/login") else {
            throw EngineError.badURL(settings.gatewayIP)
        }
        components.queryItems = [URLQueryItem(name: "dst", value: settings.destinationURL)]
        guard let url = components.url else { throw EngineError.badURL(settings.gatewayIP) }

        let attempts = 3
        var page: PortalResponse?
        var answeredWithAnotherPage = false

        for attempt in 1...attempts {
            do {
                // 8s, not the 5 the originals use. Measured on a real capture,
                // /login takes 1.37s to first byte even when it is working,
                // while every other endpoint on this router answers in about
                // 12ms. It is doing something slow, so a tight timeout turns a
                // working sign-in into a failure.
                let response = try await client.get(url, timeout: 8)
                if response.isSuccess, looksLikeLoginPage(response.body) {
                    page = response
                    break
                }
                if response.isSuccess {
                    answeredWithAnotherPage = true
                    await log.warning(
                        "Router answered attempt \(attempt) with a page that is not a hotspot "
                            + "login, retrying"
                    )
                } else {
                    await log.warning(
                        "Router answered attempt \(attempt) with HTTP \(response.statusCode), "
                            + "retrying"
                    )
                }
            } catch {
                await log.warning(
                    "Router did not answer attempt \(attempt): \(ErrorText.short(for: error))"
                )
            }
            if attempt < attempts {
                try? await Task.sleep(for: .seconds(1))
            }
        }

        // Answering with the wrong page and not answering at all send the reader
        // to look in completely different places, so they are reported
        // separately. The check that drew this distinction used to sit after the
        // loop, where `page` was already known to be a login page, so it could
        // never fire and an ISP block page was reported as an unreachable
        // router.
        guard let page else {
            throw answeredWithAnotherPage
                ? EngineError.gatewayNotALoginPage
                : EngineError.gatewayUnreachable(attempts: attempts)
        }

        let chapID = HTMLInputScraper.value(identifiedBy: "chap-id", in: page.body)
        let chapChallenge = HTMLInputScraper.value(identifiedBy: "chap-challenge", in: page.body)

        if chapID != nil, chapChallenge != nil {
            await log.info("Router issued a CHAP challenge")
        } else {
            await log.warning("Router issued no CHAP challenge; will send a plain hash")
        }

        let portalURL = try portalURL(from: page.body)
        await log.info("Portal is at \(portalURL.host() ?? portalURL.absoluteString)")

        // Say out loud where that address came from. When the router names its
        // own portal we are reading the truth off the page. When it does not we
        // are using a guess from the settings file, and that guess has only ever
        // been checked against one of this hotspot's routers. If the next step
        // fails, this line is the difference between suspecting the portal and
        // suspecting the address we invented for it.
        if HTMLInputScraper.formAction(in: page.body) == nil {
            await log.warning(
                """
                The router at \(settings.gatewayIP) named no portal, so this is falling back to \
                portal_fallback_url. That address has only been confirmed on one router of this \
                hotspot, so if the next step fails, suspect it first.
                """
            )
        }

        return GatewayPage(chapID: chapID, chapChallenge: chapChallenge, portalURL: portalURL)
    }

    private func looksLikeLoginPage(_ body: String) -> Bool {
        body.contains("chap-id") || body.lowercased().contains("username")
    }

    /// Where to send the browser next, with every hidden field carried along.
    func portalURL(from html: String) throws -> URL {
        let action = HTMLInputScraper.formAction(in: html)
        let items = HTMLInputScraper.queryItems(in: html)

        let base: URL
        if let action, !action.isEmpty {
            base = try resolve(action: action)
        } else if !settings.portalFallbackURL.isEmpty {
            guard let fallback = URL(string: settings.portalFallbackURL) else {
                throw EngineError.badURL(settings.portalFallbackURL)
            }
            base = fallback
        } else {
            throw EngineError.noPortalURL
        }

        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw EngineError.badURL(base.absoluteString)
        }
        components.queryItems = (components.queryItems ?? []) + items

        guard let url = components.url else { throw EngineError.badURL(base.absoluteString) }
        return url
    }

    /// Turn the login form's `action` into a URL that can actually be fetched.
    ///
    /// Only an action that carries its own scheme is the portal naming itself.
    /// Everything else belongs to the router and is resolved against its login
    /// page, a bare `alogin.html` included. Treating every action that did not
    /// begin with a slash as absolute produced a URL with no scheme at all,
    /// which failed several steps later as an unexplained "unsupported URL".
    private func resolve(action: String) throws -> URL {
        // `//host/path` means "whatever scheme this page used", which is HTTP.
        if action.hasPrefix("//") {
            guard let url = URL(string: "http:\(action)") else {
                throw EngineError.badURL(action)
            }
            return url
        }

        if let absolute = URL(string: action), absolute.scheme != nil {
            return absolute
        }

        let loginPage = URL(string: "http://\(settings.gatewayIP)/login")
        guard let url = URL(string: action, relativeTo: loginPage)?.absoluteURL else {
            throw EngineError.badURL(action)
        }
        return url
    }

    // MARK: - Step 2

    struct Credentials: Sendable {
        let username: String
        let password: String
        let dst: String
        let popup: String
    }

    func fetchCredentials(from portalURL: URL, using client: PortalClient) async throws
        -> Credentials
    {
        // The first visit sets the cookies every later call depends on, and can
        // redirect to whichever host actually serves the portal.
        let landing = try await client.get(portalURL, timeout: 10)
        let finalURL = landing.finalURL

        guard var baseComponents = URLComponents(url: finalURL, resolvingAgainstBaseURL: false)
        else {
            throw EngineError.badURL(finalURL.absoluteString)
        }
        baseComponents.path = ""
        baseComponents.query = nil
        baseComponents.fragment = nil
        guard let baseURL = baseComponents.url else {
            throw EngineError.badURL(finalURL.absoluteString)
        }

        let endpoints = settings.portalEndpoints

        func endpoint(_ path: String) throws -> URL {
            guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
                throw EngineError.badURL(path)
            }
            return url
        }

        // Hand the portal an empty object; it replies with the context that every
        // later call has to echo back.
        let verify = try await client.postJSON(
            try endpoint(endpoints.verifyURL),
            body: .object([:]),
            referer: finalURL,
            origin: baseURL,
            timeout: 10
        )
        guard verify.isSuccess else {
            throw EngineError.portalRejected(step: "VerifyUrl", status: verify.statusCode)
        }
        var context = try verify.json()

        let customerResponse = try await client.postJSON(
            try endpoint(endpoints.getCustomer),
            body: context,
            referer: finalURL,
            origin: baseURL,
            timeout: 10
        )
        if customerResponse.isSuccess {
            context = try customerResponse.json()
        }

        let customer = context.value(at: "captiveContext", "customer") ?? .object([:])
        let campaign = try await client.postJSON(
            try endpoint(endpoints.getCampaignHTML),
            body: .object([
                "captiveContextDTO": context,
                "updateCustomerFlag": .bool(false),
                "customer": customer,
            ]),
            referer: finalURL,
            origin: baseURL,
            timeout: 10
        )
        guard campaign.isSuccess else {
            throw EngineError.portalRejected(step: "GetCampaignHtml", status: campaign.statusCode)
        }

        let payload = try campaign.json()
        guard let form = authenticationForm(in: payload) else {
            throw EngineError.noLoginForm
        }

        guard let username = HTMLInputScraper.value(named: "username", in: form),
              let password = HTMLInputScraper.value(named: "password", in: form)
        else {
            throw EngineError.noLoginForm
        }

        // Telling the portal its advert was seen. It was not, but the portal
        // holds the account open based on this, so it is part of the flow.
        await sendAnalytics(context: context, baseURL: baseURL, using: client)

        return Credentials(
            username: username,
            password: password,
            dst: HTMLInputScraper.value(named: "dst", in: form) ?? settings.destinationURL,
            popup: HTMLInputScraper.value(named: "popup", in: form) ?? "false"
        )
    }

    /// The portal nests the login form at one of three depths depending on which
    /// call answered, so all three are tried.
    func authenticationForm(in payload: JSONValue) -> String? {
        let paths = [
            ["captiveContextDTO", "captiveContext", "contentAuthenForm"],
            ["captiveContext", "contentAuthenForm"],
            ["contentAuthenForm"],
        ]
        for path in paths {
            if let form = payload.value(at: path)?.stringValue, !form.isEmpty {
                return form
            }
        }
        return nil
    }

    private func sendAnalytics(
        context: JSONValue,
        baseURL: URL,
        using client: PortalClient
    ) async {
        guard let url = URL(string: settings.portalEndpoints.analytic, relativeTo: baseURL)?
            .absoluteURL
        else { return }

        _ = try? await client.postJSON(
            url,
            body: .object([
                "captiveContextDTO": context,
                "analyticType": .string("View"),
                "viewIndex": .integer(1),
            ]),
            referer: nil,
            origin: baseURL,
            timeout: 5
        )
    }

    // MARK: - Step 3

    func signIn(
        gateway: GatewayPage,
        credentials: Credentials,
        using client: PortalClient
    ) async throws -> LoginResult {
        guard let url = URL(string: "http://\(settings.gatewayIP)/login") else {
            throw EngineError.badURL(settings.gatewayIP)
        }

        let password: String
        if let chapID = gateway.chapID, let challenge = gateway.chapChallenge {
            do {
                password = try CHAP.hash(
                    password: credentials.password,
                    chapID: chapID,
                    chapChallenge: challenge
                )
            } catch {
                // The old code quietly fell back to a plain hash here, which the
                // router always rejects, and reported "invalid password".
                let reason = ErrorText.short(for: error)
                await log.failure("Could not read the router's challenge: \(reason)")
                return .failed("Unreadable CHAP challenge: \(reason)")
            }
        } else {
            password = CHAP.plainMD5(credentials.password)
        }

        let response = try await client.postForm(
            url,
            fields: [
                ("username", credentials.username),
                ("password", password),
                ("dst", credentials.dst),
                // What the portal's own form asked for, not a fixed guess.
                ("popup", credentials.popup),
            ],
            referer: nil,
            origin: nil,
            timeout: 15
        )

        return await interpretSignIn(response, using: client)
    }

    private func interpretSignIn(
        _ response: PortalResponse,
        using client: PortalClient
    ) async -> LoginResult {
        let body = response.body.lowercased()

        if body.contains("invalid password") || body.contains("invalid username") {
            return .invalidPassword
        }
        if body.contains("session limit") || body.contains("too many") {
            return .sessionLimit
        }

        // Landing on the connectivity check page means the router let us out.
        if response.finalURL.absoluteString.contains("captive.apple.com")
            || body.contains("<title>success</title>")
        {
            return .loggedIn
        }

        // Otherwise ask the router directly rather than guessing from the page.
        switch await SessionProbe.status(gatewayIP: settings.gatewayIP, client: client) {
        case .active:
            return .loggedIn
        case .expired:
            return .failed("The router still shows us as signed out.")
        case .unknown, .unreachable:
            // Last resort: is there actually internet?
            return await NetworkProbe.hasInternet()
                ? .loggedIn
                : .failed("Signed in with no confirmation, and there is still no internet.")
        }
    }
}
