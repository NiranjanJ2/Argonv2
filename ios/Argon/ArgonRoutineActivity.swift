import DeviceActivity
import Foundation
import ManagedSettings

/// The routine that has to run with the app closed.
///
/// This is the shape to reach for before reaching for a push. A
/// `DeviceActivitySchedule` is registered with the system, and the system runs
/// the monitor extension at its boundaries — with the app closed, the server
/// down, and the network off. The previous implementation reconciled on a
/// `Timer.scheduledTimer` inside the app, which iOS suspends the instant the
/// app backgrounds, so anything the server published only ever applied while he
/// happened to be looking at the screen.
///
/// Note for the Xcode project: the app target picks new files up automatically
/// (`PBXFileSystemSynchronizedRootGroup`), but **FoqosDeviceMonitor** takes an
/// explicit `membershipExceptions` list. Anything the monitor needs must be
/// added there by hand or it fails to build with "cannot find X in scope".
enum ArgonRoutine {
  static let activity = DeviceActivityName("argon.evening")
  static let store = ManagedSettingsStore(named: .init("argon"))

  /// Evening study window. Matches the server's tick window so the two agree
  /// about when the evening is, without needing to talk.
  static let startHour = 16
  static let endHour = 23

  static var schedule: DeviceActivitySchedule {
    DeviceActivitySchedule(
      intervalStart: DateComponents(hour: startHour, minute: 0),
      intervalEnd: DateComponents(hour: endHour, minute: 59),
      repeats: true
    )
  }

  /// Register once. Safe to call again — re-registering replaces.
  static func begin() throws {
    try DeviceActivityCenter().startMonitoring(activity, during: schedule)
  }

  static func end() {
    DeviceActivityCenter().stopMonitoring([activity])
    store.clearAllSettings()
  }

  /// He asked to be let out. Always granted: a lock he cannot escape is a lock
  /// he deletes the app over, and an override that stands down is the only
  /// reason the routine is tolerable at all.
  static func release() {
    store.clearAllSettings()
  }
}
