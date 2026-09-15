import Combine
import SwiftUI

/// Compatibility surface for Foqos's own settings screen.
///
/// Foqos's `SettingsView` and `HomeView` were written against v1's
/// `ArgonBridge` and reference it twenty-five times. Rewriting those screens
/// would be surgery on views this rewrite is not otherwise touching, so the
/// name survives as a thin adapter over the new client.
///
/// **It stores nothing of its own.** Server address and token live in the same
/// `UserDefaults` keys `ArgonSettingsView` reads, so the two screens are two
/// windows onto one value rather than two places to configure the same thing —
/// which is the drift this rewrite exists to end.
///
/// There is no timer here and no reconciliation loop. `startMonitoring` and
/// `stopMonitoring` are deliberately no-ops: the evening block is a
/// `DeviceActivitySchedule` the system runs with the app closed, and the
/// polling loop they used to drive is the thing that never worked.
/// Not `@MainActor`-isolated as a whole: Foqos binds to these properties with
/// `$argonBridge.serverURL` from inside large view bodies, and isolation
/// checking on every such binding pushed those bodies past "unable to
/// type-check this expression in reasonable time". The async work hops to the
/// main actor explicitly instead.
final class ArgonBridge: ObservableObject {
  static let shared = ArgonBridge()

  static let publicURL = "https://argon.agentneon.dev"

  /// `@Published` backed by `UserDefaults`, not `@AppStorage`.
  ///
  /// `@AppStorage` is a `DynamicProperty` built for views; inside a class it
  /// does not observe, and `$bridge.serverURL` off one produces a type the
  /// compiler cannot check in reasonable time — which is how a Foqos view that
  /// had compiled for months suddenly could not.
  @Published var serverURL: String { didSet { write(serverURL, "argon.base") } }
  @Published var apiToken: String { didSet { write(apiToken, "argon.token") } }
  @Published var profileName: String { didSet { write(profileName, "argon.profileName") } }
  @Published var desiredMode: String { didSet { write(desiredMode, "argon.desiredMode") } }

  @Published var connectionState = "Not connected"
  @Published var lastError: String?

  private init() {
    let defaults = UserDefaults.standard
    // The same keys ArgonSettingsView reads, so the two screens are two
    // windows onto one value rather than two places to set the same thing.
    serverURL = defaults.string(forKey: "argon.base") ?? "http://192.168.68.72:3997"
    apiToken = defaults.string(forKey: "argon.token") ?? ""
    profileName = defaults.string(forKey: "argon.profileName") ?? "Argon Lockdown"
    desiredMode = defaults.string(forKey: "argon.desiredMode") ?? "normal"
  }

  private func write(_ value: String, _ key: String) {
    UserDefaults.standard.set(value, forKey: key)
  }

  @MainActor private var store: ArgonStore { ArgonAppDelegate.shared.store }
  @MainActor private var client: ArgonClient { ArgonAppDelegate.shared.client }

  var isConfigured: Bool { !serverURL.isEmpty && !apiToken.isEmpty }
  var activeAddress: String { serverURL }
  var deviceToken: String { UserDefaults.standard.string(forKey: "argon.deviceToken") ?? "" }

  /// Re-point the client at whatever the fields now say, and prove it works.
  func connect() {
    Task { @MainActor in
      connectionState = "Connecting…"
      await ArgonAppDelegate.shared.reconfigure()
      await store.refresh()
      connectionState = store.connection.isLive ? "Connected" : "Not connected"
      lastError = store.failure
    }
  }

  /// Ask the server for a focus mode. Reported, not enforced here: the block
  /// itself is applied on-device so a dead server cannot strand him.
  @MainActor
  func setFocusMode(_ mode: String, allowanceMinutes: Int? = nil,
                    perHours: Int? = nil) async {
    desiredMode = mode
    var payload: [String: Any] = ["mode": mode]
    if let allowanceMinutes { payload["allowance_minutes"] = allowanceMinutes }
    if let perHours { payload["per_hours"] = perHours }
    do {
      try await client.report(payload)
      lastError = nil
    } catch {
      lastError = error.localizedDescription
    }
  }

  func reportEmergencyOverride(minutes: Int) {
    Task { @MainActor in try? await client.report(["override_minutes": minutes]) }
  }

  /// No-ops, kept so Foqos's call sites compile.
  ///
  /// v1 reconciled on a `Timer.scheduledTimer` here, which iOS suspends the
  /// moment the app backgrounds — so anything the server published only
  /// applied while he happened to be looking at the screen. Refreshing is now
  /// driven by `scenePhase`, a silent push, and `BGAppRefreshTask`.
  func startMonitoring() {}
  func stopMonitoring() {}

  func requestRemoteNotifications() {
    Task { @MainActor in await ArgonAppDelegate.shared.push.requestAuthorisation() }
  }
}
