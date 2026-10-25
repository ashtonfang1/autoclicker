# macOS autoclicker

A small macOS app for repeating mouse clicks, pressing keyboard keys, and recording and playing back mouse and keyboard input. The same custom key starts and stops the active action.

## Install

Install dependencies in Terminal:

pip install pyautogui pynput rubicon-objc py2app

Build the macOS app with:

./build.sh

For updates that should preserve macOS privacy permissions, sign each build
with the same trusted Developer ID Application identity:

CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build.sh

Without a stable trusted signing identity, py2app produces an ad-hoc signature
whose code hash changes on rebuild; macOS may treat the rebuilt app as a new
permission client even though its bundle identifier is fixed.

The app bundle is created at `dist/macOS autoclicker.app`. Copy it to
`/Applications/macOS autoclicker.app` and launch that copy so macOS permissions
stay associated with the stable app location. To leave its icon in the Dock
after quitting, open the app's Dock menu and choose Options > Keep in Dock.

## Required macOS permissions

The app uses two macOS permissions:

- Accessibility lets the app perform mouse clicks and keyboard presses. pynput also requires it to listen for key and mouse events. Enable macOS autoclicker under System Settings > Privacy & Security > Accessibility.
- Input Monitoring lets the app listen for the global start/stop key, capture keys from the + and custom-key buttons, and record input. Enable macOS autoclicker under System Settings > Privacy & Security > Input Monitoring.

The permission buttons ask macOS for the relevant permission and open the
matching System Settings privacy pane. macOS requires you to enable the app's
switch there; the app cannot grant or toggle a permission on your behalf. If
the app is not listed, use + to add `/Applications/macOS autoclicker.app`.
Grant both permissions for all features to work, then quit and reopen the app.
The app keeps both permission buttons visible in the Required: row and shows a
green checkbox beside each permission macOS confirms. The checks refresh
automatically. If a check stays empty after enabling a permission, remove the
older entry, add the current app from `/Applications` again, and reopen it.
The app uses the fixed bundle identifier `com.ashton.autoclicker`. Use the same
trusted signing identity for each release build to preserve the macOS app
identity. Click Guide below the permission row to open this guide.

`NSAppleEventsUsageDescription` is not an Accessibility or Input Monitoring
purpose string; it is for apps that send Apple Events to control other apps.

The app writes startup permission states and runtime failures to
`~/Library/Logs/macOS autoclicker.log`.

## Controls

- Set a start/stop key by clicking (click to add key) and pressing a key. The button shows the key after it is set. Press Escape while setting it to clear the key. Without a configured key, the app cannot start an action.
- Enable Mouse to repeat left or right clicks, or hold the selected mouse button. Set a decimal delay and choose milliseconds, seconds, minutes, or hours; the minimum delay is 1 ms. The delay controls are disabled in Hold mode.
- Enable Keyboard and click + to add individual keys (including function keys such as F4). Each displayed key is pressed and released once per delay. Click a key chip to remove it. Mouse and keyboard actions can run together. Mode is below the keyboard input and controls the mouse's Autoclick or Hold behavior.
- Check Record to arm recording, then choose All inputs, Only mouse, or Only keyboard. Press Ctrl + (custom key) to start recording, and press it again to stop. A centered dialog lets you edit the default `Recording` name and Save or Discard the result. Unchecking Record while recording also stops and opens the save dialog. Saved recordings are stored as JSON in the app's application support directory and appear in the Recordings tab.
