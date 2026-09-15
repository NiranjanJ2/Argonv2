# The app

The Foqos project now lives in this repo at `ios/app`, with the Argon layer in
`ios/app/Foqos/Argon`. **The integration is done** — this is a record of what
was changed and the short list of what still needs you.

`./check.sh` builds every target (app, widget, device monitor, both shield
extensions) and runs the sync tests. All green.

## What still needs you

**1. An app icon.** `Assets.xcassets/AppIcon` is empty. Drop a 1024×1024 PNG in.

**2. Signing, to run it on your phone.** The project signs
`com.niranjanj.argon` / team `DX3U2FC8X5`; builds here were `CODE_SIGNING_ALLOWED=NO`,
which verifies compilation but not provisioning.

**3. Server and token, in the app's Settings tab.** Address is
`http://192.168.68.72:3997`, token is in `~/.argon2/config.json` → `api.token`.
Nothing is hardcoded and no credential is in source. **This needs `ufw` open on
3997** or the phone cannot reach it off-tunnel.

**4. Tests need a simulator.** None are installed, so `xcodebuild test` could
not run — only the build. Install one if you want `foqosTests` exercised.

## What changed

**Deleted the 25 v1 `Argon*.swift` files.** Seven of them redeclared types the
new layer defines.

**Kept three of them, because they were never the assistant.** `ArgonMetered`
(a real usage budget enforced by Screen Time), `ArgonRoutineActivity` (the
evening block as a `DeviceActivitySchedule`, which fires with the app closed
and the server down) and `ArgonOverride` (the local emergency release, which
works in airplane mode). These belong to the blocking layer. I deleted them
first and was wrong to; v2 briefly shipped a standalone routine that did not
know about `TimerActivity`, `SharedData` or `AppBlockerUtil` — a worse copy of
something already correct.

**`ArgonPalette` is v1's design system re-pointed at the new theme.** It is
referenced 164 times across twenty-three *Foqos* files — v1's Argon theme had
quietly become the whole app's theme. Keeping the API and changing only the
values means those files compile untouched **and pick up the overhaul**, rather
than sitting beside it in a different blue.

**`ArgonBridge` survives as a thin adapter.** Foqos's `SettingsView` and
`HomeView` reference it twenty-five times. It stores nothing of its own: server
and token live in the same `UserDefaults` keys the new Settings tab reads, so
the two screens are two windows onto one value. `startMonitoring` and
`stopMonitoring` are deliberately no-ops — the polling loop they drove is the
thing that never worked.

**`HomeView.body` is split across four properties.** It carries ~27 chained
modifiers and was already at the type-checker's limit; any edit to its content
tipped it into *"unable to type-check this expression in reasonable time"*.
Splitting the chain is the fix, and it is why that file has a comment saying so.

**The widget is real again.** `ArgonSnapshot` is written to the
`group.com.niranjanj.argon` App Group by the store on every refresh and read by
the widget and the Live Activity. One writer, one reader — not a second client
that can disagree.

**Two Foqos call sites were rewired**: `StrategyManager` reports the emergency
override through the new client, and `LiveActivityManager` reads the snapshot
instead of v1's store.

**`BGTaskSchedulerPermittedIdentifiers` gained `com.niranjanj.argon.refresh`.**
Without it background refresh never fires, and it fails silently.

## Where things live

```
ios/
  Package.swift        tests the sync layer on macOS; points into the app,
                       not at a second copy of the same files
  Tests/               14 tests: outbox durability, optimistic writes,
                       transient vs permanent failure, offline fallback
  app/                 the Foqos project
    Foqos/Argon/       the Argon layer — the only place these files exist
```
