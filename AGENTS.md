# nhannhtdormwifihack

Signs in to a captive-portal WiFi and keeps the session alive: a MikroTik CHAP
sign-in, a watcher that renews at the session boundary, and a safety net that
falls back to another network when the internet stays down.

```
Sources/nhannhtdormwifihackKit/    the Swift engine (macOS and iOS)
Sources/nhannhtdormwifihackCLI/    the Swift command line tool
Sources/nhannhtdormwifihackMenuBar/ the macOS menu bar app
iOS/                               the phone app and its tunnel
Tests/                             the Swift tests
Linux/                             the Linux port: Python, GTK 4 window, tray icon
```

`Linux/` has its own `README.md` and its own settings file
(`~/.config/nhannhtdormwifihack/settings.json`). Checks: `swift test` for the
Swift side, `cd Linux && python3 -m unittest discover -s tests -t .` for the port.

## The hotspots this exists for

Verified on `INET - Free WiFi` / `WIFI INET 5G`, 2026-11-06. Re-verify before
trusting any of it; the portal is a third party's.

- Router: MikroTik at `192.168.200.1` (a second address, `10.11.0.1`, answers for
  the portal's hostname while the client is captive).
- Portal: **Awing Connect at `http://v1.awingconnect.vn`**. The router's splash
  page names no portal at all — its JavaScript assembles the portal URL at run
  time — so `portal_fallback_url` has to be set by hand and `dormwifi discover`
  cannot find it.
- The portal issues a free 15-minute account (`inet_15m-15m`); there is no
  username or password on this network.
- `/login` stops answering entirely while a session is open. `/status` is the only
  endpoint safe to ask first, and `/logout` hangs too — never call it to end a
  session for testing. Wait for the lapse instead.
- While captive, plain HTTP to the portal host passes through the walled garden;
  HTTPS to it is reset in curl and in Chromium alike (`ERR_CONNECTION_CLOSED`).

## Do not repeat

- **Diagnosing a captive portal from the pages** — don't build a theory from HTML
  the router serves. Record one real sign-in (`sudo Linux/tools/capture-login.sh`)
  and read the requests in the pcap. Five wrong hypotheses (portal API, account,
  click-through form, WISPr, wrapped `dst` chain) each cost an hour; one capture
  answered it.
- **Assuming a credential model** — a free hotspot has no login, and the account
  its status page displays belongs to the *previous* session. Check whether the
  portal issues accounts before adding username/password anywhere.
- **Testing on the live network without a rollback** — any script that changes
  the connection restores it in a trap, re-enables autoconnect, and verifies a
  204 from a connectivity check afterwards. Never leave `autoconnect-priority`
  changed: a network with no internet then wins over a working one.
- **`pgrep -f <text>` followed by a kill** — the pattern matches the shell running
  it. List with `pgrep -f "[t]ext"` and kill those PIDs.
- **Bounding an interactive capture with `timeout`** — it swallows ^C, so the
  person recording cannot stop it. `exec` the capture tool instead.
- **Wiring an installed command** — test it through the installed symlink, not
  through the checkout path. `~/.local/bin/<name>` makes `dirname $0` resolve to
  `~/.local`, so a script that derives its package directory from `$0` fails with
  "No module named …" while the checkout is fine; follow the link first.
- **Desktop entries** — run `desktop-file-validate` (an unregistered category
  hides the entry), and make `StartupWMClass` match the window's real WM_CLASS
  from `xprop`: for `python3 -m package` that is `__main__.py` until
  `GLib.set_prgname` is called, and a mismatch puts two icons in the dock.
- **Calling a sign-in fixed** — the evidence is `Logged in.` in the log *and* a
  204 from the connectivity check, in the same run. A session someone else opened
  proves nothing.
