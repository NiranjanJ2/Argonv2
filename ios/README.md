# The Argon layer

Drops into the existing Foqos project. Foqos keeps its own screens, its
profiles, and the whole FamilyControls/DeviceActivity blocking stack — none of
it is touched. These files are the Argon half.

## Adding it to Xcode

The project uses `PBXFileSystemSynchronizedRootGroup`, so dragging `Argon/`
into the app target picks the files up automatically.

**The `FoqosDeviceMonitor` extension does not work that way.** It takes an
explicit `membershipExceptions` list, so `ArgonRoutineActivity.swift` must be
added to it by hand or the build fails with "cannot find ArgonRoutine in scope".

Two Info.plist entries are needed:

- `UIBackgroundModes` → `remote-notification`, `fetch`
- `BGTaskSchedulerPermittedIdentifiers` → `com.niranjanj.argon.refresh`

Then one line wherever the root view is built:

```swift
ArgonRootView(store: ArgonAppDelegate.shared.store)
```

Server address and token go in Settings inside the app — never in source.

## Why it is shaped this way

v1's `ArgonBridge` had a method per endpoint, a 20-second polling timer, and
four calls of the form `_ = try? await perform(request)` whose result was
discarded. Tap Done, the server 500s, the row stays ticked and the task is
still open tomorrow — with no way for him to find out.

**There is no timer anywhere in this layer.** iOS suspends `Timer` the moment
the app backgrounds, so a timer-driven refresh only runs while he is already
looking at the screen. Three things wake the app instead: bringing it forward,
a silent push, and `BGAppRefreshTask`. Anything that must happen with the app
*closed* is a `DeviceActivitySchedule`, which the system runs for us.

**Every write is durable.** A tap changes the screen immediately and enqueues a
`PendingWrite` persisted to disk. Transient failures stay queued and retry;
a 4xx is dropped and surfaced, because asking again will not help. Nothing is
silently lost.

**The cache is drawn before any request.** Opening offline used to show an empty
board, which is indistinguishable from "nothing is due" — the worst lie this app
can tell. Now the last known state appears instantly, labelled with its age.

## Tests

The sync layer is pure Foundation, so it runs on the Mac:

```sh
cd ios && swift test
```

Ten tests drive the real client, store and outbox against a stubbed transport:
writes surviving relaunch, permanent versus transient failure, offline
fallback, message ordering, and a partial server payload degrading instead of
blanking the screen. `../check.sh` runs these plus an iOS-SDK typecheck of the
UI.
