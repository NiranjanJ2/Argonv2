import Foundation

/// One line of an Argon message, already classified.
///
/// The system prompt promises him headings, lists, checkboxes and a row of
/// buttons; an inline-only `AttributedString` delivers none of them, so the
/// promise showed up as literal `- [ ]` and links that went nowhere.
///
/// Parsing is line-based on purpose. A full Markdown engine is a dependency and
/// a rendering surface, and the only constructs worth having in a chat bubble
/// are these — inline styling comes free from `AttributedString(markdown:)`.
/// Pure Foundation so it runs in the SwiftPM tests; the view is
/// `ArgonRichText.swift`.
///
/// Not `Identifiable`: v1 derived an id from the content, so two identical
/// bullets collided in `ForEach` and every divider minted a fresh UUID on each
/// render. The view keys blocks by position instead.
enum ArgonBlock: Equatable {
  case heading(String)
  case paragraph(String)
  case bullet(String)
  case numbered(index: Int, text: String)
  /// `id` is `<messageID>:<line>`, the key his tick is stored under.
  case checkbox(id: String, text: String, checked: Bool)
  /// A line that is nothing but `argon:` links, rendered as a row of buttons.
  case actions([ArgonAction])
  case divider
}

/// A button Argon offered, written as a link so no payload had to grow.
///
/// `[Start HW 9](argon:start/8c45122ea6b5)` — the verb and the task id are
/// already in the message, and the model already knows task ids from its
/// tools. A structured `actions` array would have meant a field on the
/// message, on the response, on the model and a tool parameter, to carry two
/// strings a link already holds.
struct ArgonAction: Equatable {
  let label: String
  let verb: String
  let taskID: String

  /// Parses `argon:<verb>/<id>`. Anything else is not an action and stays a link.
  init?(label: String, url: String) {
    guard url.hasPrefix("argon:") else { return nil }
    let parts = url.dropFirst("argon:".count).split(separator: "/", maxSplits: 1,
                                                    omittingEmptySubsequences: false)
    guard parts.count == 2, !parts[0].isEmpty, !parts[1].isEmpty else { return nil }
    self.label = label
    self.verb = String(parts[0]).lowercased()
    self.taskID = String(parts[1])
  }

  /// The task this button would act on, or nil if tapping it could change
  /// nothing — which is what the view disables on.
  ///
  /// An exact id wins; failing that, a *unique* prefix, because the model
  /// sometimes truncates the id it copied from its tools. Two tasks sharing
  /// the prefix is nil rather than a guess: starting the wrong assignment is
  /// worse than a grey button. A task already done, or already started for
  /// `start`, is nil too, so the button greys out once it has done its job.
  func target(in tasks: [ArgonTask]) -> ArgonTask? {
    let task = tasks.first { $0.id == taskID } ?? {
      let hits = tasks.filter { $0.id.hasPrefix(taskID) }
      return hits.count == 1 ? hits[0] : nil
    }()
    guard let task, !task.done else { return nil }
    switch verb {
    case "start": return task.isStarted ? nil : task
    // "done" is v1's spelling; the prompt says "complete", the model drifts.
    case "complete", "done": return task
    default: return nil
    }
  }
}

enum ArgonMarkdown {
  // Extended delimiters: the package builds in Swift 5 mode, where bare
  // `/regex/` literals need a compiler flag.
  private static let bullet = #/^\s*[-*•]\s+(.*)$/#
  private static let numbered = #/^\s*(\d+)[.)]\s+(.*)$/#
  private static let checkbox = #/^\s*[-*]\s+\[([ xX])\]\s+(.*)$/#
  private static let heading = #/^\s*#{1,6}\s+(.*)$/#
  private static let divider = #/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/#
  private static let link = #/\[([^\]]+)\]\(([^)]+)\)/#

  /// Every `argon:` link on the line, but only if the line is *nothing else*.
  /// A sentence that happens to contain one is prose with a link in it.
  static func actionRow(_ line: String) -> [ArgonAction]? {
    var found: [ArgonAction] = []
    var rest = line
    for m in line.matches(of: link) {
      guard let action = ArgonAction(label: String(m.1), url: String(m.2)) else { return nil }
      found.append(action)
      rest = rest.replacingOccurrences(of: String(m.0), with: "")
    }
    guard !found.isEmpty, rest.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return found
  }

  /// Split a message into renderable blocks. Never throws: an unparseable line
  /// is a paragraph, because dropping something Argon said is worse than
  /// rendering it plainly. Blank lines are dropped; the view's spacing does
  /// their job.
  static func blocks(_ text: String, messageID: String = "") -> [ArgonBlock] {
    var out: [ArgonBlock] = []
    for (offset, raw) in text.components(separatedBy: .newlines).enumerated() {
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line.isEmpty { continue }

      // Checkbox before bullet: "- [ ] x" matches both, and the checkbox is
      // the more specific reading. Divider before bullet for the same reason
      // with "- - -".
      if let row = actionRow(line) {
        out.append(.actions(row))
      } else if let m = line.firstMatch(of: checkbox) {
        let checked = String(m.1).lowercased() == "x"
        out.append(.checkbox(id: "\(messageID):\(offset)", text: String(m.2), checked: checked))
      } else if line.firstMatch(of: divider) != nil {
        out.append(.divider)
      } else if let m = line.firstMatch(of: heading) {
        out.append(.heading(String(m.1)))
      } else if let m = line.firstMatch(of: numbered) {
        out.append(.numbered(index: Int(m.1) ?? 1, text: String(m.2)))
      } else if let m = line.firstMatch(of: bullet) {
        out.append(.bullet(String(m.1)))
      } else {
        out.append(.paragraph(line))
      }
    }
    return out
  }

  /// Inline styling — bold, italic, code, links. Falls back to the raw string,
  /// so a stray asterisk shows as an asterisk rather than eating the message.
  static func inline(_ text: String) -> AttributedString {
    (try? AttributedString(
      markdown: text,
      options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    )) ?? AttributedString(text)
  }
}

/// Where his ticks live: on this phone, per message line, across relaunches.
///
/// A checkbox in a message is his scratchpad, not the task board. Argon does
/// not say which task a line means, and guessing by title is how a tick on
/// "read chapter 3" would close the wrong assignment. Ticking real work is
/// what the Today tab and the action buttons are for.
enum ArgonCheckboxState {
  private static let key = "argon.message.checkboxes"

  static func checked(_ id: String, in defaults: UserDefaults = .standard) -> Bool? {
    (defaults.dictionary(forKey: key) as? [String: Bool])?[id]
  }

  static func set(_ id: String, _ value: Bool, in defaults: UserDefaults = .standard) {
    var all = (defaults.dictionary(forKey: key) as? [String: Bool]) ?? [:]
    all[id] = value
    defaults.set(all, forKey: key)
  }
}

extension ArgonStore {
  /// Run a button or an inline `argon:` link against the board.
  ///
  /// Goes through the same `start`/`complete` the Today tab uses, so a tap in
  /// chat is optimistic, queued in the outbox when offline, and shows on Today
  /// at once. Returns false when there was nothing to act on.
  @discardableResult
  func perform(_ action: ArgonAction) async -> Bool {
    guard let task = action.target(in: state.tasks) else { return false }
    if action.verb == "start" { await start(task) } else { await complete(task) }
    return true
  }
}
