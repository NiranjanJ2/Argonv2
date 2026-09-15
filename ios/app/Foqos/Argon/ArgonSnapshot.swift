import Foundation
import WidgetKit

/// What the widget is allowed to know.
///
/// The widget runs in its own process and cannot reach the store, the outbox or
/// the network the way the app does. Rather than give it a second, slightly
/// different client — which is how the old app ended up with four refresh paths
/// that could disagree — the app writes this one small snapshot to the shared
/// App Group whenever its state changes, and the widget only reads.
///
/// One writer, one reader, one shape. If the widget looks stale, the app has
/// not refreshed; there is no second thing to debug.
struct ArgonSnapshot: Codable, Equatable {
  var headline: String
  var detail: String
  var overdue: Int
  var open: Int
  var watching: Bool
  var updated: Date

  static let empty = ArgonSnapshot(headline: "Argon", detail: "Open the app",
                                   overdue: 0, open: 0, watching: false,
                                   updated: .distantPast)

  static let suite = "group.com.niranjanj.argon"
  private static let key = "argon.snapshot"

  static func load() -> ArgonSnapshot {
    guard let defaults = UserDefaults(suiteName: suite),
          let data = defaults.data(forKey: key),
          let snapshot = try? JSONDecoder().decode(ArgonSnapshot.self, from: data)
    else { return .empty }
    return snapshot
  }

  func save() {
    guard let defaults = UserDefaults(suiteName: Self.suite),
          let data = try? JSONEncoder().encode(self) else { return }
    defaults.set(data, forKey: Self.key)
    WidgetCenter.shared.reloadAllTimelines()
  }

  /// Built from the state the app already holds, so the two cannot drift.
  static func from(state: ArgonState) -> ArgonSnapshot {
    let open = state.sortedTasks
    let headline: String
    let detail: String

    if let started = state.started {
      headline = started.title
      detail = "in progress"
    } else if let next = open.first {
      headline = next.title
      let due = next.dueLabel()
      detail = due.isEmpty ? (next.subject ?? "next up") : due
    } else {
      headline = "Nothing open"
      detail = state.school.schedule ?? ""
    }

    return ArgonSnapshot(headline: headline, detail: detail,
                         overdue: state.overdueCount, open: open.count,
                         watching: state.ticking, updated: Date())
  }
}
