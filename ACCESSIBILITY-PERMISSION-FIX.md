# Accessibility Permission Fix: Change Record

This document records the focused changes and diagnostics related to
Accessibility status and key capture in the macOS autoclicker. A code
requirement failure was observed, but the underlying TCC/signing cause is not
fully established. Line numbers below refer to the current source and build
script; later edits may move them.

## What was wrong

macOS owns the actual Accessibility authorization. The app can only query it;
it cannot grant access or infer a grant from the System Settings switch alone.
Keyboard capture also needs Input Monitoring, which is a separate permission
and is queried separately from Accessibility.

The initial symptom had two aspects: permission state could be stale after
returning from System Settings, and Add a key could redirect the user to a
privacy pane when the cached state said access was missing. Previous logs
showed both conditions at different times:

- At 2026-10-25 23:12:58, the app log reported Accessibility `True` and Input
  Monitoring `False`. This means Accessibility alone was insufficient for key
  capture.
- At 2026-10-25 23:26:12, the app log reported both permissions `False` at
  startup.
- At 2026-10-25 23:26:19, `tccd` logged `Failed to match existing code
  requirement` for `com.ashton.autoclicker` and Accessibility. It printed
  several candidate CDHashes, but that log does not establish exactly why
  requirement evaluation failed.

Therefore, seeing the Accessibility switch enabled is not proof that the
running app's Accessibility preflight succeeds, and it says nothing about
Input Monitoring. The latest captured app log did not confirm a successful
preflight for the current build. Do not describe the issue as definitively
fixed without testing the current app after granting both permissions.

## Change 1: Refresh permission state when the app regains focus

### `autoclicker.py:329`

Added a field to track the pending Tk callback:

```python
self._trust_refresh_after_id = None
```

This lets the app debounce repeated focus events instead of queueing multiple
trust checks during one return-to-app transition.

### `autoclicker.py:337`

Bound the root window's focus event to the refresh scheduler:

```python
self.root.bind("<FocusIn>", self._schedule_trust_state_refresh, add="+")
```

The `add="+"` option preserves other focus bindings. The callback is scheduled
when the app receives focus again, such as after switching back from System
Settings.

### `autoclicker.py:930-939`

Added the debounced scheduler and callback:

```python
def _schedule_trust_state_refresh(self, _=None):
    if self._trust_refresh_after_id is not None:
        self.root.after_cancel(self._trust_refresh_after_id)
    self._trust_refresh_after_id = self.root.after(
        150, self._refresh_trust_state_after_focus
    )

def _refresh_trust_state_after_focus(self):
    self._trust_refresh_after_id = None
    self._refresh_trust_state()
```

The short delay allows the focus transition to finish before querying
permission state. Clearing the callback ID before refreshing means the next
focus transition can schedule another refresh.

### Existing refresh path: `autoclicker.py:150`, `autoclicker.py:168`,
and `autoclicker.py:869+`

The focus callback reuses the app's existing refresh path; it does not create a
second permission mechanism:

- `is_ax_trusted()` at line 150 queries the read-only
  `AXIsProcessTrusted()` API.
- `is_input_monitoring_trusted()` at line 168 checks
  `CGPreflightListenEventAccess()`.
- `_refresh_trust_state()` at line 869 updates the cached values and starts or
  stops listeners based on both checks.

This is a one-shot refresh on a window event, not a timer or continuous TCC
polling loop. `_refresh_trust_state()` stores the trust values used to gate
actions and global listeners.

## Change 2: Refresh trust immediately when key capture is requested

### `autoclicker.py:768-775`

Add a key and custom start/stop-key capture both pass through
`_begin_key_capture()`. Before this fix, it checked the cached combined
permission value without refreshing it. If the focus event had not refreshed
the cache after a Settings change, the app could treat the previous missing
state as current and offer to open Settings again.

The handler now calls `_refresh_trust_state()` immediately before testing
`_has_listener_permissions()`. This is an explicit, user-triggered read-only
preflight, not continuous polling and not a prompt API call. The capture
listener starts only when both Accessibility and Input Monitoring preflights
are true.

The missing-permission dialog now states the separately detected values:

- If Accessibility is false, it reports whether Input Monitoring is granted
  and offers to open Accessibility settings.
- If Accessibility is true but Input Monitoring is false, it identifies
  Input Monitoring as the missing permission and offers that settings pane.

The user must confirm before the app opens either pane.

## Permission UI removal

The Controls tab's `Required:` row and its Request Accessibility and Request
Input Monitoring buttons were removed. The corresponding native request
helpers, button handlers, checkmark widgets, and permission-button signature
diagnostics were also removed. Users manage both switches directly in System
Settings. Action-blocked dialogs retain an optional, confirmed route to the
appropriate settings pane. The Guide remains available.

## Change 3: Make the build's signing order explicit

### `build.sh:29-53`

The previous signing block used a single deep-signing command with the app's
identifier:

```sh
codesign --force --deep --sign "$SIGNING_IDENTITY" \
    --identifier "com.ashton.autoclicker" "$APP_PATH"
```

The updated build signs embedded code first, without passing the app
identifier to nested code, then signs the launcher and outer app:

1. Lines 36-41 sign `.dylib` and `.so` files using their own identifiers.
2. Lines 43-44 sign embedded frameworks.
3. Lines 46-49 sign the py2app launcher when it exists.
4. Lines 51-53 sign the outer app with `com.ashton.autoclicker` and run strict
   deep verification.

