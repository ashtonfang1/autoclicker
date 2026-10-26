# macOS autoclicker

A small macOS app for repeating mouse clicks, pressing keyboard keys, and recording and playing back mouse and keyboard input. The same custom key starts and stops the active action.

## Install

Install dependencies in Terminal:

pip install pyautogui pynput rubicon-objc py2app

Build the macOS app with:

./build.sh

If the valid `LocalDevSign` code-signing identity is in your Keychain, the
build script selects it automatically. You can explicitly select another
identity:

CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build.sh

The build fails rather than silently falling back to ad-hoc signing when no
valid `LocalDevSign` identity is found. To explicitly request an ad-hoc build,
set `CODESIGN_IDENTITY=-`; macOS permissions may need to be granted again after
rebuilds. A self-signed identity does not guarantee that macOS will preserve
TCC permissions; verify the resulting signature and test the installed build.

The app bundle is created at `dist/macOS autoclicker.app`. For a stable
installed copy, move it to `/Applications/macOS autoclicker.app`; launching
from `dist/` is allowed for local testing, but ad-hoc rebuilds may not retain
their macOS permissions. To leave the app's icon in the Dock after quitting,
open the app's Dock menu and choose Options > Keep in Dock. Permission state
is checked once at startup and again when you click Start/Stop, a key-capture
control, or when the app regains focus; there is no continuous TCC polling.
After changing a switch in System Settings, return to the app to refresh the
displayed status.

## Required macOS permissions

The app uses two macOS permissions:

- Accessibility lets the app perform mouse clicks and keyboard presses. pynput also requires it to listen for key and mouse events. Enable macOS autoclicker under System Settings > Privacy & Security > Accessibility.
- Input Monitoring lets the app listen for the global start/stop key, capture keys from the + and custom-key buttons, and record input. Enable macOS autoclicker under System Settings > Privacy & Security > Input Monitoring.

Enable the app's switches in System Settings > Privacy & Security >
Accessibility and Input Monitoring; the app cannot grant or toggle a
permission on your behalf. Use the Controls tab's Request Input Monitoring
button to call macOS's explicit listen-event access request and open the
Input Monitoring pane. macOS controls whether it displays a prompt or lists
the app; if it is not listed, use + to add
`/Applications/macOS autoclicker.app`. After changing permission, quit and
reopen the app before testing keyboard capture.
The app uses the fixed bundle identifier `com.ashton.autoclicker`. Use the same
trusted signing identity for each release build to preserve the macOS app
identity. `build.sh` signs embedded libraries and frameworks without
overriding their identifiers, signs the app bundle with its fixed identifier,
and verifies the resulting signature. Click Guide in the Controls tab to open
this guide.

`NSAppleEventsUsageDescription` is not an Accessibility or Input Monitoring
purpose string; it is for apps that send Apple Events to control other apps.

The app writes permission state changes, permission-blocked actions,
start/stop and playback actions, and runtime failures to the rotating log
`~/Library/Logs/macOS autoclicker.log` (1 MB per file, three backups). It also
sends INFO-level diagnostics to stderr when launched from Terminal. Permission
checks report Accessibility and Input Monitoring separately. Clicking Add a
key or the custom start/stop-key control refreshes both checks before starting
capture; key capture requires both permissions.
Recording JSON and ordering data are stored in
`~/Library/Application Support/macOS autoclicker/Recordings`; logs and
recording data are not written inside the app bundle.

## Permission troubleshooting

This project currently builds with **py2app**, as configured in `setup.py` and
`build.sh`; it does not use PyInstaller. The app checks Accessibility using
the read-only `AXIsProcessTrusted()` API. Manage Accessibility and Input
Monitoring grants in System Settings; the app no longer includes dedicated
permission-request buttons. Its action-blocked dialogs can offer to open the
relevant privacy pane. These checks do not turn a System Settings switch off.
The Input Monitoring request API is called only when the user clicks its
dedicated button, never on startup or from listeners/polling. Accessibility
does not require a camera usage string, a special Accessibility entitlement,
or an `NSCameraUsageDescription` entry.

The bundle ID is `com.ashton.autoclicker`. A fixed bundle ID alone does not
preserve the TCC identity: updates should be signed with the same valid
code-signing identity and installed at the same
`/Applications/macOS autoclicker.app` path. The current local build is signed
with the self-signed `LocalDevSign` identity. It has no Developer ID Team ID,
and a self-signed identity does not guarantee macOS will preserve TCC grants.
Check the installed build with:

