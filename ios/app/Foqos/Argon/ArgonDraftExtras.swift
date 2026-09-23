import SwiftUI

/// Pieces the Foqos half of the app needs on top of `ArgonTheme`.
///
/// Kept out of `ArgonTheme.swift` because the widget and the shield compile
/// that file and have no use for these, and out of `ArgonPalette.swift`
/// because that file is v1's compatibility layer, not a place for new API.

extension View {
  /// A free-standing draft box around this view, for when the caller owns the
  /// padding: an optional wash, then the outline with its overshooting edges.
  ///
  /// The overshoot is drawn outside the bounds. Leave it room, and never clip
  /// the parent — a clip trims exactly the part that makes it read as drawn.
  func draftBox(stroke: Color = Argon.line, fill: Color = .clear,
                overshoot: CGFloat = Argon.overshoot) -> some View {
    background(fill)
      .overlay(
        DraftFrame(overshoot: overshoot).stroke(stroke, lineWidth: 1)
          .allowsHitTesting(false)
      )
  }

  /// Red as the brief allows it: soft red text on a faint red wash, inside a
  /// red construction line. Late work, failures, the emergency controls.
  func draftAlarm(overshoot: CGFloat = Argon.overshoot) -> some View {
    foregroundStyle(Argon.overdue)
      .draftBox(stroke: Argon.overdue.opacity(0.45), fill: Argon.overdue.opacity(0.10),
                overshoot: overshoot)
  }

  /// A stock `Form` or `List` on the sheet's ground.
  ///
  /// Foqos's editors are system forms with a dozen custom pickers inside; a
  /// hand-drawn box per row would mean re-deriving every row's position in
  /// every section. Plain style instead: square, full-width rows separated by
  /// construction lines, which is the same drawing with the box sides left
  /// off. No grouped cards, no rounded corners.
  func argonForm() -> some View {
    listStyle(.plain)
      .scrollContentBackground(.hidden)
      .listRowSeparatorTint(Argon.line)
      .listSectionSeparatorTint(Argon.line)
      .environment(\.defaultMinListRowHeight, 48)
      .background(Argon.Ink.base.ignoresSafeArea())
      .tint(Argon.accent)
  }
}

/// A square icon control with draft corners: the settings, close and insights
/// buttons. 44pt, so it meets the tap minimum without a halo of padding.
struct DraftIconButton: View {
  let systemName: String
  var colour: Color = Argon.accent
  var accessibilityLabel: String
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Image(systemName: systemName)
        .font(.body.weight(.medium))
        .foregroundStyle(colour)
        .frame(width: 40, height: 40)
        .draftBox(overshoot: 5)
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityLabel(accessibilityLabel)
  }
}

/// A section's name, as Today draws them: small, wide, sentence case. An
/// optional action sits at the far end as plain blue words.
struct DraftSectionLabel: View {
  let title: String
  var count: Int? = nil
  var action: (title: String, run: () -> Void)? = nil

  var body: some View {
    HStack(spacing: 8) {
      Text(title).foregroundStyle(Argon.Tone.secondary)
      if let count, count > 0 { Text("\(count)").foregroundStyle(Argon.Tone.faint) }
      Spacer()
      if let action {
        Button(action.title, action: action.run)
          .font(Argon.detail.weight(.medium))
          .foregroundStyle(Argon.accent)
          .buttonStyle(.plain)
      }
    }
    .font(Argon.caption)
  }
}
