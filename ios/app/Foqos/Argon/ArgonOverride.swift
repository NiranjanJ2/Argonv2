import Foundation

/// The local half of the emergency release.
///
/// Carried over from v1 unchanged. Like ArgonMetered and ArgonRoutineActivity
/// this belongs to the blocking layer rather than the assistant, and it is
/// deliberately independent of the server.
///
/// The server has an override too, but reaching it needs a network, a running
/// gateway and a phone that is allowed to talk to it. An escape hatch with
/// dependencies is not an escape hatch, so this one lives entirely on the
/// device: while it is engaged the reconciler refuses to apply any block, no
/// matter what the server asks for. It works in airplane mode.
enum ArgonOverride {
  /// How long a release holds Argon off. Shared by the in-session emergency
  /// unblock and the switch in Settings, so the two cannot drift apart and
  /// mean different things by "emergency release".
  static let defaultMinutes = 120

  /// Nil when no override is engaged or it has expired. Stored in the app
  /// group (`ArgonRoutineSettings`) so the evening block honours it too.
  static var activeUntil: Date? { ArgonRoutineSettings.releasedUntil }

  static var isActive: Bool { activeUntil != nil }

  /// Whether the override in force came from the server rather than from his
  /// own emergency unblock. A server release ends when the server withdraws
  /// it (he asked to be locked again, or started a task); his own does not.
  private static let fromServerKey = "argon.override.fromServer"
  static var isFromServer: Bool { UserDefaults.standard.bool(forKey: fromServerKey) }

  static func engage(minutes: Int) {
    engage(until: Date().addingTimeInterval(TimeInterval(max(1, minutes) * 60)),
           fromServer: false)
  }

  static func engage(until: Date, fromServer: Bool) {
    ArgonRoutineSettings.setReleased(until: until)
    UserDefaults.standard.set(fromServer, forKey: fromServerKey)
    ArgonLog.note("lock", "override engaged until "
                  + until.formatted(date: .omitted, time: .shortened)
                  + (fromServer ? " (asked Argon)" : " (emergency)"))
  }

  static func clear() {
    ArgonRoutineSettings.setReleased(until: nil)
    UserDefaults.standard.removeObject(forKey: fromServerKey)
  }
}
