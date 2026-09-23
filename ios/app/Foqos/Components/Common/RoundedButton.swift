import SwiftUI
import UIKit

/// A small secondary control: words, an icon or both, inside a draft box.
///
/// Name kept for its call sites. It was a glass capsule — `glassEffect` on
/// iOS 26, a slate fill before it — under a gradient stroke. Now it is the
/// same outline every box on the sheet uses, with nothing behind it.
/// `backgroundColor` is ignored; the sheet has no filled controls.
struct RoundedButton: View {
  let text: String
  let action: () -> Void
  let backgroundColor: Color
  let textColor: Color
  let font: Font
  let fontWeight: Font.Weight
  let iconName: String?
  let imageName: String?

  init(
    _ text: String,
    action: @escaping () -> Void,
    backgroundColor: Color = .clear,
    textColor: Color = Argon.accent,
    font: Font = .subheadline,
    fontWeight: Font.Weight = .medium,
    iconName: String? = nil,
    imageName: String? = nil
  ) {
    self.text = text
    self.action = action
    self.backgroundColor = backgroundColor
    self.textColor = textColor
    self.font = font
    self.fontWeight = fontWeight
    self.iconName = iconName
    self.imageName = imageName
  }

  var body: some View {
    Button(action: {
      UIImpactFeedbackGenerator(style: .light).impactOccurred()
      action()
    }) {
      HStack(spacing: 6) {
        if let iconName = iconName {
          Image(systemName: iconName)
            .font(font)
            .fontWeight(fontWeight)
        }

        if let imageName = imageName {
          Image(imageName)
            .resizable()
            .renderingMode(.template)
            .scaledToFit()
            .frame(width: 18, height: 18)
        }

        if !text.isEmpty {
          Text(text)
            .font(font)
            .fontWeight(fontWeight)
            .lineLimit(1)
        }
      }
      .foregroundStyle(textColor)
      .padding(.horizontal, 12)
      .frame(minWidth: 40, minHeight: 40)
      .draftBox(overshoot: 5)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }
}

#Preview {
  VStack(spacing: 20) {
    RoundedButton("See All") {}
    RoundedButton("View Report", action: {}, iconName: "chart.bar")
    RoundedButton("", action: {}, iconName: "slider.horizontal.3")
  }
  .padding(20)
  .argonAmbience()
}
