import SwiftUI

/// A quiet monochrome surface, with SF typography and a subtle dot grid.
enum Argon {

  // MARK: ground

  enum Ink {
    static let base = Color(white: 0.035)
    static let deep = base
    /// A barely-there fill, for the few places a surface must read as pressed.
    static let slate = Color(white: 0.085)
    static let raised = Color(white: 0.13)
  }

  enum Tone {
    static let primary = Color.white
    static let secondary = Color(white: 0.75)
    /// 4.5:1 on the ground — subject lines, empty states.
    static let faint = Color(white: 0.65)
  }

  /// Construction lines: the box edges and the rules between rows.
  static let line = Color(white: 0.18)
  /// A box edge he should notice — the brief, the running task.
  static let lineStrong = Color(white: 0.28)

  static let accent = Color.white
  static let accentSoft = accent
  static let accentDeep = accent

  /// A task running. Blue, not green.
  static let running = accent
  /// Late work and failures. Only ever shown over its own tint.
  static let overdue = Color(white: 0.85)

  static let hairline = line
  static let hairlineBright = lineStrong

  /// How far a construction line runs past the corner it draws.
  static let overshoot: CGFloat = 0
  /// The gap between the screen edge and a box's vertical lines.
  static let margin: CGFloat = 18

  // MARK: type — all Dynamic Type

  static let screenTitle = Font.largeTitle.weight(.semibold)
  static let cardTitle = Font.headline
  static let heading = Font.headline
  static let body = Font.body
  static let detail = Font.subheadline
  static let label = Font.footnote
  /// Section names: small, wide, quiet. Sentence case — never tracked capitals.
  static let caption = Font.footnote.weight(.medium)
  static let mono = Font.footnote.monospacedDigit()
}

extension View {
  /// The screen's ground.
  func argonAmbience() -> some View {
    background {
      ArgonDotField().ignoresSafeArea()
    }
  }
}

struct ArgonDotField: View {
  var body: some View {
    Canvas { context, size in
      context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(Argon.Ink.base))
      var dots = Path()
      let spacing: CGFloat = 15
      stride(from: 0.0, through: size.height + spacing, by: spacing).forEach { y in
        let progress = y / max(size.height, 1)
        let center = size.width * (1.08 - 0.20 * sin(progress * .pi))
        let strength = max(0, min(1, (progress - 0.48) * 3))
        stride(from: -spacing, through: size.width + spacing, by: spacing).forEach { x in
          let distance = (x - center) / max(size.width * 0.35, 1)
          let wave = exp(-distance * distance * 2) * strength
          let diameter = 0.6 + 3.2 * wave
          let shift = sin(progress * .pi * 2) * 6
          dots.addEllipse(in: CGRect(x: x + shift - diameter / 2, y: y - diameter / 2,
                                     width: diameter, height: diameter))
        }
      }
      context.fill(dots, with: .color(.white.opacity(0.18)))
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }
}

// MARK: - linework

/// Four edges of a rectangle, each running `overshoot` past both corners.
///
/// Stroke it at 1pt. Drawn as four separate lines rather than a rectangle so
/// the ends can pass each other — that crossing at every corner is the whole
/// look.
struct DraftFrame: Shape {
  var overshoot: CGFloat = Argon.overshoot
  var edges: Edge.Set = .all

  func path(in r: CGRect) -> Path {
    if edges == .all {
      return RoundedRectangle(cornerRadius: 12).path(in: r)
    }
    var p = Path()
    let o: CGFloat = 0
    if edges.contains(.top) {
      p.move(to: CGPoint(x: r.minX - o, y: r.minY)); p.addLine(to: CGPoint(x: r.maxX + o, y: r.minY))
    }
    if edges.contains(.bottom) {
      p.move(to: CGPoint(x: r.minX - o, y: r.maxY)); p.addLine(to: CGPoint(x: r.maxX + o, y: r.maxY))
    }
    let top = edges.contains(.top) ? r.minY - o : r.minY
    let bottom = edges.contains(.bottom) ? r.maxY + o : r.maxY
    if edges.contains(.leading) {
      p.move(to: CGPoint(x: r.minX, y: top)); p.addLine(to: CGPoint(x: r.minX, y: bottom))
    }
    if edges.contains(.trailing) {
      p.move(to: CGPoint(x: r.maxX, y: top)); p.addLine(to: CGPoint(x: r.maxX, y: bottom))
    }
    return p
  }
}

/// Where a row sits in its box, so a stack of List rows can draw one box.
enum DraftPosition {
  case single, first, middle, last

  static func of(_ index: Int, in count: Int) -> DraftPosition {
    if count <= 1 { return .single }
    return index == 0 ? .first : index == count - 1 ? .last : .middle
  }

  var opens: Bool { self == .single || self == .first }
  var closes: Bool { self == .single || self == .last }
}

