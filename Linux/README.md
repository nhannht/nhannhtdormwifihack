# Dorm WiFi on Linux

The macOS app in this repository, ported: the same three step MikroTik sign-in,
the same watcher that signs back in the moment a session lapses, and a GTK 4
window in place of the menu bar icon.

Python with the standard library, plus PyGObject for the window. No virtualenv,
no `pip install`, nothing to build.

## Install

```bash
sudo apt install python3-gi gir1.2-gtk-4.0
```

Those are the only outside dependencies. `gir1.2-gtk-4.0` pulls in the GTK 4
runtime, and `python3-gi` is PyGObject. The command line tool works without
them; only the window needs them.

Then, from this directory:

```bash
./bin/dormwifi --help
```

Nothing is installed into the system. `bin/dormwifi` puts this checkout on the
path and runs `python3 -m dormwifi`, so an edit is live the next time you run it.
To put it on your `PATH`:

```bash
ln -s "$PWD/bin/dormwifi" ~/.local/bin/dormwifi
```

For the window in the application menu, copy `data/nhannhtdormwifihack.desktop`
to `~/.local/share/applications/` and make sure `dormwifi` is on the `PATH` that
desktop files see.

## What it does on Linux

| | macOS build | this one |
|---|---|---|
| Router address | `sysctl` routing table | `/proc/net/route`, then the first address of the local subnet |
| Which network we are on | `networksetup` | `nmcli` |
| Rejoin a network | `networksetup -setairportnetwork` | `nmcli connection up id` |
| Start at login | launchd agent | systemd user unit |
| The UI | menu bar icon and a drop-down | one GTK 4 window |
| One watcher at a time | `flock` on a file | `flock` on the same kind of file |

Everything else is a straight port: the three step sign-in, the CHAP hashing,
the status page reader, the safety net with its backoff, the run lock, the log.

**Rejoining needs NetworkManager.** A network this machine has never saved cannot
be joined from here, because joining needs its password and this runs without
anyone watching. That is the same limit the macOS build has with
`networksetup`: join the hotspot and the fallback network by hand once, with
"Automatically connect" ticked, and they can be rejoined from then on.

**The icon in the top bar is real, and not drawn by GTK.** GTK 4 removed
`Gtk.StatusIcon`, and libayatana-appindicator — what most tray libraries use on
Linux — is a GTK 3 library, which a GTK 4 process cannot load. Neither is the
obstacle it looks like: a tray icon is a D-Bus conversation. This exports
`org.kde.StatusNotifierItem` and a `com.canonical.dbusmenu` menu, and whichever
shell runs `org.kde.StatusNotifierWatcher` draws them — GNOME with the
AppIndicator extension, KDE, Xfce, Cinnamon, Budgie. That is what
libayatana-appindicator does internally.

The shape of the icon says what the colour says, so it still means something in
a monochrome or recoloured panel: a filled circle signed in, a ring while it is
working, a triangle when the router is not there, a rounded pair of dots on the
safety-net network, two bars when paused. Hovering shows the headline and the
countdown; the menu has the same rows the macOS menu has, plus a live countdown
in its first line.

Closing the window hides it and leaves the watcher running, with a notification
to say so; **Quit is in the icon's menu**. On a desktop with no tray host the
window is the whole UI, and closing it quits, because otherwise there would be no
way back to it.

## The window

```bash
./bin/dormwifi gui
./bin/dormwifi run --menu     # same window, and follow its log here
```

- the state at the top, with the countdown moving as you read it
- which network, which router, whether the internet is actually up
- **Pause** / **Start**, **Sign in now**, **Log out**
- **Hotspot network**: the networks NetworkManager has saved, so you point it at
  a renamed hotspot by picking it rather than typing it
- the log, with the previous runs in it, and a button that opens the file

If a systemd service or a terminal already holds the watcher, the window notices
and becomes a viewer: it reads the status file and tails the log instead of
competing for the lock. Two watchers both post to `/login` the moment a session
lapses, and the router rejects the second one.

## The command line

Same engine, more knobs, and no GTK needed.

```bash
dormwifi              # sign in, then keep the session alive
dormwifi run          # the same thing
dormwifi login        # sign in once and exit
dormwifi status       # am I signed in? is the internet up?
dormwifi discover     # read the router's login page and look for the portal's address
dormwifi logout       # end the session
dormwifi install      # start at login, restart if it dies
dormwifi uninstall    # stop doing that
dormwifi config path  # where the settings file is
dormwifi config show  # what is in it
dormwifi config set interval 20
```

`install` writes `~/.config/systemd/user/nhannhtdormwifihack.service` and enables
it. Reinstall with `--verbose` to have the service log every step rather than
only warnings and failures.

## Settings

The file is `~/.config/nhannhtdormwifihack/settings.json` (or under
`$XDG_CONFIG_HOME`). The keys are the same as the macOS and Windows builds, so a
file from either loads unchanged, and keys this build does not recognise are
carried through when it saves.

```bash
dormwifi config set wifi_name "INET - Free WiFi"
dormwifi config set fallback_wifi_name "my phone"
```

| Key | What it is | Default |
|---|---|---|
| `wifi_name` | The network to stay on | empty |
| `fallback_wifi_name` | The safety net network. Empty turns the net off | empty |
| `gateway_ip` | The router. Empty means ask this machine | empty |
| `interval` | How many minutes a session lasts | `15` |
| `check_interval_secs` | How often to look at the connection | `2` |
| `portal_fallback_url` | Portal address, for when the router names none | empty |
| `destination_url` | Where the router sends you after sign-in | Apple's check page |
| `logout_url` | An explicit logout address, if there is one | empty |
| `auto_connect` | Rejoin `wifi_name` when the router goes quiet | `true` |
| `check_wifi_every_time` | Check the network every pass, not just once | `false` |

**Do not set `gateway_ip`.** Leaving it empty means "ask this machine which
router it is using right now", and that is almost always what you want.

The log, the status file the window reads and the run lock live in the same
directory:

```
~/.config/nhannhtdormwifihack/
  settings.json     the settings, mode 0600 because it can hold an API key
  activity.log      capped at 512 KiB, trimmed at a line boundary
  status.json       what the watcher is doing, for a window in another process
  watcher.lock      held by the one running watcher
  service.err.log   standard error of the systemd unit, if installed
```

## Tests

```bash
cd Linux && python3 -m unittest discover -s tests -t .
```

181 tests, no network and no hotspot needed: the CHAP hashing against the same
golden value the Swift tests use, the HTML field scraper, the status page reader,
the order the login steps go out in against a scripted router, the settings file
round trip, the log, the run lock, the route arithmetic, and the window's wiring.
The window tests build the real window unmapped and read the labels back, and
they skip themselves when there is no display.

## Licence

Apache 2.0, the same as the rest of this repository. See `../LICENSE`.
