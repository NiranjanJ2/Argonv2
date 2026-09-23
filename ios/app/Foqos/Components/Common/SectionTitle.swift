import SwiftUI

/// A section's name, drawn the way Today draws them: small, wide, sentence
/// case, secondary grey. It was a 20pt serif heading with a glass capsule
/// button beside it; the action is now plain blue words at the far end.
///
/// `buttonIcon` is accepted and ignored — the word says what it does.
struct SectionTitle: View {
  let title: String
  let buttonText: String?
  let buttonAction: (() -> Void)?
  let buttonIcon: String?

  init(
    _ title: String, buttonText: String? = nil, buttonAction: (() -> Void)? = nil,
    buttonIcon: String? = nil
  ) {
    self.title = title
    self.buttonText = buttonText
    self.buttonAction = buttonAction
    self.buttonIcon = buttonIcon
  }

  var body: some View {
    if let buttonText, let buttonAction {
      DraftSectionLabel(title: title, action: (buttonText, buttonAction))
    } else {
      DraftSectionLabel(title: title)
    }
  }
}

#Preview {
  VStack(spacing: 24) {
    SectionTitle("Activity")
    SectionTitle("Profiles", buttonText: "Manage", buttonAction: {})
  }
  .padding(20)
  .argonAmbience()
}
