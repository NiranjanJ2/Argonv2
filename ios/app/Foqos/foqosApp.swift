//
//  foqosApp.swift
//  foqos
//
//  Created by Ali Waseem on 2024-10-06.
//

import AppIntents
import BackgroundTasks
import SwiftData
import SwiftUI

// Not private: ArgonAppDelegate reconciles the server's lock against SwiftData
// on background wakes, where there is no view and so no @Environment context.
@MainActor let container: ModelContainer = {
  do {
    let container = try ModelContainer(
      for: BlockedProfileSession.self,
      BlockedProfiles.self
    )
    // Temporary backfill for users upgrading from the legacy NFC/QR fields.
    // Remove this in the next app version once the installed base has migrated.
    try PhysicalUnblockMigrationHelper.migrateOldPhysicalUnblockFields(
      in: container.mainContext
    )
    return container
  } catch {
    fatalError("Couldn't create ModelContainer: \(error)")
  }
}()

@main
struct foqosApp: App {
  @UIApplicationDelegateAdaptor(ArgonAppDelegate.self) private var appDelegate

  @StateObject private var requestAuthorizer = RequestAuthorizer()
  @StateObject private var donationManager = TipManager()
  @StateObject private var navigationManager = NavigationManager()
  @StateObject private var nfcWriter = NFCWriter()
  @StateObject private var ratingManager = RatingManager()

  // Singletons for shared functionality
  @StateObject private var startegyManager = StrategyManager.shared
  @StateObject private var liveActivityManager = LiveActivityManager.shared
  @StateObject private var themeManager = ThemeManager.shared
  @StateObject private var alertsManager = AlertsManager.shared
  @StateObject private var argonBridge = ArgonBridge.shared

  init() {
    TimersUtil.registerBackgroundTasks()
    // ArgonReconciler is gone: it polled on a Timer that iOS suspends in
    // the background, so it only reconciled while the app was already on
    // screen. The evening block is a DeviceActivitySchedule now.

    let asyncDependency: @Sendable () async -> (ModelContainer) = {
      @MainActor in
      return container
    }
    AppDependencyManager.shared.add(
      key: "ModelContainer",
      dependency: asyncDependency
    )
  }

  var body: some Scene {
    WindowGroup {
      ArgonRootView(store: appDelegate.store)
        .preferredColorScheme(.dark)
        .tint(themeManager.themeColor)
        .onOpenURL { url in
          handleUniversalLink(url)
        }
        .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) {
          userActivity in
          guard let url = userActivity.webpageURL else {
            return
          }
          handleUniversalLink(url)

        }
        .environmentObject(requestAuthorizer)
        .environmentObject(donationManager)
        .environmentObject(alertsManager)
        .environmentObject(startegyManager)
        .environmentObject(navigationManager)
        .environmentObject(nfcWriter)
        .environmentObject(ratingManager)
        .environmentObject(liveActivityManager)
        .environmentObject(themeManager)
        .environmentObject(argonBridge)
    }
    .modelContainer(container)
  }

  private func handleUniversalLink(_ url: URL) {
    navigationManager.handleLink(url)
  }
}
