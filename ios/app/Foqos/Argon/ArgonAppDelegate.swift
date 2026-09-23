import BackgroundTasks
import SwiftUI
import UIKit

/// Wires Argon into the Foqos app. Everything Argon needs is created here and
/// nowhere else, so removing Argon is deleting these files and one line in the
/// app's `body`.
///
/// The evening block itself is not started here: `ArgonRoutineActivity` is a
/// Foqos `TimerActivity`, armed by `ArgonRoutineScheduler` whenever fresh state
/// lands, and it fires in the monitor extension with the app closed. Nothing in
/// this file needs to be running for it to work.
///
/// Three things wake the app, none of them a timer:
/// - he brings it forward (`scenePhase` in `ArgonRootView`)
/// - a silent push says the server has something new
/// - `BGAppRefreshTask`, so the board is current when he next opens it
@MainActor
final class ArgonAppDelegate: NSObject, UIApplicationDelegate {
  private static weak var installed: ArgonAppDelegate?
  static var shared: ArgonAppDelegate {
    guard let installed else {
      preconditionFailure("ArgonAppDelegate used before SwiftUI installed it")
    }
    return installed
  }

  override init() {
    super.init()
    precondition(Self.installed == nil, "ArgonAppDelegate must have one instance")
    Self.installed = self
  }

  /// Must match the `BGTaskSchedulerPermittedIdentifiers` entry in Info.plist.
  static let refreshTaskID = "com.niranjanj.argon.refresh"

  private(set) lazy var client = ArgonClient(base: Self.baseURL, token: Self.token)
  private(set) lazy var store = ArgonStore(client: client)
  private(set) lazy var push = ArgonPush(client: client, store: store)

  /// Server address and token live in settings, never in source.
  private static var baseURL: URL {
    URL(string: ArgonBridge.resolvedBase())
      ?? URL(string: ArgonBridge.publicURL)!
  }

  private static var token: String {
    UserDefaults.standard.string(forKey: "argon.token") ?? ArgonBridge.defaultToken
  }

  /// Ask for notifications the first time he opens the conversation, not at
  /// launch. A permission prompt over an empty screen, before the app has
  /// shown him anything, is the reliable way to be told no — and it is the
  /// one permission Argon genuinely needs, since the brief arrives by push.
  func requestPushIfNeeded() async {
    guard !UserDefaults.standard.bool(forKey: "argon.askedForPush") else { return }
    UserDefaults.standard.set(true, forKey: "argon.askedForPush")
    await push.requestAuthorisation()
  }

  /// Apply the server's lock whenever fresh state lands.
  ///
  /// Hung off the notification the store already posts rather than called from
  /// each wake path: push, BGAppRefresh, scenePhase and a manual pull all end
  /// in a successful refresh, and wiring four call sites means forgetting the
  /// fifth. Reconcile compares before it acts, so the extra calls are free.
  private func observeStateForLock() {
    NotificationCenter.default.addObserver(
      forName: .argonStateApplied, object: nil, queue: .main
    ) { [weak self] _ in
      MainActor.assumeIsolated {
        guard let self else { return }
        ArgonLockReconciler.reconcile(self.store.state.lock,
                                      context: container.mainContext)
        // Tonight's block, armed with the system. Same hook for the same
        // reason: every wake path ends here, and the scheduler compares
        // before it acts. Skipped until setup has produced a profile — there
        // is nothing to block with, and arming against a profile the
        // extension cannot find fires into nothing.
        if let routine = self.store.state.routine,
           let profile = ArgonLockReconciler.profile(in: container.mainContext) {
          ArgonRoutineScheduler.apply(routine, profileId: profile.id)
        }
      }
    }
  }

  func reconfigure() async {
    await client.configure(base: Self.baseURL, token: Self.token)
    // Settings just changed the address or the token, which is exactly when a
    // registration that failed against the old one can finally succeed.
    await push.syncToken()
  }

  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    observeStateForLock()
    BGTaskScheduler.shared.register(forTaskWithIdentifier: Self.refreshTaskID,
                                    using: nil) { task in
      guard let task = task as? BGAppRefreshTask else { return }
      Task { @MainActor in self.handle(task) }
    }
    Task {
      await push.resumeAtLaunch()
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
      await push.syncToken()   // cheap no-op once the server has the token
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