```sh
codesign -dv --verbose=2 "/Applications/macOS autoclicker.app" 2>&1
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  "/Applications/macOS autoclicker.app/Contents/Info.plist"
```

`build.sh` defaults `CODESIGN_IDENTITY` to `LocalDevSign` and stops with an
error if it is not a valid identity. Set
`CODESIGN_IDENTITY=-` to explicitly opt into ad-hoc signing. For a targeted
retest, first build and install the exact bundle you intend to keep at
the stable path. Then quit the app, optionally remove its row in System
Settings > Privacy & Security > Accessibility, reset this bundle ID's
Accessibility authorization, and relaunch:

```sh
./build.sh
# Replace the existing app in /Applications with the built bundle, then:
codesign --verify --deep --strict --verbose=2 \
  "/Applications/macOS autoclicker.app"
# This removes this app's Accessibility grant; grant it again after relaunch.
tccutil reset Accessibility com.ashton.autoclicker
open "/Applications/macOS autoclicker.app"
```

After relaunch, enable the app in the Accessibility list or follow the system
prompt if macOS presents one. Resetting a permission is a diagnostic step, not
a guarantee that macOS will accept an unstable or mismatched app signature.
Use the same signing identity for subsequent local builds. A Developer ID
Application identity is recommended for distributing an app to other users;
the self-signed local identity is only for development.

For a release build, set `CODESIGN_IDENTITY` to the same trusted Developer ID
Application identity on each build instead of relying on ad-hoc signing.

Do not reset TCC until the intended signed app is in `/Applications`. The
app's trust-check APIs do not turn a permission off, and a switch reverting by
itself does not prove a TCC database or CDHash conflict. First verify the app's
bundle ID and signature, then test with that same installed bundle.

Do not kill the system `tccd` daemon or recursively remove extended attributes
as a routine fix. Neither action repairs a mismatched signing identity;
removing extended attributes can also strip quarantine metadata without fixing
TCC. `build.sh` already removes the project's generated `build` and `dist`
outputs before rebuilding. After changing a permission in System Settings,
return to the app to trigger a trust refresh. If Add a key opens a privacy
pane even though Accessibility appears enabled, check Input Monitoring too:
keyboard event capture requires both permissions. The app rechecks both when
you click a key-capture control. If macOS still reports a permission as
missing, quit and reopen the installed app and verify that the current app
bundle—not an older copy—is enabled.

## Controls

- Set a start/stop key by clicking (click to add key) and pressing a key. Ctrl cannot be assigned and is reported beside the button. The button shows the key after it is set. Press Escape while setting it to clear the key. Without a configured key, the app cannot start an action.
- Enable Mouse to repeat left or right clicks, or hold the selected mouse button. Set a decimal delay and choose milliseconds, seconds, minutes, or hours; delays below 20 ms are corrected to 20 ms, enforcing a maximum configured rate of 50 clicks per second. The delay controls are disabled in Hold mode. The app disables PyAutoGUI's implicit post-action pause so the configured delay controls click cadence; PyAutoGUI's fail-safe remains enabled.
- Enable Keyboard and click + to add individual keys (including function keys such as F4). Compact key chips wrap onto additional rows to stay inside the Actions box; the Controls tab scrolls vertically when the extra rows need more space. Key capture passively listens; captured keystrokes continue to be received by macOS and other applications. Each displayed key is pressed and released once per delay. Click a key chip to remove it. Mouse and keyboard actions can run together. Mode is below the keyboard input and controls the mouse's Autoclick or Hold behavior.
- Check Record to arm recording, then choose All inputs, Only mouse, or Only keyboard. Press Ctrl + (custom key) to start recording, and press it again to stop. A centered dialog lets you edit the default `Recording` name and Save or Discard the result. Unchecking Record while recording also stops and opens the save dialog. Saved recordings are stored as JSON in the app's application support directory and appear in the Recordings tab.
- In Recordings, use Command-click to select or deselect individual recordings, Shift-click to select a range, and Command+A to select all. Drag recordings to reorder them; a blue line marks the insertion point, and the custom order is saved between launches. Delete selected removes all selected recordings. Play selected requires exactly one selected recording and plays only that recording. Choose a positive whole-number play count, or select Play forever until stopped; the count field is disabled in forever mode. Recording and order JSON files are written atomically; invalid files are preserved with a `.corrupt` suffix and shown in a non-blocking notice.
- In the recording-name dialog, Discard is on the left and the blue Save button is on the right. Saving closes the dialog before the recording list refreshes.
