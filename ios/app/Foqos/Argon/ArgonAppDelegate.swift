import BackgroundTasks
import SwiftUI
import UIKit

/// Wires Argon into the Foqos app. Everything Argon needs is created here and
/// nowhere else, so removing Argon is deleting these files and one line in the
/// app's `body`.
///
/// The evening block itself is not started here: `ArgonRoutineActivity` is a
/// Foqos `TimerActivity`, scheduled through the profile system, and it fires in
/// the monitor extension with the app closed. Nothing in this file needs to be
/// running for it to work.
///
/// Three things wake the app, none of them a timer:
/// - he brings it forward (`scenePhase` in `ArgonRootView`)
/// - a silent push says the server has something new
/// - `BGAppRefreshTask`, so the board is current when he next opens it
@MainActor
final class ArgonAppDelegate: NSObject, UIApplicationDelegate {
  static let shared = ArgonAppDelegate()

  /// Must match the `BGTaskSchedulerPermittedIdentifiers` entry in Info.plist.
  static let refreshTaskID = "com.niranjanj.argon.refresh"

  private(set) lazy var client = ArgonClient(base: Self.baseURL, token: Self.token)
  private(set) lazy var store = ArgonStore(client: client)
  private(set) lazy var push = ArgonPush(client: client, store: store)

  /// Server address and token live in settings, never in source.
  private static var baseURL: URL {
    URL(string: UserDefaults.standard.string(forKey: "argon.base") ?? "")
      ?? URL(string: "http://192.168.68.72:3997")!
  }

  private static var token: String {
    UserDefaults.standard.string(forKey: "argon.token") ?? ""
  }

  func reconfigure() async {
    await client.configure(base: Self.baseURL, token: Self.token)
  }

  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    BGTaskScheduler.shared.register(forTaskWithIdentifier: Self.refreshTaskID,
                                    using: nil) { task in
      guard let task = task as? BGAppRefreshTask else { return }
      Task { @MainActor in self.handle(task) }
    }
    Task {
      await push.requestAuthorisation()
      await store.refresh()
    }
    return true
  }

  func applicationDidEnterBackground(_ application: UIApplication) {
    scheduleRefresh()
  }

  /// Ask for a wake-up in fifteen minutes. iOS decides when it actually
  /// happens — this is a request, not a schedule, which is exactly why a
  /// `Timer` was never going to work here.
  func scheduleRefresh() {
    let request = BGAppRefreshTaskRequest(identifier: Self.refreshTaskID)
    request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
    try? BGTaskScheduler.shared.submit(request)
  }

  private func handle(_ task: BGAppRefreshTask) {
    scheduleRefresh()   // chain the next one before doing any work
    let work = Task {
      await store.refresh()
      task.setTaskCompleted(success: true)
    }
    task.expirationHandler = {
      work.cancel()
      task.setTaskCompleted(success: false)
    }
  }

  // MARK: push

  func application(_ application: UIApplication,
                   didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
    push.registered(deviceToken: token)
  }

  func application(_ application: UIApplication,
                   didFailToRegisterForRemoteNotificationsWithError error: Error) {
    push.registrationFailed(error)
  }

  func application(
    _ application: UIApplication,
    didReceiveRemoteNotification userInfo: [AnyHashable: Any]
  ) async -> UIBackgroundFetchResult {
    await push.received(userInfo: userInfo)
  }
}
