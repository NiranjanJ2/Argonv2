import SwiftUI

/// v1's design system, re-pointed at the drawing sheet (`ArgonTheme`).
///
/// The previous palette was near-black navy under four stacked gradients, a
/// floating orb and glass panels with gradient strokes; then a slate pass with
/// an inset glow for selection. Now every primitive here draws linework: boxes
/// are outlines with overshooting edges, selection is a blue outline over a
/// faint blue wash, and nothing glows.
///
/// `ArgonPalette` and friends are referenced 164 times across twenty-three
/// *Foqos* files — v1's Argon theme had quietly become the whole app's theme.
/// Reimplementing the API on `ArgonTheme` rather than deleting it means those
/// files compile untouched and pick up the overhaul, instead of sitting beside
/// it in a different blue.
///
/// Every name here is load-bearing somewhere. Do not rename one without
/// grepping the app target first.
enum ArgonPalette {
  /// The page. Flat on purpose; nothing is painted over it.
  static let canvas = Argon.Ink.deep
  static let canvasLifted = Argon.Ink.slate
  /// Cards and rows. One step up from the page, no gradient.
  static let surface = Argon.Ink.slate
  static let surfaceRaised = Argon.Ink.raised
  /// Hairlines. Visible against surface without drawing the eye.
  static let hairline = Argon.line

  /// The one accent. Used for selection and for nothing decorative.
  static let electricBlue = Argon.accent
  /// Was a paler second blue. One blue now.
  static let iceBlue = Argon.accent
  static let cobalt = Argon.accentDeep
  static let cyan = Argon.accent

  /// Body text at full strength, and the quieter tier for captions.
  static let ink = Argon.Tone.primary
  static let mutedInk = Argon.Tone.secondary

  static let warning = Color(red: 0.929, green: 0.635, blue: 0.298)
  static let danger = Argon.overdue
}

extension Font {
  /// Was serif, then rounded. Now the expanded width `Argon.screenTitle` uses,
  /// so a Foqos heading and an Argon one are the same lettering.
  static func argonDisplay(_ size: CGFloat, weight: Font.Weight = .semibold) -> Font {
    // SF Pro expanded, as `Argon.screenTitle`: drawing-sheet lettering.
    .system(size: size, weight: weight).width(.expanded)
  }
}

/// The page behind everything. The sheet's flat ground.
///
/// Deliberately not a gradient. The old one moved under scrolling text and
/// forced every card to fight it for contrast.
struct ArgonBackdrop: View {
  var accentColor = ArgonPalette.electricBlue

  var body: some View {
    ArgonPalette.canvas.ignoresSafeArea()
  }
}

/// Kept for its call sites; it used to be an inset glow — a recessed face lit
/// from the rim by three blurred strokes. Now it is the sheet's own idiom: an
/// outline whose edges run past the corners. Live or chosen, the outline turns
/// blue over a faint blue wash; otherwise it is a construction line.
///
/// The footprint never changes between states, so nothing reflows when a
/// choice is made. The overshoot is drawn outside the bounds, into whatever
/// spacing the caller left — which is what makes neighbouring boxes read as
/// one drawing.
struct ArgonInsetGlow: ViewModifier {
  var isActive: Bool
  /// Ignored. Kept so call sites compile; the sheet has square corners.
  var cornerRadius: CGFloat = 12
  /// A deeper wash while pressed.
  var inset: CGFloat = 3
  var tint: Color = ArgonPalette.electricBlue

  func body(content: Content) -> some View {
    content
      .background(isActive ? tint.opacity(inset > 3 ? 0.20 : 0.10) : Color.clear)
      .overlay(
        DraftFrame(overshoot: 6)
          .stroke(isActive ? tint.opacity(0.7) : Argon.line, lineWidth: 1)
          .allowsHitTesting(false)
      )
      .animation(.easeOut(duration: 0.16), value: isActive)
  }
}

extension View {
  /// Blue outline and wash for a control that is live or chosen.
  func argonInsetGlow(
    _ isActive: Bool,
    cornerRadius: CGFloat = 12,
    inset: CGFloat = 3,
    tint: Color = ArgonPalette.electricBlue
  ) -> some View {
    modifier(
      ArgonInsetGlow(
        isActive: isActive, cornerRadius: cornerRadius, inset: inset, tint: tint
      )
    )
  }

