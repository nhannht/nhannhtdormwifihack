#!/bin/bash
# Builds a signed, notarized disk image of the menu bar app.
#
# Notarizing is what stops macOS telling whoever downloads this that it cannot
# be checked for malware. Apple only notarizes a build signed with a Developer
# ID, so a release cannot reuse the ad-hoc signature build-app.sh makes for this
# machine.
#
# Needs, on the machine running it:
#   - a "Developer ID Application" certificate in the keychain
#   - a notarytool credential profile, made once with:
#       xcrun notarytool store-credentials <profile> --apple-id <id> \
#           --team-id <team> --password <app-specific-password>
set -euo pipefail

cd "$(dirname "$0")"

NAME="Dorm WiFi"
VERSION="${1:-1.0.0}"
NOTARY_PROFILE="${NOTARY_PROFILE:-notarytool}"

# The one certificate Apple will notarize a build signed with. Picked from the
# keychain rather than written down, so this works on any machine of the
# developer's without editing.
SIGN_IDENTITY="${SIGN_IDENTITY:-$(security find-identity -v -p codesigning \
    | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)}"

if [ -z "$SIGN_IDENTITY" ]; then
    echo "No Developer ID Application certificate in the keychain." >&2
    echo "Apple will not notarize an ad-hoc signed build, so this cannot go on." >&2
    exit 1
fi

DIST="dist"
STAGE="$DIST/stage"
DMG="$DIST/DormWiFi-$VERSION.dmg"

echo "Signing as: $SIGN_IDENTITY"

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"

SIGN_IDENTITY="$SIGN_IDENTITY" ./build-app.sh "$STAGE"

# The app is notarized and stapled before it goes in the image, as well as the
# image itself being notarized and stapled afterwards. Both are needed. The
# image's ticket does not travel with the app when someone drags it out, so an
# app carrying no ticket of its own has to reach Apple the first time it is
# opened, and shows the "cannot be verified" warning to anyone offline.
#
# Only a zip can be submitted, not a bundle, so the app is zipped to be sent and
# the ticket is stapled to the original bundle afterwards.
echo "Submitting the app to Apple. This usually takes a few minutes."
ditto -c -k --keepParent "$STAGE/$NAME.app" "$DIST/app.zip"
xcrun notarytool submit "$DIST/app.zip" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$STAGE/$NAME.app"
rm -f "$DIST/app.zip"

# Somewhere to drop the app. Without it the disk image is just a folder and the
# reader has to know where the app is supposed to go.
ln -s /Applications "$STAGE/Applications"

hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG"

# The disk image is signed as well as the app inside it. Apple staples the
# notarization ticket to the image, and an unsigned image cannot hold one.
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"

echo "Submitting the disk image to Apple."
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

# Stapling writes the ticket into the image, so a machine with no internet can
# still check it. Without this the check needs Apple to be reachable.
xcrun stapler staple "$DMG"

echo
echo "=== checks ==="
xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature -v "$DMG"

# The check that matters: the app as someone receives it, with no network.
MOUNT=$(hdiutil attach -nobrowse -readonly "$DMG" | grep -o '/Volumes/.*' | head -1)
xcrun stapler validate "$MOUNT/$NAME.app"
spctl --assess --type execute -v "$MOUNT/$NAME.app"
hdiutil detach "$MOUNT" -quiet

echo
echo "Built $DMG"
