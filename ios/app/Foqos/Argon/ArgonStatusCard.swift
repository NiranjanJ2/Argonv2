import SwiftUI

/// Argon's line on the Foqos home screen.
///
/// Reads the same `ArgonStore` as the Argon tabs, so it cannot show a
/// different board — v1 kept this card on its own refresh path and it could
/// disagree with the chat screen about what was due.
struct ArgonStatusCard: View {
  /// Tapping it opens settings, which is what Foqos's home screen expects.
  var onTap: () -> Void = {}

  /// The one store. Read from the app delegate rather than injected, because
  /// Foqos's call site passes only the closure.
  private var store: ArgonStore { ArgonAppDelegate.shared.store }

  init(onTap: @escaping () -> Void = {}) {
    self.onTap = onTap
  }

  /// Type-erased on purpose. This card sits inside Foqos's `HomeView` body,
  /// which is a long modifier chain already close to the type-checker's
  /// budget; returning a deeply generic tree from here tips it over with
  /// "unable to type-check this expression in reasonable time".
  var body: some View {
    AnyView(
      card
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    )
  }

  private var card: some View {
    ArgonGlass(tint: tint, padding: 14) {
      HStack(spacing: 12) {
        marker
        VStack(alignment: .leading, spacing: 2) {
          Text(headline)
            .font(Argon.heading)
            .foregroundStyle(Argon.Tone.primary)
            .lineLimit(1)
          Text(detail)
            .font(Argon.label)
            .foregroundStyle(Argon.Tone.faint)
            .lineLimit(1)
        }
        Spacer(minLength: 6)
        if store.state.overdueCount > 0 {
          ArgonPill(text: "\(store.state.overdueCount)", colour: Argon.overdue)
        }
      }
    }
  }

  @ViewBuilder private var marker: some View {
    if store.state.started != nil {
      ArgonPulse()
    } else {
      Circle()
        .fill(store.state.ticking ? Argon.accent.opacity(0.7) : Argon.Tone.faint.opacity(0.5))
        .frame(width: 7, height: 7)
    }
  }

  private var tint: Color {
    if store.state.started != nil { return Argon.running }
    return store.state.overdueCount > 0 ? Argon.overdue : .clear
  }

  private var headline: String {
    if let started = store.state.started { return started.title }
    if let next = store.state.sortedTasks.first { return next.title }
    return store.connection.isLive ? "Nothing open" : "Argon unreachable"
  }

  private var detail: String {
    if store.state.started != nil { return "in progress" }
    let open = store.state.sortedTasks.count
    if open > 0 { return "\(open) open • \(store.state.ticking ? "watching" : "off duty")" }
    return store.state.school.schedule ?? (store.state.ticking ? "watching" : "off duty")
  }
}