  /// Mark this as pickable, and show whether it is picked.
  func argonSelectable(_ isSelected: Bool, cornerRadius: CGFloat = 12) -> some View {
    argonInsetGlow(isSelected, cornerRadius: cornerRadius)
  }
}

/// A pickable row. The wizard's basic unit: the form's square checkbox, then
/// the words.
struct ArgonChoiceButton: View {
  let title: String
  var caption: String? = nil
  let isSelected: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 12) {
        DraftCheck(on: isSelected)

        VStack(alignment: .leading, spacing: 2) {
          Text(title)
            .font(.callout)
            .foregroundStyle(ArgonPalette.ink)
            .multilineTextAlignment(.leading)
          if let caption, !caption.isEmpty {
            Text(caption)
              .font(.footnote)
              .foregroundStyle(ArgonPalette.mutedInk)
          }
        }
        Spacer(minLength: 0)
      }
      .padding(.horizontal, 14)
      // 52pt: comfortably past the 44pt minimum, because these get tapped in
      // a hurry and a miss here costs a whole step of the wizard.
      .frame(minHeight: 52)
      .frame(maxWidth: .infinity, alignment: .leading)
      .argonSelectable(isSelected)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }
}

/// The primary action on a screen.
///
/// Blue while it is actually actionable, a grey construction line while it is
/// not, so "can I press this yet" is answered by looking.
struct ArgonPrimaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    Face(configuration: configuration)
  }

  private struct Face: View {
    @Environment(\.isEnabled) private var isEnabled
    let configuration: Configuration

    var body: some View {
      configuration.label
        .font(.callout.weight(.semibold))
        .foregroundStyle(isEnabled ? Argon.accent : Argon.Tone.faint)
        .frame(maxWidth: .infinity)
        .frame(height: 50)
        .argonInsetGlow(isEnabled, inset: configuration.isPressed ? 5 : 3)
    }
  }
}

/// A secondary action. Never blue, so it cannot be mistaken for the primary
/// one at a glance even when both are available.
struct ArgonSecondaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.callout.weight(.medium))
      .foregroundStyle(ArgonPalette.mutedInk)
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .argonInsetGlow(false)
      .opacity(configuration.isPressed ? 0.7 : 1)
  }
}

/// Kept so existing call sites compile. It was a 176pt animated orb, then a
/// tinted circle; now it is a registration mark — a small square drawn with
/// draft corners around the running-task pulse.
struct ArgonOrb: View {
  var size: CGFloat = 176
  var accentColor = ArgonPalette.electricBlue
  var showsOrbit = true

  var body: some View {
    let side = min(size, 28)
    ZStack {
      DraftFrame(overshoot: 5).stroke(Argon.lineStrong, lineWidth: 1)
      ArgonPulse(colour: accentColor)
    }
    .frame(width: side, height: side)
    .padding(5)
    .accessibilityHidden(true)
  }
}

private struct ArgonGlassPanelModifier: ViewModifier {
  func body(content: Content) -> some View {
    content
      .overlay(
        DraftFrame().stroke(Argon.line, lineWidth: 1).allowsHitTesting(false)
      )
  }
}

extension View {
  /// Name kept; it is a draft box now — an outline with overshooting edges,
  /// no fill. The overshoot is drawn outside the bounds, so leave it room.
  func argonGlassPanel(
    cornerRadius: CGFloat = 24,
    strokeOpacity: Double = 0.18
  ) -> some View {
    modifier(ArgonGlassPanelModifier())
  }
}

/// The widget target's palette. Separate because the widget compiles a small
/// subset of these files and should not have to pull in the whole design
/// system to paint four labels.
enum ArgonWidgetPalette {
  static let canvas = Argon.Ink.base
  static let surface = Argon.Ink.slate
  static let accent = Argon.accent
  static let ink = Argon.Tone.primary
  static let mutedInk = Argon.Tone.secondary
  static let danger = Argon.overdue
  static let running = Argon.running
}
