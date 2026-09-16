import SwiftUI

/// The look: blue-slate, glass, dark.
///
/// Three rules this file exists to enforce, each written after getting it
/// wrong:
///
/// **Type is Dynamic Type.** Never `Font.system(size:)`. Fixed sizes ignore the
/// text size he has chosen in Settings, and the first version of this screen
/// used 15pt body where iOS uses 17 — small for everyone and unreadable for
/// anyone who had turned text up.
///
/// **Blue is the subject, not an accent.** A slate palette with one desaturated
/// blue used only on small pills reads as grey, and then the single warm colour
/// — meant for overdue work — becomes the loudest thing on screen.
///
/// **Decoration must not affect layout.** The ambient field is applied with
/// `.argonAmbience()`, which puts it in a `background`. As a ZStack sibling its
/// 575pt blooms sized the whole stack and pushed the content off a 390pt phone.
enum Argon {

  // MARK: ground

  /// Navy-slate rather than near-black. True black leaves nothing for glass to
  /// refract and drains the blue out of everything above it.
  enum Ink {
    static let base = Color(red: 0.035, green: 0.055, blue: 0.094)   // #090E18
    static let deep = Color(red: 0.055, green: 0.082, blue: 0.137)   // #0E1523
    static let slate = Color(red: 0.094, green: 0.129, blue: 0.196)  // #182132
    static let raised = Color(red: 0.137, green: 0.180, blue: 0.263) // #232E43
  }

  enum Tone {
    static let primary = Color(red: 0.937, green: 0.957, blue: 0.984)
    static let secondary = Color(red: 0.639, green: 0.702, blue: 0.788)
    static let faint = Color(red: 0.443, green: 0.502, blue: 0.588)
  }

  /// Vivid enough to read as blue on a dark ground. The previous #6EA8D8 was
  /// close enough to grey that the screen had no colour in it at all.
  static let accent = Color(red: 0.302, green: 0.639, blue: 1.0)      // #4DA3FF
  static let accentSoft = Color(red: 0.514, green: 0.761, blue: 1.0)  // #83C2FF
  static let accentDeep = Color(red: 0.149, green: 0.404, blue: 0.769) // #2667C4

  /// Green means running. Red means late. Nothing else is coloured, so that
  /// when something is, it means something.
  static let running = Color(red: 0.325, green: 0.847, blue: 0.588)
  static let overdue = Color(red: 0.984, green: 0.353, blue: 0.376)

  static let hairline = Color.white.opacity(0.10)
  static let hairlineBright = Color.white.opacity(0.22)

  // MARK: type — all Dynamic Type

  static let screenTitle = Font.largeTitle.weight(.bold)
  static let cardTitle = Font.title3.weight(.semibold)
  static let heading = Font.headline
  static let body = Font.body
  static let detail = Font.subheadline
  static let label = Font.footnote.weight(.medium)
  static let mono = Font.system(.footnote, design: .monospaced).weight(.medium)
}

// MARK: - the ambient field

/// The background knows what time it is.
///
/// Argon watches from four until midnight on school nights and is off duty
/// otherwise. Rather than print that, the room behind the glass says it: warm
/// and lit while he is being watched over, cold and still when Argon is
/// resting.
private struct ArgonAmbience: View {
  var evening: Double
  var resting: Bool

  var body: some View {
    GeometryReader { geo in
      let w = geo.size.width, h = geo.size.height
      ZStack {
        LinearGradient(colors: [Argon.Ink.deep, Argon.Ink.base],
                       startPoint: .top, endPoint: .bottom)

        bloom(top).frame(width: w * 1.5, height: w * 1.5)
          .position(x: w * 0.18, y: h * (0.10 + 0.05 * evening))
          .blur(radius: 60)

        bloom(bottom).frame(width: w * 1.3, height: w * 1.3)
          .position(x: w * 0.88, y: h * (0.78 - 0.06 * evening))
          .blur(radius: 70)
      }
      // Sized to the view it decorates and clipped to it, so it can never
      // push the content sideways.
      .frame(width: w, height: h)
      .clipped()
    }
    .ignoresSafeArea()
    .allowsHitTesting(false)
    .animation(.easeInOut(duration: 1.2), value: resting)
  }

  private func bloom(_ colour: Color) -> some View {
    Circle().fill(RadialGradient(colors: [colour, colour.opacity(0)],
                                 center: .center, startRadius: 0, endRadius: 320))
  }

  private var top: Color {
    resting ? Argon.Ink.slate.opacity(0.7)
            : Argon.accentDeep.opacity(0.55 - 0.10 * evening)
  }

