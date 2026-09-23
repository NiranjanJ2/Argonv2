import SwiftUI

/// The Focus tab before any profile exists: what a profile is for, and the
/// two ways to make one.
///
/// Left-aligned in a draft box like every other block on the sheet. It was a
/// centred hero — an orb, tracked capitals, a 39pt headline — which made the
/// empty state the loudest thing in the app.
struct Welcome: View {
  let onGuidedTap: () -> Void
  let onAdvancedTap: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text("No focus profile yet")
        .font(Argon.caption)
        .foregroundStyle(Argon.Tone.secondary)

      Text("Make space for what matters.")
        .font(.title2.weight(.semibold).width(.expanded))
        .foregroundStyle(Argon.Tone.primary)
        .fixedSize(horizontal: false, vertical: true)

      Text("Create a focus profile once. Argon can take the wheel whenever it’s time to lock in.")
        .font(Argon.detail)
        .foregroundStyle(Argon.Tone.secondary)
        .fixedSize(horizontal: false, vertical: true)

      ShimmerLauncherButton(
        title: "Create your first profile",
        iconName: "plus",
        height: 50,
        accessibilityLabel: "Start guided profile setup",
        action: onGuidedTap
      )
      .padding(.top, 6)

      Button(action: onAdvancedTap) {
        Text("Use the full profile editor")
          .font(Argon.detail.weight(.medium))
          .foregroundStyle(Argon.accent)
          .frame(minHeight: 44)
          .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.horizontal, 16)
    .padding(.top, 16)
    .padding(.bottom, 6)
    .argonGlassPanel()
  }
}

#Preview {
  Welcome(onGuidedTap: {}, onAdvancedTap: {})
    .padding(.horizontal, Argon.margin)
    .argonAmbience()
}
