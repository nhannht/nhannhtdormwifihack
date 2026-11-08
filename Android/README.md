# Dorm WiFi on Android

The same app, on a phone: the three step MikroTik sign-in, the watcher that signs
back in the moment a session lapses, and a notification where the Linux tray icon
was.

Java, against the Android framework and nothing else. No Gradle, no AndroidX, no
third-party dependency: `build.sh` calls the Android build tools directly, which
is the same idea as `bin/dormwifi` on Linux putting this checkout on the path.

```
Android/
  app/AndroidManifest.xml        permissions, the service, the boot receiver
  app/res/                       layout, colours, icons
  app/java/.../engine/           the portable half: everything that decides a sign-in
  app/java/.../                  the Android half: service, screen, radio, notifications
  test/java/.../engine/          the engine's checks, run on a plain JVM
  build.sh                       → .build/dormwifi.apk
  check.sh                       → the engine's checks, no phone needed
  install.sh                     → adb install, then open it
```

## Build and install

Two toolchains, and they are not ones this repository already had:

```bash
# an Android SDK with one platform and build-tools 35 or newer
#   platform-34-ext7_r03.zip and build-tools_r35.0.1_linux.zip from
#   https://dl.google.com/android/repository/ , unzipped into
#   ~/Android/Sdk/platforms/android-34 and ~/Android/Sdk/build-tools/35.0.1
# a JDK, for javac and keytool
# adb, from android-tools-adb or platform-tools

./build.sh      # → .build/dormwifi.apk
./check.sh      # 105 checks, no phone, no hotspot
./install.sh    # adb install -r -g, then open it
```

`ANDROID_SDK_ROOT` and `JAVA_HOME` override where `build.sh` looks. **build-tools
35 or newer** is a real requirement rather than a preference: 34's `d8` crashes
with a bare `NullPointerException` on class files javac 21 produces, because it
chokes on a `MethodParameters` entry with no name — which is what javac writes for
an inner class's outer-instance parameter. `build.sh` says so if it happens.

The first build makes a signing key in `.build/dormwifi.jks` and keeps it there.
Android only needs every update to an installed app to carry the same signature as
the one before, so this does not have to be a real release key. Lose it and the
next install needs an uninstall first, and the settings go with it.

## What it does on Android

| | Linux build | this one |
|---|---|---|
| Router address | `/proc/net/route` | `LinkProperties` default route, then the DHCP lease |
| Which network we are on | `nmcli` | `ConnectivityManager`, Wi-Fi transport only |
| Every request | the default route | bound to the Wi-Fi network with `Network.openConnection` |
| Is there internet | TCP to 8.8.8.8:53 | the same, bound to Wi-Fi |
| Rejoin a network | `nmcli connection up id` | **nothing: Android forbids it** |
| Start at login | systemd user unit | foreground service, plus a boot receiver |
| The UI | GTK 4 window and a tray icon | one Activity, and the notification |
| One watcher at a time | `flock` on a file | one process, one service, one thread |

Everything else is a straight port: the three step sign-in, the CHAP hashing, the
status page reader, the safety net with its backoff, the log.

**Binding every request to Wi-Fi is not a detail.** A phone with mobile data has a
default network that is usually the mobile one — including while Wi-Fi sits captive
with no internet. A request that went out over mobile data would either fail
confusingly or succeed, because the portal's host is on the public internet, and
then the sign-in would be posted from an address the router had never heard of. So
`AndroidNetwork.open` refuses rather than falling back.

### Why it holds the CPU awake

A foreground service keeps the process alive; it does not stop the phone from
sleeping. In Doze a thread's timer can be held back for many minutes, and a session
that lapsed in the meantime stays lapsed until somebody unlocks the phone. That is
the whole job, so the watcher takes a partial wake lock and hands it back when it
stops.

It costs battery. Expect a few percent an hour, which is why **Pause** is a button
and not a menu item, and why the screen asks whether to let the app ignore battery
optimisation.

### Why it asks for cleartext traffic

