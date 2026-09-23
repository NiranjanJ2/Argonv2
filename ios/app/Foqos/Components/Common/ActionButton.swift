import SwiftUI

/// A sheet's primary action, full width.
///
/// The colour passed in is the button's tint rather than its fill: words in
/// that colour over a faint wash of it, inside a draft box of it. So the blue
/// calls read as blue outlines, and the red ones — emergency unblock, cancel a
/// scan — become red as the brief allows it, a tint over a tint. It used to be
/// a filled capsule (`.glassProminent` on iOS 26) with white text and a shadow.
struct ActionButton: View {
  let title: String
  let backgroundColor: Color?
  let iconName: String?
  let iconColor: Color?
  let isLoading: Bool
  let isDisabled: Bool

  let action: () -> Void

  init(
    title: String,
    backgroundColor: Color? = nil,
    iconName: String? = nil,
    iconColor: Color? = nil,
    isLoading: Bool = false,
    isDisabled: Bool = false,
    action: @escaping () -> Void
  ) {
    self.title = title
    self.backgroundColor = backgroundColor
    self.iconName = iconName
    self.iconColor = iconColor
    self.isLoading = isLoading
    self.isDisabled = isDisabled
    self.action = action
  }

  /// System red is a bright fill colour; on the sheet red is always the soft
  /// one, and only ever over its own wash.
  private var tint: Color {
    guard let backgroundColor else { return Argon.accent }
    if backgroundColor == .red || backgroundColor == Argon.overdue { return Argon.overdue }
    if backgroundColor == .gray { return Argon.Tone.secondary }
    return Argon.accent
  }

  private var live: Bool { !(isLoading || isDisabled) }

  var body: some View {
    Button(action: live ? action : {}) {
      HStack(spacing: 8) {
        if isLoading {
          ProgressView().tint(tint).controlSize(.small)
        } else {
          if let iconName = iconName {
            Image(systemName: iconName).font(.body.weight(.semibold))
          }
          Text(title).font(Argon.body.weight(.semibold))
        }
      }
      .foregroundStyle(live ? tint : Argon.Tone.faint)
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .draftBox(stroke: live ? tint.opacity(0.7) : Argon.line,
                fill: live ? tint.opacity(0.12) : .clear, overshoot: 6)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .padding(6)
    .disabled(!live)
  }
}

#Preview("Action Button Examples") {
  VStack(spacing: 20) {
    ActionButton(title: "Save") {}
    ActionButton(title: "Download", iconName: "arrow.down.circle") {}
    ActionButton(title: "Saving...", isLoading: true) {}
    ActionButton(title: "Emergency Unblock", backgroundColor: .red,
                 iconName: "exclamationmark.triangle.fill") {}
    ActionButton(title: "Disabled", iconName: "lock.fill", isDisabled: true) {}
  }
  .padding()
  .argonAmbience()
}