  private var bottom: Color {
    resting ? Argon.Ink.deep.opacity(0.8)
            : Color(red: 0.180, green: 0.267, blue: 0.561).opacity(0.50 + 0.10 * evening)
  }
}

extension View {
  /// Put the ambient field behind this view. A `background` never changes the
  /// size of what it sits behind — which is the whole point.
  func argonAmbience(ticking: Bool, date: Date = Date()) -> some View {
    let hour = Calendar.current.component(.hour, from: date)
    let evening = min(max(Double(hour - 16) / 8.0, 0), 1)
    return background(ArgonAmbience(evening: evening, resting: !ticking))
  }
}

// MARK: - glass

/// A pane of glass. One radius, one border, one shadow, everywhere.
struct ArgonGlass<Content: View>: View {
  var tint: Color = Argon.accent
  var padding: CGFloat = 18
  @ViewBuilder var content: Content

  var body: some View {
    content
      .padding(padding)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background {
        RoundedRectangle(cornerRadius: 22, style: .continuous)
          .fill(.regularMaterial)
          .overlay {
            // A blue wash, so glass over a navy ground still reads as blue
            // rather than as grey plastic.
            RoundedRectangle(cornerRadius: 22, style: .continuous)
              .fill(LinearGradient(
                colors: [tint.opacity(0.22), tint.opacity(0.06)],
                startPoint: .topLeading, endPoint: .bottomTrailing))
          }
          .overlay {
            // A brighter top edge is what makes a rectangle read as glass:
            // it is the light catching the lip.
            RoundedRectangle(cornerRadius: 22, style: .continuous)
              .strokeBorder(LinearGradient(
                colors: [Argon.hairlineBright, Argon.hairline],
                startPoint: .top, endPoint: .bottom), lineWidth: 1)
          }
          // Black for depth, blue for life. The blue one is what stops a card
          // reading as grey plastic on a slate ground.
          .shadow(color: .black.opacity(0.55), radius: 22, y: 12)
          .shadow(color: tint.opacity(0.28), radius: 18, y: 4)
      }
      .environment(\.colorScheme, .dark)
  }
}

extension View {
  /// Light coming out of the surface, not a drop shadow under it.
  ///
  /// This is the quality the logo has and a flat card does not: a lit object
  /// on a dark ground, with the falloff doing the work. Two shadows — a tight
  /// bright one and a wide soft one — because a single radius reads as blur
  /// rather than as glow.
  func argonGlow(_ colour: Color = Argon.accent, strength: Double = 1) -> some View {
    shadow(color: colour.opacity(0.45 * strength), radius: 10 * strength)
      .shadow(color: colour.opacity(0.22 * strength), radius: 28 * strength)
  }
}

/// The soft radial light the logo is built from. Sits behind a heading or an
/// empty state so the screen has a source of light rather than flat fills.
struct ArgonBloom: View {
  var colour: Color = Argon.accent
  var size: CGFloat = 260
  var opacity: Double = 0.55

  var body: some View {
    Circle()
      .fill(RadialGradient(
        colors: [colour.opacity(opacity), colour.opacity(opacity * 0.35), .clear],
        center: .center, startRadius: 0, endRadius: size / 2))
      .frame(width: size, height: size)
      .blur(radius: 26)
      .allowsHitTesting(false)
  }
}

struct ArgonDivider: View {
  var body: some View { Rectangle().fill(Argon.hairline).frame(height: 1) }
}

/// A status word: a due date, a period, a count.
struct ArgonPill: View {
  let text: String
  var colour: Color = Argon.accentSoft

  var body: some View {
    Text(text)
      .font(Argon.label)
      .foregroundStyle(colour)
      .padding(.horizontal, 10).padding(.vertical, 5)
      .background(Capsule().fill(colour.opacity(0.20)))
      .overlay(Capsule().strokeBorder(colour.opacity(0.45), lineWidth: 1))
      .shadow(color: colour.opacity(0.35), radius: 8)
      .fixedSize(horizontal: true, vertical: false)
  }
}

/// A quiet pulse, for the one thing that is genuinely live.
struct ArgonPulse: View {
  var colour: Color = Argon.running
  @State private var on = false

  var body: some View {
    Circle()
      .fill(colour)
      .frame(width: 9, height: 9)
      .shadow(color: colour.opacity(0.9), radius: 7)
      .overlay(Circle().stroke(colour.opacity(on ? 0 : 0.7), lineWidth: on ? 9 : 0))
      .opacity(on ? 0.8 : 1)
      .animation(.easeOut(duration: 1.8).repeatForever(autoreverses: false), value: on)
      .onAppear { on = true }
  }
}
