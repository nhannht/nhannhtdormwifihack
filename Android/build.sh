#!/bin/bash
#
# Builds the phone app into .build/dormwifi.apk.
#
# No Gradle. The Android build tools do the whole job in five steps — compile the
# resources, link them against the framework, compile the Java, turn the class
# files into dex, then sign — and each of them is one command. A build system
# between those commands and this file would add a daemon, a dependency resolver
# and a local.properties, and the app has no third-party dependency to resolve.
#
# It needs two toolchains and nothing else:
#   * an Android SDK with one platform and one set of build tools
#   * a JDK (javac, keytool)
#
# Set ANDROID_SDK_ROOT and JAVA_HOME to point at them; otherwise this looks in the
# places they usually live, including ~/Android/Sdk and ~/Android/jdk.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

MIN_SDK=26
TARGET_SDK=34
PACKAGE=vn.io.nhannht.dormwifihack

say() { printf '%s\n' "$*"; }
die() { printf 'build: %s\n' "$*" >&2; exit 1; }

# MARK: - Finding the toolchains

newest_directory() {
    # Version-sorted, so android-9 does not beat android-34.
    ls -d "$1"/*/ 2>/dev/null | sed 's:/*$::' | sort -V | tail -1
}

find_sdk() {
    local candidate
    for candidate in "${ANDROID_SDK_ROOT:-}" "${ANDROID_HOME:-}" \
                     "$HOME/Android/Sdk" /usr/lib/android-sdk /opt/android-sdk \
                     "$HOME/Library/Android/sdk"; do
        [ -n "$candidate" ] || continue
        if [ -d "$candidate/platforms" ] && [ -d "$candidate/build-tools" ]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

find_javac() {
    if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/javac" ]; then
        printf '%s' "$JAVA_HOME/bin/javac"
        return 0
    fi
    if command -v javac >/dev/null 2>&1; then
        command -v javac
        return 0
    fi
    if [ -x "$HOME/Android/jdk/bin/javac" ]; then
        printf '%s' "$HOME/Android/jdk/bin/javac"
        return 0
    fi
    return 1
}

SDK=$(find_sdk) || die "No Android SDK found. Set ANDROID_SDK_ROOT, or install one at ~/Android/Sdk.
       It needs platforms/android-34 and build-tools/34.0.0 under it. The two zips are
       platform-34-ext7_r03.zip and build-tools_r34-linux.zip from
       https://dl.google.com/android/repository/ ; unzip them into those names."

PLATFORM=$(newest_directory "$SDK/platforms") || die "No platform under $SDK/platforms"
BUILD_TOOLS=$(newest_directory "$SDK/build-tools") || die "No build tools under $SDK/build-tools"
ANDROID_JAR="$PLATFORM/android.jar"
[ -f "$ANDROID_JAR" ] || die "No android.jar under $PLATFORM"

for tool in aapt2 d8 zipalign apksigner; do
    [ -x "$BUILD_TOOLS/$tool" ] || die "$tool is missing from $BUILD_TOOLS"
done
command -v zip >/dev/null 2>&1 || die "zip is needed to put classes.dex into the apk: sudo apt install zip"

JAVAC=$(find_javac) || die "No javac found. Set JAVA_HOME, or install a JDK."
JAVA_HOME_ACTUAL=$(cd "$(dirname "$JAVAC")/.." && pwd)
KEYTOOL="$JAVA_HOME_ACTUAL/bin/keytool"
[ -x "$KEYTOOL" ] || die "No keytool next to $JAVAC, and one is needed to sign the apk"

say "sdk:         $SDK"
say "platform:    $(basename "$PLATFORM")"
say "build tools: $(basename "$BUILD_TOOLS")"
say "javac:       $JAVAC"
say ""

# MARK: - Clean

rm -rf .build/gen .build/classes .build/dex .build/res.zip .build/base.apk
mkdir -p .build/gen .build/classes .build/dex

# MARK: - Resources

say "resources"
"$BUILD_TOOLS/aapt2" compile --dir app/res -o .build/res.zip

"$BUILD_TOOLS/aapt2" link \
    -o .build/base.apk \
    -I "$ANDROID_JAR" \
    --manifest app/AndroidManifest.xml \
    --java .build/gen \
    --min-sdk-version "$MIN_SDK" \
    --target-sdk-version "$TARGET_SDK" \
    --version-code "$(date +%Y%m%d)" \
    --version-name "$(date +%Y.%m.%d)" \
    .build/res.zip

# MARK: - Java

say "classes"
# `--release 11` rather than the JDK's own level: d8 desugars 11 down to the 26 API
# this app supports, and a class file newer than the target is one more thing that
# can go wrong on a phone for no benefit. It is not 8, because javac emits a
# `MethodParameters` entry with no name for an inner class's outer-instance
# parameter, and that is only tolerated from build-tools 35 onwards.
"$JAVAC" --release 11 \
    -classpath "$ANDROID_JAR" \
    -d .build/classes \
    $(find app/java .build/gen -name '*.java') \
    || die "javac failed"

# MARK: - Dex

say "dex"
"$BUILD_TOOLS/d8" \
    --min-api "$MIN_SDK" \
    --lib "$ANDROID_JAR" \
    --output .build/dex \
    $(find .build/classes -name '*.class') \
    || die "d8 failed. If the error mentions MethodParameters or a null name, the
       build tools are too old: build-tools 34's d8 crashes on what javac 21 emits.
       Install build-tools 35 or newer."

cp .build/base.apk .build/app-unsigned.apk
(cd .build/dex && zip -q ../app-unsigned.apk classes.dex)

"$BUILD_TOOLS/zipalign" -p -f 4 .build/app-unsigned.apk .build/app-aligned.apk

# MARK: - Signature

# One key, made once and kept out of the repository. Android only requires that
# updates to an installed app carry the same signature as the one before, so this
# does not have to be a real release key to install and update the app over adb.
KEYSTORE=.build/dormwifi.jks
if [ ! -f "$KEYSTORE" ]; then
    say "signing key (first build only)"
    "$KEYTOOL" -genkeypair -v \
        -keystore "$KEYSTORE" \
        -storepass android -keypass android \
        -alias dormwifi \
        -keyalg RSA -keysize 2048 -validity 10950 \
        -dname "CN=Dorm WiFi, O=nhannht, C=VN" >/dev/null 2>&1 \
        || die "keytool could not make a signing key"
fi

say "signing"
"$BUILD_TOOLS/apksigner" sign \
    --ks "$KEYSTORE" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --out .build/dormwifi.apk \
    .build/app-aligned.apk

"$BUILD_TOOLS/apksigner" verify .build/dormwifi.apk >/dev/null \
    || die "the signed apk does not verify"

say ""
say "built .build/dormwifi.apk ($(du -h .build/dormwifi.apk | cut -f1))"
say "install it with ./install.sh"