/// One List row's share of a draft box: the side lines, the top edge if it
/// opens the box, the bottom edge if it closes it, and a rule between rows.
///
/// Everything is drawn *inside* the row's own rect — List cells are not a
/// safe place to draw past their bounds — so the box sits `margin` in from the
/// screen edge and the overshoot lives in that margin.
struct DraftRowBackground: View {
  let position: DraftPosition
  var stroke: Color = Argon.line
  var fill: Color = .clear

  var body: some View {
    GeometryReader { g in
      let o = Argon.overshoot, m = Argon.margin
      let box = CGRect(x: m, y: position.opens ? o : 0,
                       width: g.size.width - 2 * m,
                       height: g.size.height - (position.opens ? o : 0) - (position.closes ? o : 0))
      UnevenRoundedRectangle(
        topLeadingRadius: position.opens ? 16 : 0,
        bottomLeadingRadius: position.closes ? 16 : 0,
        bottomTrailingRadius: position.closes ? 16 : 0,
        topTrailingRadius: position.opens ? 16 : 0)
        .fill(fill == .clear ? Argon.Ink.slate : fill)
        .overlay(alignment: .bottom) {
          if !position.closes {
            Rectangle().fill(stroke.opacity(0.65)).frame(height: 0.5).padding(.leading, 14)
          }
        }
        .frame(width: box.width, height: box.height)
        .position(x: box.midX, y: box.midY)
    }
  }
}

extension View {
  /// Make a List row part of a draft box. Content is inset past the box edge,
  /// and the rows that open or close the box leave room for the overshoot.
  func draftRow(_ position: DraftPosition, stroke: Color = Argon.line,
                fill: Color = .clear) -> some View {
    let o = Argon.overshoot, inset = Argon.margin + 14
    return listRowBackground(DraftRowBackground(position: position, stroke: stroke, fill: fill))
      .listRowSeparator(.hidden)
      .listRowInsets(EdgeInsets(top: position.opens ? o : 0, leading: inset,
                                bottom: position.closes ? o : 0, trailing: inset))
  }

  /// A List row with no box: a section's name, the screen title.
  func draftLabelRow(top: CGFloat = 18) -> some View {
    listRowBackground(Color.clear)
      .listRowSeparator(.hidden)
      .listRowInsets(EdgeInsets(top: top, leading: Argon.margin, bottom: 6,
                                trailing: Argon.margin))
  }
}

// MARK: - pieces

/// A free-standing draft box, for screens that are not lists.
struct ArgonGlass<Content: View>: View {
  var padding: CGFloat = 16
  var stroke: Color = Argon.line
  var surface: Color = Argon.Ink.slate
  @ViewBuilder var content: Content

  var body: some View {
    content
      .padding(padding)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(surface, in: RoundedRectangle(cornerRadius: 16))
      .padding(Argon.overshoot)   // room for the overshoot, inside our frame
  }
}

struct ArgonDivider: View {
  var body: some View { Rectangle().fill(Argon.line).frame(height: 1) }
}

/// A status word: a due date, a count.
///
/// Plain text by default. `tinted` puts the word on a faint wash of its own
/// colour — how red appears at all, and how "today" is marked in blue.
struct ArgonPill: View {
  let text: String
  var colour: Color = Argon.Tone.secondary
  var tinted = false

  var body: some View {
    Text(text)
      .font(Argon.label.monospacedDigit())
      .foregroundStyle(colour)
      .padding(.horizontal, tinted ? 6 : 0).padding(.vertical, tinted ? 2 : 0)
      .background(tinted ? colour.opacity(0.14) : .clear)
      .fixedSize(horizontal: true, vertical: false)
  }
}

/// A task running: a small filled blue square — the checkbox's own shape, lit.
struct ArgonPulse: View {
  var colour: Color = Argon.running

  var body: some View {
    Rectangle().fill(colour).frame(width: 8, height: 8).accessibilityHidden(true)
  }
}

/// The checkbox: a square, as on a form. Filled blue with a tick when done.
struct DraftCheck: View {
  let on: Bool

  var body: some View {
    ZStack {
      Rectangle().strokeBorder(on ? Argon.accent : Argon.Tone.faint, lineWidth: 1)
      if on {
        Rectangle().fill(Argon.accent.opacity(0.18))
        Image(systemName: "checkmark").font(.caption2.weight(.bold))
          .foregroundStyle(Argon.accent)
      }
    }
    .frame(width: 18, height: 18)
  }
}

/// An outlined button with draft corners. `prominent` adds a blue wash.
struct ArgonButtonStyle: ButtonStyle {
  var prominent = true

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(Argon.body.weight(.medium))
      .foregroundStyle(prominent ? Argon.Ink.base : Argon.Tone.primary)
      .frame(maxWidth: .infinity, minHeight: 48)
      .background(prominent ? Argon.accent.opacity(configuration.isPressed ? 0.75 : 1)
                            : Argon.Ink.slate, in: RoundedRectangle(cornerRadius: 12))
      .overlay(RoundedRectangle(cornerRadius: 12).stroke(Argon.lineStrong, lineWidth: 1))
  }
}
