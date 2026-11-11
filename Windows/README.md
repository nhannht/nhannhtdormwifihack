# Dorm WiFi on Windows

The macOS app in this repository, ported through the Linux version: the same
three step MikroTik sign-in, the same watcher that signs back in the moment a
session lapses, and the same safety net. Python and Tkinter, with nothing to
install and nothing to build.

## Install

Python 3.10 or newer, from [python.org](https://www.python.org/downloads/) or
the Microsoft Store. Tkinter is included with both. There is no virtualenv, no
`pip install`, and no compiler.

From this directory:

```powershell
.\bin\dormwifi.cmd --help
.\bin\dormwifi.cmd gui
```

For a real desktop app, put a Dorm WiFi icon on the Desktop once:

```powershell
.\bin\dormwifi.cmd shortcut
```

After that, double-click **Dorm WiFi** on the Desktop. The shortcut targets
`pythonw.exe`, so no terminal window appears. `install` creates the same
shortcut, and `uninstall` removes it. If you move this checkout, run `shortcut`
again so the shortcut points at the new place.

`bin\dormwifi.cmd` puts this checkout on `PYTHONPATH` and runs
`python -m dormwifi`, so an edit is live the next time it runs. To put it on your
`PATH`, add this directory's `bin` folder to the user `Path`, or copy the `.cmd`
somewhere permanent.

## What it does on Windows

| | macOS build | this one |
|---|---|---|
| Router address | `sysctl` routing table | `GetAdaptersAddresses`, through `ctypes` |
| Which network we are on | `networksetup` | `netsh wlan show interfaces` |
| Rejoin a network | `networksetup -setairportnetwork` | `netsh wlan connect` |
| Start at login | launchd agent | hidden VBScript at logon (Task Scheduler or Startup) |
| The UI | menu bar icon and a drop-down | one Tkinter window and a tray icon |
| One watcher at a time | `flock` on a file | `msvcrt.locking` on a file |

Everything else is a straight port: the three step sign-in, the CHAP hashing, the
status page reader, the safety net with its backoff, the run lock, the log.

**Rejoining needs a saved profile.** A network this machine has never joined
cannot be rejoined from here, because joining needs its password and this runs
without anyone watching. Join the hotspot and the fallback network by hand once,
with "Connect automatically" ticked, and they can be rejoined from then on.

**Closing the window hides it to the notification area.** The watcher keeps
running. Double-click the icon, or right-click it and choose **Show Dorm WiFi**,
to bring the window back. **Quit** in the tray menu, or the window's own Quit
button, stops the watcher. If Windows refuses the icon, closing the window quits
instead, because otherwise there would be no way back.

## The command line

Same engine as the window, more knobs.

```powershell
.\bin\dormwifi.cmd                  # sign in, then keep the session alive
.\bin\dormwifi.cmd login            # sign in once and exit
.\bin\dormwifi.cmd status           # am I signed in? is the internet up?
.\bin\dormwifi.cmd discover         # read the router's login page for a portal address
.\bin\dormwifi.cmd logout           # end the session
.\bin\dormwifi.cmd install          # start at login, and restart if it dies
.\bin\dormwifi.cmd uninstall        # stop doing that
.\bin\dormwifi.cmd config path      # where the settings file is
.\bin\dormwifi.cmd config show      # what is in it
.\bin\dormwifi.cmd config set interval 20
.\bin\dormwifi.cmd gui              # open the window
.\bin\dormwifi.cmd shortcut         # put a launcher on the Desktop
.\bin\dormwifi.cmd shortcut --remove
```

`install` writes `%APPDATA%\nhannhtdormwifihack\watch.vbs`, which runs the
watcher with no console window and starts it again five seconds after it exits.
On an elevated terminal it registers a Task Scheduler task named
`nhannhtdormwifihack` at logon; on a normal terminal, where Task Scheduler
answers "Access is denied", it puts the same script in your Startup folder
instead and says so. Either way it also creates the Desktop launcher.

Reinstall with `--verbose` to have the watcher log every step rather than only
warnings and failures. `uninstall` removes the script and the Desktop launcher,
ends and deletes the task if there is one, and stops a watcher that the launcher
started.

## The window

```powershell
.\bin\dormwifi-gui.cmd
.\bin\dormwifi.cmd gui
.\bin\dormwifi.cmd run --menu     # same window, and follow its log here
```

- the state at the top, with the countdown moving as you read it
- which network, which router, whether the internet is actually up
- **Pause** / **Start**, **Sign in now**
- **Hotspot network**: the profiles Windows has saved, so you point it at a
  renamed hotspot by picking it rather than typing it
- the log, with the previous lines in it, and a button that opens the file

Closing the window does not stop it: the window disappears and the Dorm WiFi
icon stays in the notification area, with the countdown in its tooltip. The tray
menu has Show, Pause/Start, Sign in now, Open log file, and Quit.

If the installed task or a terminal already holds the watcher, the window notices
and becomes a viewer: it reads the status file and tails the log instead of
competing for the lock. Two watchers both post to `/login` the moment a session
lapses, and the router rejects the second one.

## Settings

The file is `%APPDATA%\nhannhtdormwifihack\settings.json`. The keys are the same
as the macOS, Linux and Android builds, so a file from any of them loads
unchanged, and keys this build does not recognise are carried through when it
saves. `DORMWIFI_CONFIG_DIR` overrides the whole directory, which is what the
tests and a portable install use.

```powershell
.\bin\dormwifi.cmd config set wifi_name "INET - Free WiFi"
.\bin\dormwifi.cmd config set fallback_wifi_name "my phone"
```

| Key | What it is | Default |
|---|---|---|
| `wifi_name` | The network to stay on | empty |
| `fallback_wifi_name` | The safety net network. Empty turns the net off | empty |
| `username` | An account to sign in with, straight at the router | empty |
| `password` | Its password. Set both and the portal is never contacted | empty |
| `gateway_ip` | The router. Empty means ask this machine | empty |
| `interval` | How many minutes a session lasts | `15` |
| `check_interval_secs` | How often to look at the connection | `2` |
| `portal_fallback_url` | Portal address, for when the router names none | empty |
| `destination_url` | Where the router sends you after sign-in | Apple's check page |
| `logout_url` | An explicit logout address, if there is one | empty |
| `auto_connect` | Rejoin `wifi_name` when the router goes quiet | `true` |
| `check_wifi_every_time` | Check the network every pass, not just once | `false` |

**Do not set `gateway_ip`.** Leaving it empty means "ask this machine which
router it is using right now", and that is almost always what you want. Two
hotspots on the same campus can sit at different addresses, so a fixed one stops
working the moment you walk into a different building.

`config show` prints the password; the file lives under your own `%APPDATA%`,
which is already private to the account. Windows has no per-file mode bits, so
there is no 0600 equivalent to set.

The log, the status file the window reads and the run lock live in the same
directory:

```
%APPDATA%\nhannhtdormwifihack\
  settings.json     the settings
  activity.log      capped at 512 KiB, trimmed at a line boundary
  status.json       what the watcher is doing, for a window in another process
  watcher.lock      held by the one running watcher
  watch.vbs         the hidden launcher the Task Scheduler task runs, if installed
```

## This hotspot: Awing Connect

The dorm network (`INET - Free WiFi`, `WIFI INET 5G`) runs the **Awing Connect**
captive portal. The router's splash page names no portal at all — its JavaScript
assembles the portal URL at run time — so `portal_fallback_url` has to be set by
hand:

```powershell
.\bin\dormwifi.cmd config set portal_fallback_url "http://v1.awingconnect.vn"
```

The portal issues a free 15-minute account (`inet_15m-15m`); there is no username
or password on this network. `/login` stops answering entirely while a session is
open, so `/status` is the only endpoint safe to ask first. Do not call `/logout`
to end a session for testing: it hangs too. Wait for the lapse instead.

On hotspots with no portal at all, a `username` and `password` in the settings
file are the whole story: the app reads the challenge from the router, hashes the
password against it, and posts. No page, no advert, nothing to approve.

### Verified on the real hotspot

On 2026-11-11, this port signed in to `INET - Free WiFi` through Awing Connect:

```
Step 1 of 3: asking the router for its login page
Router issued a CHAP challenge
Portal is at v1.awingconnect.vn
Step 2 of 3: collecting an account from the portal
Portal issued the account inet_15m-15m
Step 3 of 3: signing in to the router
Logged in.
```

`status` then reported `Session: signed in`, an address, and `expires: in 14m 59s`;
`http://captive.apple.com/hotspot-detect.html` returned `Success`. The window,
opened against that session, showed **Signed in, 14m 5s left**, router
`192.168.200.1`, internet up, and the safety net network.

## Tests

```powershell
cd Windows
python -m unittest discover -s tests -t .
```

181 tests, no network and no hotspot needed: the CHAP hashing against the same
golden value the Swift and Linux tests use, the HTML field scraper, the status
page reader, the order the login steps go out in against a scripted router, the
settings round trip, the log, the Windows file lock, the route arithmetic, the
portal client against a loopback HTTP server (redirects, cookies, form encoding),
the `netsh` parsers, the Tkinter window's wiring, the tray commands and the
single-instance launcher path.

## Licence

Apache 2.0, the same as the rest of this repository. See `../LICENSE`.
