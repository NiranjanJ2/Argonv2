// swift-tools-version: 5.9
import PackageDescription

// The sync layer is pure Foundation, so it builds and tests on the Mac without
// a simulator. It points at the files inside the app rather than a second copy
// of them: two directories of the same Swift is exactly the drift this rewrite
// exists to end. Sources are listed explicitly rather than excluded: a new file
// in Argon/ should not silently join this target and drag UIKit,
// DeviceActivity or FamilyControls in with it. The UI and the iOS-only
// frameworks are typechecked against the real SDK by ../check.sh instead.
let package = Package(
  name: "ArgonSync",
  platforms: [.macOS(.v14)],
  targets: [
    .target(
      name: "ArgonSync",
      path: "app/Foqos/Argon",
      // Everything in the directory that is NOT part of this target must be
      // listed, not merely omitted. SwiftPM tolerates `sources` alone, but
      // SourceKit then treats the remaining files as package members with no
      // module context and reports every symbol in them as missing —
      // "Cannot find type 'ArgonStore' in scope" on a file that compiles fine.
      exclude: [
        "ArgonAppDelegate.swift",
        "ArgonBridge.swift",
        "ArgonChatView.swift",
        "ArgonMetered.swift",
        "ArgonOverride.swift",
        "ArgonPalette.swift",
        "ArgonPush.swift",
        "ArgonRootView.swift",
        "ArgonRoutineActivity.swift",
        "ArgonSettingsView.swift",
        "ArgonStatusCard.swift",
        "ArgonTheme.swift",
        "ArgonTodayView.swift",
      ],
      sources: [
        "ArgonModels.swift",
        "ArgonClient.swift",
        "ArgonLog.swift",
        "ArgonStore.swift",
        "ArgonOutbox.swift",
        "ArgonCache.swift",
        "ArgonSnapshot.swift",
      ]
    ),
    .testTarget(name: "ArgonSyncTests", dependencies: ["ArgonSync"],
                path: "Tests/ArgonSyncTests"),
  ]
)
