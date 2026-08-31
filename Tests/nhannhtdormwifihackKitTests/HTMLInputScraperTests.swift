import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("HTML scraping")
struct HTMLInputScraperTests {

    /// A MikroTik hotspot login page, in the shape the router actually serves:
    /// one form, the CHAP fields carried by id rather than name, and the empty
    /// credential boxes the user would normally type into.
    static let gatewayPage = """
        <html><head><title>hotspot login</title></head>
        <body>
        <form name="login" action="http://portal.example.test/login" method="post">
          <input type="hidden" name="dst" value="http://captive.apple.com/hotspot-detect.html" />
          <input type="hidden" name="popup" value="true" />
          <input type="hidden" id="chap-id" value="\\044" />
          <input type="hidden" id="chap-challenge" value="\\154\\263\\277\\262" />
          <input type="hidden" name="link-login-only" value="http://10.5.50.1/login" />
          <input name="username" type="text" value="" />
          <input name="password" type="password" />
        </form>
        </body></html>
        """

    /// What the portal returns inside its JSON: a bare fragment, single quoted,
    /// carrying the account to use.
    static let authenForm = """
        <form name='sendin' action='http://10.5.50.1/login' method='post'>
          <input type='hidden' name='username' value='pt-9f2c1a'>
          <input type='hidden' name='password' value='6bd41f0e'>
          <input type='hidden' name='dst' value='http://captive.apple.com/hotspot-detect.html'>
          <input type='hidden' name='popup' value='false'>
        </form>
        """

    // MARK: - Inputs

    @Test("Every input on the page is found")
    func findsAllInputs() {
        #expect(HTMLInputScraper.inputs(in: Self.gatewayPage).count == 7)
    }

    @Test("The CHAP fields are found by id")
    func findsCHAPFieldsByID() {
        #expect(HTMLInputScraper.value(identifiedBy: "chap-id", in: Self.gatewayPage) == "\\044")
        #expect(
            HTMLInputScraper.value(identifiedBy: "chap-challenge", in: Self.gatewayPage)
                == "\\154\\263\\277\\262"
        )
    }

    @Test("Looking up by id falls back to name")
    func idLookupFallsBackToName() {
        // Some firmware labels them with name instead of id.
        let page = #"<input name="chap-id" value="\044">"#
        #expect(HTMLInputScraper.value(identifiedBy: "chap-id", in: page) == "\\044")
    }

    @Test("Single quoted attributes are read")
    func singleQuotedAttributes() {
        #expect(HTMLInputScraper.value(named: "username", in: Self.authenForm) == "pt-9f2c1a")
        #expect(HTMLInputScraper.value(named: "password", in: Self.authenForm) == "6bd41f0e")
        #expect(HTMLInputScraper.value(named: "popup", in: Self.authenForm) == "false")
    }

    @Test("Unquoted attributes are read")
    func unquotedAttributes() {
        #expect(HTMLInputScraper.value(named: "popup", in: "<input name=popup value=true>") == "true")
    }

    // MARK: - Query items

    @Test("Fields with values are forwarded, empty ones are not")
    func queryItemsSkipEmptyValues() {
        let items = HTMLInputScraper.queryItems(in: Self.gatewayPage)
        let names = items.map(\.name)

        #expect(names.contains("dst"))
        #expect(names.contains("chap-id"))
        #expect(names.contains("chap-challenge"))
        // username has an empty value and password has none at all.
        #expect(!names.contains("username"))
        #expect(!names.contains("password"))
    }

    @Test("A field with no name is forwarded under its id")
    func fallsBackToIDAsKey() {
        let items = HTMLInputScraper.queryItems(in: Self.gatewayPage)
        #expect(items.first { $0.name == "chap-id" }?.value == "\\044")
    }

    @Test("Document order is kept")
    func preservesOrder() {
        let names = HTMLInputScraper.queryItems(in: Self.gatewayPage).map(\.name)
        #expect(names.first == "dst")
    }

    @Test("A repeated key keeps its first position and its last value")
    func repeatedKey() {
        let page = """
            <input name="a" value="1"><input name="b" value="2"><input name="a" value="3">
            """
        let items = HTMLInputScraper.queryItems(in: page)
        #expect(items.map(\.name) == ["a", "b"])
        #expect(items.first?.value == "3")
    }

    // MARK: - Form action

    @Test("The form action is found")
    func findsFormAction() {
        #expect(HTMLInputScraper.formAction(in: Self.gatewayPage) == "http://portal.example.test/login")
    }

    @Test("A page with no form reports no action")
    func noFormAction() {
        #expect(HTMLInputScraper.formAction(in: "<html><body>nothing here</body></html>") == nil)
    }

    @Test("An empty action counts as no action")
    func emptyFormAction() {
        #expect(HTMLInputScraper.formAction(in: #"<form action="">"#) == nil)
    }

    // MARK: - Entities

    @Test("Entities in values are decoded")
    func decodesEntities() {
        let page = #"<input name="dst" value="http://x.test/?a=1&amp;b=2">"#
        #expect(HTMLInputScraper.value(named: "dst", in: page) == "http://x.test/?a=1&b=2")
    }

    @Test("A double-escaped entity is only decoded once")
    func doesNotDoubleDecode() {
        let page = #"<input name="x" value="&amp;lt;">"#
        #expect(HTMLInputScraper.value(named: "x", in: page) == "&lt;")
    }
}
