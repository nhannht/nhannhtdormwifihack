# nhannhtdormwifihack

Signs in to a dorm captive-portal WiFi and keeps the session alive, on macOS.

A Swift port of a Windows tool that was three programs: a Python GUI, a
hot-swappable Python login script, and a Rust daemon. This is one command-line
program with a shared core library.

## What it does

The WiFi is a MikroTik hotspot sitting behind an advertising portal. To get
online you normally load the portal page, watch an advert, and let its
JavaScript submit a login form to the router. This does the same network calls
directly, in three steps:

1. Ask the router for its login page and read the CHAP challenge off it.
2. Ask the portal for an account, through its JSON API.
3. Post that account back to the router, with the password hashed against the
   challenge as `MD5(id + password + challenge)`.

Then it keeps signing in again a few minutes *before* each session expires. The
router treats an early sign-in as a renewal and resets its timer, so the
connection never actually drops. Waiting for the drop and reconnecting, which
the Windows GUI did, costs a dropout every cycle.

## Build

```bash
swift build -c release
swift test
```

Requires macOS 14 or newer. The only dependency is Apple's
`swift-argument-parser`; everything else is system frameworks.

## Set up

Two values, and only one of them is required.

```bash
.build/release/nhannhtdormwifihack config set wifi_name "<your hotspot SSID>"
.build/release/nhannhtdormwifihack config set portal_fallback_url http://<your-portal>/login
```

`wifi_name` is the network to rejoin when the router stops answering. Join it by
hand once with Remember ticked, so macOS has the network saved; after that this
rejoins it on its own.

`portal_fallback_url` is only used when the router's login page does not name its
portal, which does happen.

**You do not set the router's address.** `gateway_ip` is empty by default, which
means "ask this Mac which router it is currently using". Hotspots on the same
campus answer at different addresses, so a fixed value stops working the moment
you join a different one. Set it only to force a specific address.

`config show` prints everything, `config path` says where the file is. An
existing `settings.json` from the Windows build can be copied straight into
`~/Library/Application Support/nhannhtdormwifihack/` and will load as-is.

## Use

```bash
nhannhtdormwifihack              # sign in, then keep the session alive
nhannhtdormwifihack login        # sign in once and exit
nhannhtdormwifihack status       # is the session up? is the internet up?
nhannhtdormwifihack logout       # end the session
nhannhtdormwifihack install      # run at login, restart if it dies
nhannhtdormwifihack uninstall    # stop that
```

`install` writes a launchd agent to `~/Library/LaunchAgents`. Copy the release
binary somewhere permanent first, then point at it:

```bash
cp .build/release/nhannhtdormwifihack /usr/local/bin/
/usr/local/bin/nhannhtdormwifihack install --executable /usr/local/bin/nhannhtdormwifihack
```

Logs go to `~/Library/Application Support/nhannhtdormwifihack/`.

## Settings

| Key | Meaning | Default |
|---|---|---|
| `gateway_ip` | The router | `192.168.200.1` |
| `interval` | Session length in minutes; must match the router | `15` |
| `prelogin_margin_secs` | How early to renew | `180` |
| `check_interval_secs` | How often to check the connection | `2` |
| `portal_fallback_url` | Portal address when the router names none | empty |
| `destination_url` | Where the router sends you after sign-in | Apple's check page |
| `logout_url` | Optional explicit logout address | empty |
| `wifi_name` | The network to be on | empty |
| `auto_connect` | Join `wifi_name` before signing in | `false` |
| `check_wifi_every_time` | Re-check the network on every pass, not just once | `false` |
| `auto_start` | Recorded for the Windows build; `install` is what starts this one | `false` |

Keys this build does not recognise are left alone when it saves, so a
`settings.json` shared with the Windows build keeps working.

### About `auto_connect`

macOS only tells an app which network it is on if that app holds Location
access, and a plain command-line binary has no way to ask for it. Run as a bare
binary, `auto_connect` therefore reports that it cannot tell which network you
are on and leaves it to you. To make it work, put the binary in a `.app` bundle
with `NSLocationWhenInUseUsageDescription` in its `Info.plist` and grant the
permission once in System Settings.

## Layout

```
Sources/nhannhtdormwifihackKit/    the engine, builds for iOS untouched
  CHAP.swift             the MikroTik hash and its escape decoding
  LoginEngine.swift      the three-step sign-in
  SessionScheduler.swift the renew-early loop
  SessionProbe.swift     asks the router whether we are signed in
  NetworkProbe.swift     TCP to a public DNS server, for speed
  WiFi.swift             reads and joins the wireless network
  PortalClient.swift     HTTP, cookies, certificates
  HTMLInputScraper.swift reads hidden form fields
  JSONValue.swift        carries the portal's context object opaquely
  Settings.swift         the settings file
  ActivityLog.swift      one log for the screen and the file
  ErrorText.swift        readable error messages

Sources/nhannhtdormwifihackCLI/    the commands
```

## Differences from the Windows version

Deliberate changes, not oversights:

- **Sign-in is confirmed with the router, not with a ping.** The old code
  finished by checking whether the internet worked. A previous session that had
  not expired yet answered yes regardless, so a failed sign-in reported success.
  This asks the router's own status page.
- **The escape decoder rejects bad input instead of guessing.** Digits 8 and 9
  are not octal. Python threw and quietly fell back to a plain hash the router
  always rejects; Rust emitted a stray backslash. Both produced a wrong hash and
  a misleading "invalid password".
- **Percent-decoding happens on bytes.** Both originals decoded to text first,
  which replaces any byte that is not valid UTF-8 and silently corrupts the
  challenge.
- **The portal's address lives in the settings file, not in the source.**
- **`destination_url` is Apple's connectivity check page**, so landing on it is
  itself proof the sign-in worked.
- **No DNS flush.** It needs root on macOS. A fresh URLSession per attempt
  achieves what the flush was there for.