`android:usesCleartextTraffic="true"`, deliberately. This hotspot's whole protocol
is plain HTTP: the router's login page, the challenge posted back, and the portal
in the walled garden. Android 9 and later refuse cleartext by default, and with that
default the app could not sign in anywhere.

## The screen

- the state at the top, with the countdown moving as you read it
- which network, which router, whether the internet is actually up
- **Start** / **Pause**, and **Sign in now**
- **Settings**: hotspot name, portal address, session length, fallback network, an
  account, and start-on-boot. **Find portal** reads the router's login page and puts
  the best candidate it finds in the field; it never saves one by itself
- a **Log** tab with the same running commentary the Linux window shows

Closing the screen does not stop the watcher, and the notification is where you
stop it. There used to be a third action on the Linux window, Log out; it is not
here for the same reason it is not there: this router stops answering `/login` the
moment a session is ended, so the button could only strand the phone.

## The two things that will bite

**Android will not let an app join a network**, and has not since API 29. So
`fallback_wifi_name` names a network rather than promising to switch to it: the
safety net notices that the internet has been gone for fifteen seconds, says so in
the log and in a notification, and stops. It cannot put the phone on your other
hotspot. The phone's own auto-reconnect does that, usually without being asked.

**Private DNS has to be off while this hotspot is captive.** A phone set to a
Private DNS provider hostname (Settings → Network → Private DNS) resolves names over
TLS, and the walled garden does not let that port through — so *nothing* resolves,
the portal's hostname included, and the sign-in stops at step 2 with "that name does
not resolve". This is not the app being wrong: `ping v1.awingconnect.vn` from the
phone fails the same way at the same moment, and so does the browser. The app says
so in the log. Set Private DNS to Off (or to Automatic) and the same sign-in
completes in a second.

There is a second, quieter way to lose a sign-in: a phone that has been asleep a
long time. Allow the app to ignore battery optimisation and it does not happen.

## Settings

The same keys, the same JSON, as the macOS, Windows and Linux builds:

| Key | What it is | Default |
|---|---|---|
| `wifi_name` | The network to watch for | empty |
| `fallback_wifi_name` | The safety net network. Empty turns the net off | empty |
| `username` / `password` | An account to sign in with straight at the router | empty |
| `gateway_ip` | The router. Empty means ask this phone | empty |
| `interval` | How many minutes a session lasts | `15` |
| `check_interval_secs` | How often to look at the connection | `2` |
| `portal_fallback_url` | Portal address, for when the router names none | empty |
| `destination_url` | Where the router sends you after sign-in | Apple's check page |
| `logout_url` | An explicit logout address, if there is one | empty |
| `auto_connect` | Written and carried, unused: Android cannot join | `true` |
| `auto_start` | Start watching when the phone reboots | `false` |
| `check_wifi_every_time` | Check the network every pass | `false` |

They live as one JSON string in the app's preferences rather than in a file, so the
two platforms are not literally the same path — but the bytes are the same, so a
`settings.json` can be moved between them by copying its contents. A key this build
does not model is carried through untouched, exactly as on the other platforms.

The log is `files/activity.log` in the app's own storage, capped at 512 KiB.

### This hotspot: Awing Connect

Verified on a phone on `INET - Free WiFi`, 2026-11-08: the router at
`192.168.200.1` issues a CHAP challenge, the portal at `http://v1.awingconnect.vn`
issues `inet_15m-15m`, and the router accepts it. The portal's address has to be set
by hand, because the router's splash names no portal at all. A first run fills the
form with these two values and says nothing is applied until Save.

## Tests

```bash
./check.sh       # 105 checks on a plain JVM
```

The engine under `app/java/.../engine` imports nothing from `android.*`, on purpose.
The sign-in is a function of bytes and strings — the CHAP hash, the escape decoding,
the HTML scraper, the status page reader, the URLs the sequence builds — so it is
checked the way the Linux build checks its own engine, and the golden values come
from that Python engine. A disagreement means one of the two ports has drifted.

What is *not* covered by those checks: anything that touches the radio, the service
or the screen. Those were checked by hand on one phone, and the log of a real
sign-in is the evidence.

## Licence

Apache 2.0, the same as the rest of this repository. See `../LICENSE`.