This produces an explicit and verifiable signing sequence. It does not assert
that the former command alone caused the observed TCC failure; the log
evidence did not establish that conclusion.

## Recovery steps used

After rebuilding:

1. The updated `.app` was copied from `dist/macOS autoclicker.app` to
   `/Applications/macOS autoclicker.app`.
2. The installed app was checked with
   `codesign --verify --deep --strict`.
3. Its embedded `autoclicker.py` hash was compared with the worktree source.
4. The targeted command `tccutil reset Accessibility
   com.ashton.autoclicker` was run to remove the prior stored Accessibility
   approval. This resets authorization; it does not grant it. The current
   installed app must be enabled again in System Settings.
5. On returning focus to the app, the focus callback rechecks trust. As a
   second explicit refresh point, Add a key/custom-key capture now rechecks
   both permissions immediately before starting a listener.

At the time of the earlier build described above, the build was ad-hoc signed
when `CODESIGN_IDENTITY` was unset. Ad-hoc code hashes can change across
rebuilds, so macOS may require the user to grant Accessibility again after
installing a rebuilt copy. A stable trusted Developer ID identity is the
preferred way to keep an app's signing identity stable across releases.

## Local signing identity follow-up (2026-10-25)

Initially `LocalDevSign` was present as a certificate but not exposed as a
valid signing identity. On 2026-10-25 at 23:51, the Keychain reported one valid
code-signing identity named `LocalDevSign`. `build.sh` now defaults
`CODESIGN_IDENTITY` to `LocalDevSign`, resolves it to its valid identity hash,
and signs nested code before the outer app. It fails before cleaning
`build/`/`dist/` if that default identity cannot be found. Setting
`CODESIGN_IDENTITY=-` remains an explicit opt-in to ad-hoc signing.

The app's JSON recordings and ordering manifest are written under
`~/Library/Application Support/macOS autoclicker/Recordings`; the rotating log
is written under `~/Library/Logs`. Neither is written inside the `.app` bundle.

The global hotkey listener is no longer started from `AutoClicker.__init__`
or automatically when a focus refresh detects newly granted permissions. It
starts after an explicit key-capture action or Start/Stop-button action, only
when a custom key is configured and the required permissions are available.
Recording listeners still start from the user-triggered recording flow after
its permission preflight. This avoids starting global listeners simply because
the app launched or regained focus.

These checks do not establish that a changing CDHash is the reason TCC
revoked permission. A persistent signing identity can improve identity
stability, but it does not by itself prove that macOS will preserve a TCC
grant. `LocalDevSign` is self-signed, has no Developer ID Team ID, and should
not be treated as equivalent to a Developer ID Application identity.

### Signed build and install verification

The app was built with the valid `LocalDevSign` identity and the resulting
bundle passed `codesign --verify --deep --strict --verbose=2`. The installed
`/Applications/macOS autoclicker.app` was replaced with that build and passed
the same strict verification. Both bundles report bundle ID
`com.ashton.autoclicker` and `Authority=LocalDevSign`; the packaged
`autoclicker.py` SHA-256 matched between `dist/` and `/Applications/`.

After verifying the installed signature, the requested
`tccutil reset Accessibility com.ashton.autoclicker` completed successfully.
This reset removes the app's Accessibility grant; the user must enable
Accessibility for the newly installed app again in System Settings. The
permission was not granted automatically, and no post-reset runtime test has
yet been performed.

## Verification record

The change set was checked with:

- Python compilation (`python3 -m py_compile autoclicker.py setup.py`)
- Build-script syntax validation (`zsh -n build.sh`)
- Whitespace validation (`git diff --check`)
- Strict deep signature verification of the built and installed app
- SHA-256 comparison of `autoclicker.py` against the copy embedded in the app
- Confirmation that the installed signature authority is `LocalDevSign`
- Successful targeted Accessibility TCC reset after installation
- A focused callback test confirming focus schedules a refresh and the
  callback calls `_refresh_trust_state()`
- A focused capture-path test confirming permissions are refreshed before
  Add a key/custom-key capture starts or opens a missing-permission dialog
- A focused dialog test confirming each missing permission is identified and
  opens only its corresponding pane after explicit confirmation

The installed bundle was verified with bundle identifier
`com.ashton.autoclicker`; its embedded source hash matched the worktree at the
time of verification.

## Explicit Input Monitoring request action

The Controls tab now has a dedicated **Request Input Monitoring** button.
Its handler is the only code path that calls `CGRequestListenEventAccess()`;
startup, permission refreshes, listener threads, and action loops only perform
the passive `CGPreflightListenEventAccess()` check. The handler opens the
Input Monitoring settings pane and explains that a restart is needed after
changing the switch.

The CoreGraphics C APIs are called through `ctypes` with a boolean return
type. A failed request is logged and presented as not granted; the app does
not claim macOS must show a native prompt or guarantee the app appears in the
System Settings list. That behavior depends on macOS and still needs runtime
verification on the installed build. This update was rebuilt and installed
with `LocalDevSign`; strict deep signature verification, Python compilation,
and build-script syntax checks passed. No TCC reset was performed for this
Input Monitoring change.

## Related artifacts

- Full source comparison: [github-master-vs-local-worktree.diff.txt](./github-master-vs-local-worktree.diff.txt)
- Permission implementation: [autoclicker.py](./autoclicker.py)
- Build signing sequence: [build.sh](./build.sh)
