import SwiftUI

/// A card's box: an outline with overshooting edges, and a faint blue wash
/// while the thing it holds is live.
///
/// This was an animated aurora — metaball blobs of the theme colour, three
/// blurred radial gradients blended additively, redrawn every frame by a
/// TimelineView. The sheet has no decoration, so "active" is now what it is on
/// Today: the blue line and the blue wash, standing still.
///
/// The initialiser keeps its old parameters so call sites compile; the blob
/// ones are ignored.
struct CardBackground: View {
  var isActive: Bool
  var customColor: Color?
  var backgroundColor: Color?

  init(
    isActive: Bool = false,
    customColor: Color? = nil,
    backgroundColor: Color? = nil,
    cornerRadius: CGFloat = 0,
    activeBlobScale: CGFloat = 1,
    activeBlobCount: Int = 5,
    activeBlobSizeRange: ClosedRange<CGFloat> = 0.30...0.55,
    activeBlobWidthRange: ClosedRange<CGFloat> = 1.0...1.0,
    activeBlobHeightRange: ClosedRange<CGFloat> = 1.0...1.0
  ) {
    self.isActive = isActive
    self.customColor = customColor
    self.backgroundColor = backgroundColor
  }

  private var tint: Color { customColor ?? Argon.accent }

  var body: some View {
    Rectangle()
      .fill(isActive ? tint.opacity(0.08) : (backgroundColor ?? .clear))
      .overlay(
        DraftFrame().stroke(isActive ? tint.opacity(0.55) : Argon.line, lineWidth: 1)
      )
  }

  /// The card's colour, for other components.
  public func getCardColor() -> Color { tint }
}

#Preview {
  ZStack {
    Argon.Ink.base.ignoresSafeArea()
    CardBackground(isActive: true)
      .frame(height: 170)
      .padding(.horizontal, 28)
  }
}
