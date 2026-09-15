import SwiftUI
import UIKit

/// Wires Argon into the Foqos app. Everything Argon needs is created here and
/// nowhere else, so removing Argon is deleting this file and one line in the
/// app's `body`.
@MainActor
final class ArgonAppDelegate: NSObject, UIApplicationDelegate {
  static let shared = ArgonAppDelegate()

  private(set) lazy var client = ArgonClient(base: Self.baseURL, token: Self.token)
  private(set) lazy var store = ArgonStore(client: client)
  private(set) lazy var push = ArgonPush(client: client, store: store)

  /// Server address and token live in the app's settings bundle, not in source.
  private static var baseURL: URL {
    URL(string: UserDefaults.standard.string(forKey: "argon.base")
        ?? "http://agentneon.local:3995")!
  }

  private static var token: String {
    UserDefaults.standard.string(forKey: "argon.token") ?? ""
  }

  func reconfigure() async {
    await client.configure(base: Self.baseURL, token: Self.token)
    await store.refresh()
  }

  func application(_ application: UIApplication,
                   didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    Task {
      await push.requestAuthorisation()
      try? ArgonRoutine.begin()
      await store.refresh()
    }
    return true
  }

  func application(_ application: UIApplication,
                   didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
    push.registered(deviceToken: token)
  }

  func application(_ application: UIApplication,
                   didFailToRegisterForRemoteNotificationsWithError error: Error) {
    push.registrationFailed(error)
  }

  func application(_ application: UIApplication,
                   didReceiveRemoteNotification userInfo: [AnyHashable: Any]) async
    -> UIBackgroundFetchResult {
    await push.received(userInfo: userInfo)
  }
}
