import Foundation
import UIKit
import UserNotifications

/// APNs registration and delivery.
///
/// One thing worth knowing before debugging this: an APNs auth key scoped to
/// Development at creation can never be widened. Production then fails on
/// *auth* before it looks at the device token and returns
/// `BadEnvironmentKeyInToken`, while sandbox rejects a production token with
/// `BadDeviceToken`. Together those look exactly like a stale registration when
/// the token was fine. If pushes stop, check the key's scope in the Apple
/// developer portal before regenerating anything here — `argon doctor` on the
/// server probes both environments and will tell you which side is wrong.
@MainActor
final class ArgonPush: NSObject, UNUserNotificationCenterDelegate {
  private let client: ArgonClient
  private let store: ArgonStore

  init(client: ArgonClient, store: ArgonStore) {
    self.client = client
    self.store = store
    super.init()
    UNUserNotificationCenter.current().delegate = self
  }

  func requestAuthorisation() async {
    let centre = UNUserNotificationCenter.current()
    guard let granted = try? await centre.requestAuthorization(options: [.alert, .sound, .badge]),
          granted else { return }
    UIApplication.shared.registerForRemoteNotifications()
  }

  /// Called from the app delegate with the raw token data.
  func registered(deviceToken: Data) {
    let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
    UserDefaults.standard.set(hex, forKey: "argon.deviceToken")
    UserDefaults.standard.set(false, forKey: Self.syncedKey)
    Task { await syncToken() }
  }

  private static let tokenKey = "argon.deviceToken"
  private static let syncedKey = "argon.deviceTokenSynced"

  /// Push the stored token to the server until it actually lands.
  ///
  /// iOS hands the token to the app once per launch. The old code posted it
  /// with `try?` and dropped the error, so one failed POST — server asleep,
  /// wrong token in Settings, phone on cellular off the LAN — meant the server
  /// held no device and every brief afterwards logged "no device registered
  /// for push" until the next cold launch. The failure was invisible from both
  /// ends. Now the token is only marked synced when the server takes it, and
  /// anything that wakes the app retries.
  func syncToken() async {
    guard !UserDefaults.standard.bool(forKey: Self.syncedKey),
          let hex = UserDefaults.standard.string(forKey: Self.tokenKey),
          !hex.isEmpty else { return }
    do {
      try await client.register(deviceToken: hex)
      UserDefaults.standard.set(true, forKey: Self.syncedKey)
    } catch {
      print("[argon] device registration deferred: \(error.localizedDescription)")
    }
  }

  func registrationFailed(_ error: Error) {
    // Logged, not surfaced: he can do nothing about it from the phone, and a
    // banner about push failing is exactly the noise this rewrite removes.
    print("[argon] push registration failed: \(error.localizedDescription)")
  }

  /// A silent push means "come and look" — never a payload to display. The
  /// server holds the truth; the phone fetches it. Refresh flushes the outbox
  /// first, so a push is also the moment a write stranded by a dead connection
  /// finally lands.
  func received(userInfo: [AnyHashable: Any]) async -> UIBackgroundFetchResult {
    await store.refresh()
    return store.connection.isLive ? .newData : .failed
  }

  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification
  ) async -> UNNotificationPresentationOptions {
    [.banner, .sound]
  }

  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse
  ) async {
    await store.refresh()
    await store.markRead()
  }
}
