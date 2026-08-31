# nhannhtdormwifihack

Gets you onto a dorm WiFi that makes you watch an advert, and keeps you on it.

## The problem

Some dorm and campus WiFi works like this. You connect, and nothing loads. A page
pops up instead, you watch an advert, you press a button, and now the internet
works. Fifteen minutes later it stops working and you do the whole thing again.

That page is called a captive portal. This app does the clicking for you.

## What it does

It talks to the router the same way that page does, so you never see the page.

Then it watches the clock. When your fifteen minutes run out, it signs you in
again straight away. You get a gap of a few seconds instead of a gap that lasts
until you notice and go clicking.

## The part that surprised us

You cannot sign in early on this router.

That sounds like the obvious trick: sign in again at minute fourteen, before you
get kicked off, and never have a gap at all. It does not work. While your session
is open, the router takes the request and then simply never answers it. It hangs
until the app gives up.

So there is no way to renew early, and anything that tries just breaks. The app
asks the router how much time is left, waits for the session to end, and signs in
the moment it does.

## How the sign-in works

Three steps.

1. Ask the router for its login page. The page carries a random number, called a
   challenge, that is different every time.
2. Ask the advert portal for a username and password. It hands them out over a
   normal web API.
3. Send them back to the router, with the password scrambled together with the
   challenge as `MD5(id + password + challenge)`.

The scrambling is why step 1 has to happen first. The router will only accept a
password mixed with the exact challenge it just handed you.

## The safety net

You can name a second network, usually your phone's hotspot.

If the internet stays dead for fifteen seconds, the app gives up on the dorm WiFi
and joins that instead. This matters more than it sounds. Without it, one bad
experiment leaves you with no internet for the evening and no easy way to fix it.
With it, the worst case is fifteen seconds.

It backs off after each switch, so it cannot end up flapping between the two
networks forever.

## Install

Download the `.dmg` from the Releases page and drag the app to Applications. It
is signed and notarized by Apple, so it opens without any scary warnings.

Or build it yourself:

```bash
swift build -c release   # the command line tool
swift test               # 92 tests
./build-app.sh           # the menu bar app, into ~/Applications
```

Needs macOS 14 or newer. The only outside dependency is Apple's
`swift-argument-parser`.

## The menu bar app

A small icon sits in the menu bar. Click it and you get:

- whether you are signed in, and how long is left
- which network you are on and which router you are talking to
- whether the internet is actually up
- a button to pause and start the auto sign-in
- **Hotspot Network**, a list of the WiFi networks this Mac has saved, so you can
  point the app at a different one. Handy if the dorm renames theirs.
- **Show Log**, a window with everything the app has done

Only one copy runs at a time. If you start it in a terminal while the app is
running, the terminal one steps aside instead of both fighting over the router.

## The command line

Same engine, more knobs.

```bash
nhannhtdormwifihack              # sign in, then keep the session alive
nhannhtdormwifihack --menu       # do that, but in the menu bar app
nhannhtdormwifihack login        # sign in once and stop
nhannhtdormwifihack status       # am I signed in? is the internet up?
nhannhtdormwifihack logout       # end the session
nhannhtdormwifihack install      # start at login, restart if it dies
nhannhtdormwifihack uninstall    # stop doing that
```

`install` writes a launchd agent. Put the binary somewhere permanent first:

```bash
cp .build/release/nhannhtdormwifihack /usr/local/bin/
/usr/local/bin/nhannhtdormwifihack install \
    --executable /usr/local/bin/nhannhtdormwifihack
```

## Settings

The file lives in `~/Library/Application Support/nhannhtdormwifihack/`. Use
`config path` to find it and `config show` to read it.

Set your network name, and your safety net if you want one:

```bash
nhannhtdormwifihack config set wifi_name "<your hotspot name>"
nhannhtdormwifihack config set fallback_wifi_name "<your phone hotspot>"
```

Both have to be networks this Mac has already saved. Join each by hand once with
Remember ticked, or macOS will not let anything rejoin them later.

| Key | What it is | Default |
|---|---|---|
| `wifi_name` | The network to stay on | empty |
| `fallback_wifi_name` | The safety net network. Empty turns the net off | empty |
| `gateway_ip` | The router. Empty means ask this Mac | empty |
| `interval` | How many minutes a session lasts | `15` |
| `check_interval_secs` | How often to look at the connection | `2` |
| `portal_fallback_url` | Portal address, for when the router names none | empty |
| `destination_url` | Where the router sends you after sign-in | Apple's check page |
| `logout_url` | An explicit logout address, if there is one | empty |
| `auto_connect` | Rejoin `wifi_name` when the router goes quiet | `true` |
| `check_wifi_every_time` | Check the network every pass, not just once | `false` |

**Do not set `gateway_ip`.** Leaving it empty means "ask this Mac which router it
is using right now", and that is almost always what you want. Two hotspots on the
same campus can sit at different addresses, so a fixed one stops working the
moment you walk into a different building. Set it only to force a specific value.

Settings this build does not recognise are left alone when it saves, so a file
shared with the old Windows tool keeps working.

## Where things are

```
Sources/nhannhtdormwifihackKit/      the engine
  LoginEngine.swift        the three step sign-in
  SessionScheduler.swift   the loop that watches the clock
  SessionProbe.swift       asks the router if we are signed in
  CHAP.swift               the password scrambling
  WiFi.swift               reads and joins wireless networks
  NetworkProbe.swift       is the internet actually up
  PortalClient.swift       HTTP, cookies, certificates
  HTMLInputScraper.swift   reads hidden fields out of a web page
  JSONValue.swift          carries the portal's data around untouched
  Settings.swift           the settings file
  ActivityLog.swift        one log for the screen and the file
  StatusBoard.swift        what the menu bar reads
  RunLock.swift            stops two copies running at once
  Shell.swift              runs command line tools
  ErrorText.swift          turns errors into readable English

Sources/nhannhtdormwifihackCLI/      the commands
Sources/nhannhtdormwifihackMenuBar/  the menu bar app
```

## What changed from the Windows version this replaces

Deliberate, not oversights.

- **It asks the router whether the sign-in worked, instead of pinging the
  internet.** An old session that had not expired yet would answer the ping
  happily, so a failed sign-in used to report success.
- **The escape decoder refuses bad input instead of guessing.** The digits 8 and 9
  are not valid octal. Python threw and quietly fell back to a hash the router
  always rejects. Rust emitted a stray backslash. Both produced the wrong answer
  and blamed your password.
- **Percent decoding works on raw bytes.** Both older versions turned the data
  into text first, which quietly replaces any byte that is not valid UTF-8 and
  corrupts the challenge.
- **The portal address lives in the settings file, not in the source code.**
- **No DNS flush.** It needs root on macOS, and a fresh connection for each
  attempt does the same job.

## Licence

Apache 2.0. See `LICENSE` and `NOTICE`.
