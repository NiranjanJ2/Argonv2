import DeviceActivity
import Foundation
import OSLog
import UserNotifications

/// Keeps the phone's evening block matching the routine the server last sent.
///
/// Ported from v1's `ArgonRoutine.swift`. `ArgonRoutineActivity` came across to
/// v2 unchanged, but nothing armed it, so the evening block that the 4 PM sheet
/// exists to set simply never fired. This is the arming half.
///
/// The server says *what* (the start he chose, or the 18:00 default, and which
/// nights are school nights); this makes the system keep *when*. The schedule
/// repeats daily and `ArgonRoutineActivity` filters the weekday at fire time,
/// so it works with the app closed, the server down and the network off.
///
/// One consequence worth knowing: the schedule repeats until it is re-armed.
/// If he picks 19:30 on Monday and does not open the app on Tuesday, Tuesday
/// blocks at 19:30 too rather than at the default. Re-arming needs the app to
/// wake — foreground, silent push or background refresh — and any of them
/// brings tonight's routine with it.
@MainActor
enum ArgonRoutineScheduler {
  private static let log = Logger(subsystem: "com.niranjanj.argon", category: "routine")
  private static let defaults = UserDefaults.standard
  private static let appliedKey = "argon.routine.applied"
  private static let warningPrefix = "argon.routine.warning."

  /// Rewrite the schedule only when it actually changed.
  ///
  /// This runs on every successful refresh. `startMonitoring` on an unchanged
  /// interval is not free — it tears the activity down and rebuilds it, and a
  /// rebuild that lands mid-interval can drop the shield that is currently up.
  static func apply(_ routine: ArgonRoutine, profileId: UUID) {
    ArgonRoutineSettings.save(schoolNights: routine.schoolNights)

    let fingerprint = routine.fingerprint(profile: profileId.uuidString)
    guard defaults.string(forKey: appliedKey) != fingerprint else { return }

    guard let start = routine.startMinutes, let end = routine.endMinutes else {
      ArgonLog.note("routine", "server sent a start that is not HH:MM: \(routine.startAt)")
      return
    }

    let activity = ArgonRoutineActivity()
    let name = activity.getDeviceActivityName(from: profileId.uuidString)
    let center = DeviceActivityCenter()
    let schedule = DeviceActivitySchedule(
      intervalStart: DateComponents(hour: start / 60, minute: start % 60),
      intervalEnd: DateComponents(hour: end / 60, minute: end % 60),
      repeats: true
    )

    center.stopMonitoring([name])
    do {
      try center.startMonitoring(name, during: schedule)
      defaults.set(fingerprint, forKey: appliedKey)
      scheduleWarnings(routine)
      ArgonLog.note("routine", "armed \(routine.startAt) +\(routine.windowMinutes)m"
                    + (routine.chosen ? "" : " (default)"))
    } catch {
      // Leave the fingerprint unset so the next refresh tries again rather
      // than believing a schedule is armed when none is.
      log.error("could not arm: \(error.localizedDescription, privacy: .public)")
      ArgonLog.note("routine", "arm failed: \(error.localizedDescription)")
    }
  }

  /// The heads-up before the block, one weekly notification per school night.
  ///
  /// v1's sheet promised "a notification 30 minutes before" and, once the
  /// clock moved to the phone, nothing sent it — the server's cron that used
  /// to was cleared and no one replaced it. Local notifications are the
  /// on-device equivalent: they fire with the app closed, like the block.
  /// Weekly rather than daily so the weekday filter lives in the trigger;
  /// a notification cannot check a condition at fire time the way the
  /// monitor extension can.
  private static func scheduleWarnings(_ routine: ArgonRoutine) {
    let center = UNUserNotificationCenter.current()
    let ids = (0..<7).map { "\(warningPrefix)\($0)" }
    center.removePendingNotificationRequests(withIdentifiers: ids)
    guard let warning = routine.warning, routine.warningMinutes > 0 else { return }

    let content = UNMutableNotificationContent()
    content.title = "Starting in \(routine.warningMinutes) minutes"
    content.body = "Your phone locks down at \(routine.startAt)."
    content.sound = .default

    for night in Set(routine.schoolNights) where (0..<7).contains(night) {
      // Python 0=Mon…6=Sun to Calendar 1=Sun…7=Sat, stepping back a day when
      // the warning falls before midnight for a block just after it.
      let pythonDay = warning.dayBefore ? (night + 6) % 7 : night
      var when = DateComponents()
      when.weekday = (pythonDay + 1) % 7 + 1
      when.hour = warning.minutes / 60
      when.minute = warning.minutes % 60
      let request = UNNotificationRequest(
        identifier: "\(warningPrefix)\(night)", content: content,
        trigger: UNCalendarNotificationTrigger(dateMatching: when, repeats: true))
      center.add(request)
    }
  }
}
