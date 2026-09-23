import SwiftUI

/// A screen's name in the sheet's lettering: SF Pro expanded, no ornament.
///
/// It used to trail a small blue dot with a blue shadow under it — a glow, and
/// the only thing on the title line competing with the words.
struct AppTitle: View {
  let title: String
  let font: Font
  let fontWeight: Font.Weight
  let horizontalPadding: CGFloat

  init(
    _ title: String = "Argon",
    font: Font = Argon.screenTitle,
    fontWeight: Font.Weight = .semibold,
    horizontalPadding: CGFloat = Argon.margin
  ) {
    self.title = title
    self.font = font
    self.fontWeight = fontWeight
    self.horizontalPadding = horizontalPadding
  }

  var body: some View {
    Text(title)
      .font(font)
      .fontWeight(fontWeight)
      .foregroundStyle(Argon.Tone.primary)
      .padding(.horizontal, horizontalPadding)
  }
}

#Preview {
  VStack(alignment: .leading, spacing: 24) {
    AppTitle()
    AppTitle("Focus")
  }
  .padding(20)
  .argonAmbience()
}
