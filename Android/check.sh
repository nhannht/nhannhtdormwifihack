#!/bin/bash
#
# Runs the engine's checks on a plain JVM. No phone, no hotspot, no network.
#
# Everything under app/java/.../engine is free of android.* imports on purpose: the
# sign-in rules are a function of bytes and strings, so they can be checked the way
# the Linux build checks its own engine, and a phone is only ever needed for the
# parts that talk to the radio.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/javac" ]; then
    JDK="$JAVA_HOME/bin"
elif command -v javac >/dev/null 2>&1; then
    JDK=$(dirname "$(command -v javac)")
elif [ -x "$HOME/Android/jdk/bin/javac" ]; then
    JDK="$HOME/Android/jdk/bin"
else
    printf 'check: no javac found. Set JAVA_HOME, or install a JDK.\n' >&2
    exit 1
fi

rm -rf .build/check
mkdir -p .build/check

"$JDK/javac" --release 11 -Xlint:all -d .build/check \
    $(find app/java/vn/io/nhannht/dormwifihack/engine -name '*.java') \
    $(find test/java -name '*.java')

exec "$JDK/java" -cp .build/check vn.io.nhannht.dormwifihack.engine.EngineChecks
