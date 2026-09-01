import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("Finding the router")
struct DefaultRouteTests {

    /// `192.168.200.5` and friends, as the four bytes a socket carries.
    private func address(_ text: String) -> in_addr_t {
        var value = in_addr()
        #expect(inet_pton(AF_INET, text, &value) == 1)
        return value.s_addr
    }

    private func firstHost(_ ip: String, _ mask: String) -> String? {
        guard let found = DefaultRoute.firstHost(address: address(ip), mask: address(mask)) else {
            return nil
        }
        var value = in_addr(s_addr: found)
        var text = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        inet_ntop(AF_INET, &value, &text, socklen_t(INET_ADDRSTRLEN))
        return String(cString: text)
    }

    @Test("Both of this hotspot's networks put the router on the first address")
    func knownNetworks() {
        // The two routers this app has actually been used against. A /24 on one
        // network, a /16 on the other, and the same rule finds both.
        #expect(firstHost("192.168.200.5", "255.255.255.0") == "192.168.200.1")
        #expect(firstHost("10.11.7.9", "255.255.0.0") == "10.11.0.1")
    }

    @Test("The first address is worked out from the mask, not from the last octet")
    func respectsTheMask() {
        // A phone at .130 in a /25 is on the upper half of the block, so its
        // router is .129. Reading only the last octet would answer .1, which is
        // on the other network entirely.
        #expect(firstHost("192.168.1.130", "255.255.255.128") == "192.168.1.129")
    }

    @Test("A mask with no room for both a router and a host is refused")
    func refusesUnusableMasks() {
        // Nothing here can be the first host of anything, and answering anyway
        // would send every later step at an address that cannot exist.
        #expect(firstHost("10.0.0.1", "255.255.255.255") == nil)
        #expect(firstHost("10.0.0.1", "255.255.255.254") == nil)
        #expect(firstHost("10.0.0.1", "0.0.0.0") == nil)
    }

    #if os(macOS)

        @Test("The routing table gives the same router that route(8) prints")
        func agreesWithRoute() throws {
            // This replaced a version that launched `/sbin/route` and read its
            // output, so the test is that the two still agree. Read-only, and it
            // steps aside when this Mac has no network rather than failing.
            let printed = Shell.run("/sbin/route", ["-n", "get", "default"], timeout: 5)?
                .split(separator: "\n")
                .first { $0.contains("gateway:") }?
                .split(separator: ":", maxSplits: 1)
                .last
                .map { $0.trimmingCharacters(in: .whitespaces) }

            try #require(printed != nil, "this Mac has no default route to compare against")
            #expect(DefaultRoute.gateway() == printed)
        }

    #endif
}
