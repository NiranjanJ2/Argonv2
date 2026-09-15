# Installing into the Foqos project

Everything you need to do by hand, in order. Ten minutes, and two of the steps
are the ones that will bite if skipped.

## 1. Delete the v1 Argon layer — all 25 files

They define the same type names (`ArgonTask`, `ArgonMessage`, `ArgonRootView`,
`ArgonChatView`, `ArgonAppDelegate`, `ArgonRoutine`, `ArgonMessagesResponse`),
so leaving them in is a redeclaration error, not a warning.

```
Foqos/ArgonAppDelegate.swift
Foqos/Components/Dashboard/ArgonStatusCard.swift
Foqos/Intents/ArgonACIntents.swift
Foqos/Models/ArgonACModels.swift
Foqos/Models/ArgonMessageModels.swift
Foqos/Models/ArgonPlannerModels.swift
Foqos/Models/ArgonTaskModels.swift
Foqos/Models/ArgonWidgetSnapshot.swift
Foqos/Models/Timers/ArgonRoutineActivity.swift
Foqos/Utils/ArgonBridge.swift
Foqos/Utils/ArgonChatStore.swift
Foqos/Utils/ArgonDesign.swift
Foqos/Utils/ArgonMetered.swift
Foqos/Utils/ArgonModels.swift
Foqos/Utils/ArgonOverride.swift
Foqos/Utils/ArgonReconciler.swift
Foqos/Utils/ArgonRoutine.swift
Foqos/Views/ArgonACView.swift
Foqos/Views/ArgonChatView.swift
Foqos/Views/ArgonDashboardView.swift
Foqos/Views/ArgonPlannerView.swift
Foqos/Views/ArgonRichText.swift
Foqos/Views/ArgonRootView.swift
FoqosWidget/Views/ArgonTodayView.swift
FoqosWidget/Widgets/ArgonTodayWidget.swift

foqosTests/ArgonRoutineTests.swift
foqosTests/ArgonMarkdownTests.swift
foqosTests/ArgonModelsTests.swift
```

**`ArgonDesign.swift` is the one to be careful about.** It defines
`ArgonPalette`, which twenty-three *Foqos* files reference 164 times — v1's
Argon theme quietly became the whole app's theme. `ArgonPalette.swift` in this
folder reimplements that exact API on the new slate system, so those files keep
compiling untouched and pick up the overhaul for free. Delete `ArgonDesign.swift`
only once `ArgonPalette.swift` is in the target.

Some Foqos files also reference `ArgonMetered`, `ArgonOverride`,
`ArgonWidgetSnapshot`, `ArgonACUnit`, `ArgonPlannerItem` and friends. Those
belong to features v2 does not carry, so the call sites go too. Xcode will list
them; they are all in Argon-only views and the widget.

## 2. Add `ios/Argon/` to the app target

The project uses `PBXFileSystemSynchronizedRootGroup`, so dragging the folder in
is enough for the **app** target.

**`FoqosDeviceMonitor` is the exception.** It takes an explicit
`membershipExceptions` list, so `ArgonRoutineActivity.swift` must be added to it
by hand or the build fails with *"cannot find ArgonRoutine in scope"*.

## 3. Info.plist

```xml
<key>UIBackgroundModes</key>
<array>
  <string>remote-notification</string>
  <string>fetch</string>
</array>

<key>BGTaskSchedulerPermittedIdentifiers</key>
<array>
  <string>com.niranjanj.argon.refresh</string>
</array>
```

That identifier must match `ArgonAppDelegate.refreshTaskID` exactly. A mismatch
does not error — the task simply never fires, which is the worst kind of wrong.

## 4. Capabilities

Push Notifications is the only one to add; Background Modes and Family Controls
are already on the target from Foqos.

## 5. Mount it

One line wherever the root view is built (`foqosApp.swift`):

```swift
ArgonRootView(store: ArgonAppDelegate.shared.store)
```

and register the delegate:

```swift
@UIApplicationDelegateAdaptor(ArgonAppDelegate.self) var argon
```

## 6. Point it at the server

In the app's Settings tab:

- **Server** `http://192.168.68.72:3997`
- **Token** from `~/.argon2/config.json` → `api.token`

Nothing is hardcoded and no credential is in source. Tap **Test connection**;
it reports the real result rather than assuming.

## 7. App icon

Not included — supply a 1024×1024 PNG in `Assets.xcassets/AppIcon`.

## What you should see

Dark slate, glass cards over a field that warms as the evening goes on and goes
cold when Argon is off duty. The **live / cached Xm ago / offline** badge top
right is honest about where the numbers came from, and a **queued** pill appears
when writes are waiting on a connection.
