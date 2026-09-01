import Foundation

/// The router this device sends its traffic through.
///
/// The captive portal always sits on it, and its address is different on every
/// network: one iNET network answers on `192.168.200.1`, another on `10.11.0.1`.
/// Asking the system each time is what lets one settings file work on both
/// without being edited in between, which a fixed address cannot do.
///
/// The two platforms answer this from different places, because they have to.
/// A Mac has the kernel's routing table and that is the real answer. An iPhone
/// does not publish its routing table to an app, so there the answer is worked
/// out from the address the network handed this phone. Neither is a fallback
/// for the other; each is the best answer available where it runs.
///
/// Whatever comes back is only ever a candidate, and nothing here trusts it.
/// The next thing that happens to this address is that `SessionProbe` asks it
/// for `/status`, and an address that does not answer is reported as such. So a
/// wrong guess shows up immediately as "nothing answered at 10.0.0.1" rather
/// than as a mysterious login failure, and `gateway_ip` in the settings file
/// overrides the whole question.
public enum DefaultRoute {

    /// The gateway of the default route, or nil when this device has no network.
    public static func gateway() -> String? {
        #if os(macOS)
            return routingTableGateway()
        #else
            return firstHostOfLocalSubnet()
        #endif
    }

    /// Wait for a default route to exist, for up to `seconds`.
    ///
    /// Joining a network returns as soon as the radio associates, which is
    /// several seconds before DHCP hands out an address and installs a route.
    /// Asking for the router inside that gap gets nothing, and reporting "no
    /// network at all" then is wrong: the network is a few seconds away from
    /// being ready.
    public static func waitForGateway(upTo seconds: Int = 20) async -> String? {
        for _ in 0..<max(seconds, 1) {
            if let found = gateway() { return found }
            try? await Task.sleep(for: .seconds(1))
        }
        return gateway()
    }

    // MARK: - macOS: read the routing table

    #if os(macOS)

        /// The gateway of the default IPv4 route, straight out of the kernel.
        ///
        /// This is what `/sbin/route -n get default` prints, read from the same
        /// place `route` reads it. It used to be done by launching `route` and
        /// scraping its output, which cost a process launch every pass and
        /// cannot be done from a phone at all.
        private static func routingTableGateway() -> String? {
            // "Every IPv4 route that has a gateway." The default route is the
            // one among them whose destination is 0.0.0.0.
            var mib: [Int32] = [CTL_NET, PF_ROUTE, 0, AF_INET, NET_RT_FLAGS, RTF_GATEWAY]

            // Asked twice on purpose: once with no buffer to learn the size,
            // once to fill it. The table can grow between the two calls, so the
            // second is allowed to fail and simply reports no gateway rather
            // than reading past the end of the buffer.
            var size = 0
            guard sysctl(&mib, u_int(mib.count), nil, &size, nil, 0) == 0, size > 0 else {
                return nil
            }

            var buffer = [UInt8](repeating: 0, count: size)
            let read = buffer.withUnsafeMutableBytes { raw -> Bool in
                guard let base = raw.baseAddress else { return false }
                return sysctl(&mib, u_int(mib.count), base, &size, nil, 0) == 0
            }
            guard read else { return nil }

            return buffer.withUnsafeBytes { raw in defaultGateway(in: raw, length: size) }
        }

        /// Walk the routing messages and pick the gateway of the default route.
        private static func defaultGateway(
            in raw: UnsafeRawBufferPointer, length: Int
        ) -> String? {
            guard let base = raw.baseAddress else { return nil }

            var offset = 0
            while offset + MemoryLayout<rt_msghdr>.size <= length {
                let header = base.loadUnaligned(fromByteOffset: offset, as: rt_msghdr.self)
                let messageLength = Int(header.rtm_msglen)

                // A message claiming no length, or more length than is left,
                // would walk this loop off the end of the buffer or spin
                // forever.
                guard messageLength > 0, offset + messageLength <= length else { return nil }
                defer { offset += messageLength }

                guard header.rtm_flags & RTF_GATEWAY != 0,
                      header.rtm_addrs & RTA_DST != 0,
                      header.rtm_addrs & RTA_GATEWAY != 0
                else { continue }

                // The addresses follow the header back to back, in the order of
                // the bits set in `rtm_addrs`, each padded to a four byte
                // boundary. They are not all the same size and not all of them
                // are IPv4, so the only way to reach the gateway is to step over
                // everything before it.
                var cursor = offset + MemoryLayout<rt_msghdr>.size
                var destination: in_addr?
                var gateway: in_addr?

                for slot in 0..<RTAX_MAX {
                    guard header.rtm_addrs & (1 << slot) != 0 else { continue }
                    guard cursor + MemoryLayout<sockaddr>.size <= offset + messageLength
                    else { break }

                    let address = base.loadUnaligned(fromByteOffset: cursor, as: sockaddr.self)

                    // A zero length means the socket address is a bare four byte
                    // stub rather than a real one, which the kernel does emit.
                    let addressLength =
                        address.sa_len == 0 ? MemoryLayout<UInt32>.size : Int(address.sa_len)

                    if address.sa_family == sa_family_t(AF_INET),
                       cursor + MemoryLayout<sockaddr_in>.size <= offset + messageLength
                    {
                        let inet = base.loadUnaligned(fromByteOffset: cursor, as: sockaddr_in.self)
                        if slot == RTAX_DST { destination = inet.sin_addr }
                        if slot == RTAX_GATEWAY { gateway = inet.sin_addr }
                    }

                    cursor += (addressLength + 3) & ~3
                }

                // Destination 0.0.0.0 is what makes this the default route
                // rather than a route to one particular network.
                guard let destination, destination.s_addr == 0, let gateway else { continue }
                if let text = Self.text(for: gateway) { return text }
            }

            return nil
        }

