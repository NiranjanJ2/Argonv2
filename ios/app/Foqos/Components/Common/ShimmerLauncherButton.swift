import SwiftUI

/// The launcher: "Begin focus", "Go", "Create your first profile".
///
/// Name kept for its call sites. It was a gradient capsule with a blue drop
/// shadow and a white sheen that swept across it every few seconds. Now it is
/// the sheet's prominent button — blue words on a faint blue wash inside a
/// blue draft box — and a disabled one is a grey construction line.
///
/// `imageName` (a colour sticker) is accepted and ignored; the icon is always
/// the SF Symbol, so it takes the button's colour like everything else.
struct ShimmerLauncherButton: View {
  let title: String
  let iconName: String
  let imageName: String?
  let height: CGFloat
  let isEnabled: Bool
  let accessibilityLabel: String
  let action: () -> Void

  init(
    title: String,
    iconName: String = "play.fill",
    imageName: String? = nil,
    height: CGFloat = 56,
    isEnabled: Bool = true,
    accessibilityLabel: String,
    action: @escaping () -> Void
  ) {
    self.title = title
    self.iconName = iconName
    self.imageName = imageName
    self.height = height
    self.isEnabled = isEnabled
    self.accessibilityLabel = accessibilityLabel
    self.action = action
  }

  var body: some View {
    Button(action: action) {
      HStack(spacing: 10) {
        Image(systemName: iconName).font(.callout.weight(.semibold))
        Text(title).font(Argon.body.weight(.semibold))
      }
      .foregroundStyle(isEnabled ? Argon.accent : Argon.Tone.faint)
      .frame(maxWidth: .infinity)
      .frame(height: height)
      .contentShape(Rectangle())
    }
    .buttonStyle(LauncherButtonStyle(isEnabled: isEnabled))
    .disabled(!isEnabled)
    .accessibilityLabel(Text(accessibilityLabel))
  }
}

/// Pressing deepens the wash; nothing scales or springs.
struct LauncherButtonStyle: ButtonStyle {
  var isEnabled = true

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .draftBox(
        stroke: isEnabled ? Argon.accent.opacity(0.7) : Argon.line,
        fill: isEnabled ? Argon.accent.opacity(configuration.isPressed ? 0.22 : 0.12) : .clear,
        overshoot: 6)
  }
}
