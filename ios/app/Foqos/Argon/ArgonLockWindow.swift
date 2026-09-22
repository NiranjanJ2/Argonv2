import DeviceActivity
import os
import FamilyControls
import Foundation
import ManagedSettings

/// A lock the system enforces, from a start time to an end time.
///
/// ## Why this is not just "apply a shield when the server says so"
///
/// The server can only publish state; the phone has to make it true. Driving
/// that from pushes alone meant a lock published at 19:28 to begin at 20:30
/// sent its only push at 19:28 — nothing reached the phone at 20:30, and if it
/// had, a silent push is rate-limited and not guaranteed to be delivered at
/// all. A lock that only works when a notification happens to land on time is
/// not a lock.
///
/// A `DeviceActivitySchedule` is evaluated by iOS. Once armed, the window
/// starts and ends whether or not the app is running, the server is reachable,
/// or any push was delivered — the same mechanism that already makes the
/// metered weekend budget survive the app being killed.
///
/// The app still reconciles on every wake. That covers the changes a schedule
/// cannot: he overrode it, the agent lifted it, the window moved.
enum ArgonLockWindow {
  static let activityName = DeviceActivityName("argon.lock")
  static let emptySelectionError = "the blocking profile has no apps, categories, or websites"

  enum ArmResult: Equatable {
    case armed
    case failed(String)

    var error: String? {
      if case .failed(let message) = self { return message }
      return nil
    }
  }

  static func armResult(hasSelection: Bool, schedulingError: String?) -> ArmResult {
    guard hasSelection else {
      return .failed(emptySelectionError)
    }
    return schedulingError.map(ArmResult.failed) ?? .armed
  }

  static func canApplyImmediately(after result: ArmResult) -> Bool {
    result.error != emptySelectionError
  }

  // os.Logger, not ArgonLog: this file is compiled into the DeviceActivity
  // extension too, and ArgonLog reaches the network client, which an extension
  // has no business holding.
  private static let log = Logger(subsystem: "com.niranjanj.argon", category: "lock")

  private static let store = ManagedSettingsStore()
  private static let suiteName = "group.com.niranjanj.argon"
  private static let windowKey = "argon.lock.window"

  /// Arm the window. Idempotent: re-arming the same one is a no-op, because
  /// restarting monitoring restarts the interval and would re-apply a shield
  /// he has already overridden.
  @discardableResult
  static func arm(from start: Date, until end: Date,
                  selection: FamilyActivitySelection) -> ArmResult {
    guard end > Date() else {
      disarm()
      return .failed("the lock window already ended")
    }
    let hasSelection = !selection.applicationTokens.isEmpty
      || !selection.categoryTokens.isEmpty
      || !selection.webDomainTokens.isEmpty
    let selectionResult = armResult(hasSelection: hasSelection, schedulingError: nil)
    guard selectionResult == .armed else { return selectionResult }
    if stored() == Window(from: start, until: end),
       DeviceActivityCenter().activities.contains(activityName) {
      return .armed
    }

    let cal = Calendar.current
    let parts: Set<Calendar.Component> = [.hour, .minute, .second]
    let schedule = DeviceActivitySchedule(
      intervalStart: cal.dateComponents(parts, from: max(start, Date().addingTimeInterval(1))),
      intervalEnd: cal.dateComponents(parts, from: end),
      repeats: false)

    // Written before monitoring starts: the interval can begin immediately and
    // the extension shields whatever it finds here.
    SharedData.setArgonLockSelection(selection)
    let center = DeviceActivityCenter()
    center.stopMonitoring([activityName])
    do {
      try center.startMonitoring(activityName, during: schedule)
      Window(from: start, until: end).save(to: suiteName, key: windowKey)
      log.info("armed \(start, privacy: .public)–\(end, privacy: .public)")
      return .armed
    } catch {
      // Report it rather than leaving him told he is locked. The commonest
      // cause is a window under fifteen minutes, which Screen Time refuses.
      log.error("arm failed: \(error.localizedDescription, privacy: .public)")
      return armResult(hasSelection: true,
                       schedulingError: error.localizedDescription)
    }
  }

  /// Stand the window down and lift anything it raised.
  static func disarm() {
    DeviceActivityCenter().stopMonitoring([activityName])
    clearShield()
    UserDefaults(suiteName: suiteName)?.removeObject(forKey: windowKey)
    log.info("disarmed")
  }

  // MARK: - the shield the extension drives

  static func raiseShield() {
    guard let selection = SharedData.argonLockSelection() else { return }
    store.shield.applications =
      selection.applicationTokens.isEmpty ? nil : selection.applicationTokens
    store.shield.applicationCategories =
      selection.categoryTokens.isEmpty
      ? nil : ShieldSettings.ActivityCategoryPolicy.specific(selection.categoryTokens)
    store.shield.webDomains =
      selection.webDomainTokens.isEmpty ? nil : selection.webDomainTokens
  }

  static func clearShield() {
    store.shield.applications = nil
    store.shield.applicationCategories = nil
    store.shield.webDomains = nil
  }

  // MARK: - what is currently armed

  struct Window: Codable, Equatable {
    let from: Date
    let until: Date

    func save(to suite: String, key: String) {
      guard let data = try? JSONEncoder().encode(self) else { return }
      UserDefaults(suiteName: suite)?.set(data, forKey: key)
    }
  }

  static func stored() -> Window? {
    guard let data = UserDefaults(suiteName: suiteName)?.data(forKey: windowKey)
    else { return nil }
    return try? JSONDecoder().decode(Window.self, from: data)
  }
}