    #endif

    // MARK: - iOS: work it out from this device's own address

    /// The first usable address of the network this device is on.
    ///
    /// iOS does not let an app read the routing table. `net/route.h` is not even
    /// in the SDK, and Apple's position is that reaching the table through
    /// `sysctl` is unsupported on iOS because the layout of what comes back is
    /// undocumented there. So the routing table is not available to ask.
    ///
    /// What is available, through the ordinary documented `getifaddrs`, is the
    /// address and mask this network handed the phone. On a home or hotel or
    /// dorm network the router is the first address on the network, and that
    /// holds on both of this hotspot's networks: `192.168.200.5/24` gives
    /// `192.168.200.1`, and a `10.11.x.y/16` address gives `10.11.0.1`. Both
    /// are the addresses those routers actually answer on.
    ///
    /// It is a convention rather than a rule, so on a network that puts its
    /// router somewhere else this returns an address that will not answer. The
    /// app says exactly that when it happens, and `gateway_ip` in the settings
    /// is there to name the real one.
    static func firstHostOfLocalSubnet() -> String? {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return nil }
        defer { freeifaddrs(list) }

        for interface in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let flags = Int32(interface.pointee.ifa_flags)

            guard flags & IFF_UP != 0,
                  flags & IFF_RUNNING != 0,
                  flags & IFF_LOOPBACK == 0,
                  // A wireless network only. `pdp_ip0` is the cellular link, and
                  // it is a point to point route with no local network on it, so
                  // its "first host" would be an address nothing is at.
                  flags & IFF_POINTOPOINT == 0,
                  String(cString: interface.pointee.ifa_name).hasPrefix("en"),
                  let addressPointer = interface.pointee.ifa_addr,
                  let maskPointer = interface.pointee.ifa_netmask,
                  addressPointer.pointee.sa_family == sa_family_t(AF_INET),
                  maskPointer.pointee.sa_family == sa_family_t(AF_INET)
            else { continue }

            let address = UnsafeRawPointer(addressPointer)
                .loadUnaligned(as: sockaddr_in.self).sin_addr.s_addr
            let mask = UnsafeRawPointer(maskPointer)
                .loadUnaligned(as: sockaddr_in.self).sin_addr.s_addr

            guard let gateway = firstHost(address: address, mask: mask),
                  let text = text(for: in_addr(s_addr: gateway))
            else { continue }

            return text
        }

        return nil
    }

    /// Network address plus one, in network byte order throughout.
    ///
    /// A mask of `/31` or narrower has no room for a router and a host, so those
    /// are rejected rather than answered with an address that cannot exist. A
    /// mask of `/0` is rejected too: it describes no network to be the first
    /// host of.
    static func firstHost(address: in_addr_t, mask: in_addr_t) -> in_addr_t? {
        let hostAddress = UInt32(bigEndian: address)
        let hostMask = UInt32(bigEndian: mask)

        guard hostMask != 0, hostMask != .max, hostMask != .max - 1 else { return nil }

        let network = hostAddress & hostMask
        return (network + 1).bigEndian
    }

    /// `192.168.200.1` from four bytes.
    private static func text(for address: in_addr) -> String? {
        var value = address
        var text = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        guard inet_ntop(AF_INET, &value, &text, socklen_t(INET_ADDRSTRLEN)) != nil else {
            return nil
        }
        return String(cString: text)
    }
}
