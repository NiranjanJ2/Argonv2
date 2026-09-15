import SwiftUI

/// The look: slate, blue-grey, glass.
///
/// Everything visual lives here so a colour is never invented at a call site.
/// Argon is a dark-first app — he uses it in the evening, in his room, usually
/// after nine — so the palette is built for that and light mode is a courtesy,
/// not the baseline.
enum Argon {

  // MARK: palette

  /// Blue-grey slate, cool end. Not black: a true black background makes glass
  /// look like a sticker, because there is nothing behind it to refract.
  enum Ink {
    static let void = Color(red: 0.035, green: 0.047, blue: 0.067)   // #090C11
    static let deep = Color(red: 0.055, green: 0.071, blue: 0.098)   // #0E1219
    static let slate = Color(red: 0.086, green: 0.106, blue: 0.145)  // #161B25
    static let raised = Color(red: 0.125, green: 0.149, blue: 0.196) // #202632
  }

  enum Text {
    static let primary = Color(red: 0.902, green: 0.925, blue: 0.957)
    static let secondary = Color(red: 0.553, green: 0.596, blue: 0.667)
    static let faint = Color(red: 0.373, green: 0.412, blue: 0.478)
  }

  /// Steel blue — the one colour that is Argon's. Used sparingly: on a slate
  /// field, a single accent reads as meaning, and three read as decoration.
  static let accent = Color(red: 0.431, green: 0.659, blue: 0.847)     // #6EA8D8
  static let accentDim = Color(red: 0.278, green: 0.435, blue: 0.573)

  /// Warm, for the only thing that should ever feel urgent.
  static let overdue = Color(red: 0.878, green: 0.463, blue: 0.408)    // #E07668
  static let running = Color(red: 0.459, green: 0.788, blue: 0.616)    // #75C99D

  static let hairline = Color.white.opacity(0.08)
  static let hairlineBright = Color.white.opacity(0.14)

  // MARK: type

  static let title = Font.system(size: 28, weight: .semibold, design: .rounded)
  static let heading = Font.system(size: 15, weight: .semibold)
  static let body = Font.system(size: 15, weight: .regular)
  static let label = Font.system(size: 12, weight: .medium)
  static let mono = Font.system(size: 12, weight: .medium, design: .monospaced)
}

// MARK: - the ambient field

/// The background knows what time it is.
///
/// Argon watches from four in the afternoon until midnight on school nights,
/// and is off duty otherwise. Rather than print that somewhere, the room
/// behind the glass says it: the field warms and lifts while he is being
/// watched over, and goes cold and still when Argon is off. It is the one
/// piece of state he never has to read.
struct ArgonAmbience: View {
  /// 0 at four o'clock, 1 at midnight. Drives how deep the field sits.
  var evening: Double
  /// Off duty — weekends, and the small hours.
  var resting: Bool

  var body: some View {
    ZStack {
      Argon.Ink.void

      // Two slow blooms, offset. Their warmth tracks the evening; when Argon
      // is resting they desaturate toward the slate and sink.
      bloom(colour: topColour, size: 1.25)
        .offset(x: -110, y: -220 + CGFloat(evening * 60))
        .blur(radius: 90)

      bloom(colour: bottomColour, size: 0.95)
        .offset(x: 130, y: 260 - CGFloat(evening * 40))
        .blur(radius: 110)

      // A vignette keeps the glass edges legible over the blooms.
      RadialGradient(colors: [.clear, Argon.Ink.void.opacity(0.75)],
                     center: .center, startRadius: 160, endRadius: 520)
    }
    .ignoresSafeArea()
    .animation(.easeInOut(duration: 1.2), value: resting)
  }

  private func bloom(colour: Color, size: Double) -> some View {
    Circle()
      .fill(RadialGradient(colors: [colour, colour.opacity(0)],
                           center: .center, startRadius: 0, endRadius: 260))
      .frame(width: 460 * size, height: 460 * size)
  }

  private var topColour: Color {
    resting
      ? Argon.Ink.slate.opacity(0.55)
      : Argon.accentDim.opacity(0.30 + 0.10 * (1 - evening))
  }

  private var bottomColour: Color {
    resting
      ? Argon.Ink.deep.opacity(0.6)
      : Color(red: 0.22, green: 0.27, blue: 0.44).opacity(0.40 + 0.12 * evening)
  }

  /// Where we are between 16:00 and midnight, and whether Argon is on duty.
  /// Mirrors the server's own window so the two never disagree on screen.
  static func now(ticking: Bool, date: Date = Date()) -> ArgonAmbience {
    let hour = Calendar.current.component(.hour, from: date)
    let progress = min(max(Double(hour - 16) / 8.0, 0), 1)
    return ArgonAmbience(evening: progress, resting: !ticking)
  }
}

// MARK: - glass

/// A pane of glass. One shape, one border, one shadow — used everywhere, so
/// the app reads as one surface rather than a pile of cards.
struct ArgonGlass<Content: View>: View {
  var tint: Color = .clear
  var padding: CGFloat = 16
  @ViewBuilder var content: Content

  var body: some View {
    content
      .padding(padding)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background {
        RoundedRectangle(cornerRadius: 18, style: .continuous)
          .fill(.ultraThinMaterial)
          .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
              .fill(tint.opacity(0.10))
          }
          .overlay {
            // A brighter top edge is what makes it read as glass rather than
            // as a grey rectangle: it is the light catching the lip.
            RoundedRectangle(cornerRadius: 18, style: .continuous)
              .strokeBorder(
                LinearGradient(colors: [Argon.hairlineBright, Argon.hairline],
                               startPoint: .top, endPoint: .bottom),
                lineWidth: 1)
          }
          .shadow(color: .black.opacity(0.35), radius: 18, y: 10)
      }
      .environment(\.colorScheme, .dark)
  }
}

/// A row inside glass, without the material stacking twice.
struct ArgonDivider: View {
  var body: some View {
    Rectangle().fill(Argon.hairline).frame(height: 1)
  }
}

/// Small capsule for a status word — due date, period, count.
struct ArgonPill: View {
  let text: String
  var colour: Color = Argon.Text.secondary

  var body: some View {
    Text(text)
      .font(Argon.label)
      .foregroundStyle(colour)
      .padding(.horizontal, 8).padding(.vertical, 3)
      .background(Capsule().fill(colour.opacity(0.13)))
      .overlay(Capsule().strokeBorder(colour.opacity(0.20), lineWidth: 0.5))
  }
}

/// A quiet pulse, for the one thing that is genuinely live.
struct ArgonPulse: View {
  var colour: Color = Argon.running
  @State private var on = false

  var body: some View {
    Circle()
      .fill(colour)
      .frame(width: 7, height: 7)
      .overlay(Circle().stroke(colour.opacity(on ? 0 : 0.6), lineWidth: on ? 7 : 0))
      .opacity(on ? 0.75 : 1)
      .animation(.easeOut(duration: 1.8).repeatForever(autoreverses: false), value: on)
      .onAppear { on = true }
  }
}
