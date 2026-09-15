// swift-tools-version: 5.9
import PackageDescription

// The sync layer is pure Foundation, so it builds and tests on the Mac without
// a simulator. The UI and the iOS-only frameworks are excluded — they are
// typechecked against the iOS SDK by check.sh instead.
let package = Package(
  name: "ArgonSync",
  platforms: [.macOS(.v14)],
  targets: [
    .target(
      name: "ArgonSync",
      path: "Argon",
      exclude: [
        "ArgonAppDelegate.swift", "ArgonPush.swift", "ArgonRoutineActivity.swift",
        "ArgonRootView.swift", "ArgonTodayView.swift", "ArgonChatView.swift",
        "ArgonSettingsView.swift",
      ]
    ),
    .testTarget(name: "ArgonSyncTests", dependencies: ["ArgonSync"], path: "Tests/ArgonSyncTests"),
  ]
)
