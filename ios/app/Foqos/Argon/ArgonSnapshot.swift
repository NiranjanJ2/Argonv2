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

/// Where the server is, written by the app for the widget's refresh button.
///
/// The app keeps its address and token in its own defaults, which a widget
/// extension cannot read; the app group is the one place both can.
enum ArgonWidgetServer {
  private static let baseKey = "argon.widget.base"
  private static let tokenKey = "argon.widget.token"

  static func save(base: URL, token: String) {
    let defaults = UserDefaults(suiteName: ArgonSnapshot.suite)
    defaults?.set(base.absoluteString, forKey: baseKey)
    defaults?.set(token, forKey: tokenKey)
  }

  static func load() -> (base: URL, token: String)? {
    let defaults = UserDefaults(suiteName: ArgonSnapshot.suite)
    guard let raw = defaults?.string(forKey: baseKey), let base = URL(string: raw),
          let token = defaults?.string(forKey: tokenKey), !token.isEmpty else { return nil }
    return (base, token)
  }
}

extension ArgonSnapshot {
  /// Fetch the board and redraw. Runs in the widget's own process when the
  /// app is not running, so it cannot lean on the app's store or client.
  /// `fresh=1` has the server re-read Classroom first. Any failure still
  /// redraws, from the last snapshot, rather than leaving a spinner.
  static func refresh() async {
    guard let server = ArgonWidgetServer.load() else {
      WidgetCenter.shared.reloadAllTimelines()
      return
    }
    var parts = URLComponents(url: server.base.appendingPathComponent("v2/state"),
                              resolvingAgainstBaseURL: false)
    parts?.queryItems = [URLQueryItem(name: "fresh", value: "1")]
    guard let url = parts?.url else { return }
    var request = URLRequest(url: url, timeoutInterval: 25)
    request.setValue("Bearer \(server.token)", forHTTPHeaderField: "Authorization")
    if let (data, response) = try? await URLSession.shared.data(for: request),
       (response as? HTTPURLResponse)?.statusCode == 200,
       let state = try? JSONDecoder().decode(ArgonState.self, from: data) {
      from(state: state).save()
    } else {
      WidgetCenter.shared.reloadAllTimelines()
    }
  }
}
