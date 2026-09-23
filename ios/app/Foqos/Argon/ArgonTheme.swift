import SwiftUI

/// The look: a drawing sheet. Blue-black ground, fine linework, one blue.
///
/// This replaced a navy "glass" theme — blooms behind titles, gradient washes
/// on every card, glows under buttons, a light field that moved with the hour.
/// Each piece was defensible alone; together they read as generated. His brief
/// for the replacement (2026-09-22): intentional and minimal, with lines —
/// "draft lines extending past boxes"; blue as the main colour and a blue tint
/// for general surfaces, not in the AI-looking way; no bright reds or greens;
/// red only as a tint over a red.
///
/// **The one bold thing is the linework.** Boxes are outlines, not fills, and
/// their edges run a few points past the corners like construction lines on a
/// technical drawing (`DraftFrame`). Everything else stays quiet so that reads.
///
/// **Blue carries all the colour.** Things to press, today, a task running.
/// There is no green.
///
/// **Red is a tint, never a fill.** Soft red text on a faint red wash — late
/// work and failures only.
///
/// **No decoration.** No gradients, shadows, blur or glow. Square corners.
///
/// **Type is Dynamic Type.** Titles use SF Pro's expanded width, which has the
/// flat, even set of drawing-sheet lettering; everything else is plain SF Pro.
enum Argon {

  // MARK: ground

  enum Ink {
    static let base = Color(red: 0.039, green: 0.051, blue: 0.071)   // #0A0D12
    static let deep = base
    /// A barely-there fill, for the few places a surface must read as pressed.
    static let slate = Color(red: 0.063, green: 0.078, blue: 0.106)  // #10141B
    static let raised = Color(red: 0.094, green: 0.114, blue: 0.153) // #181D27
  }

  enum Tone {
    static let primary = Color(red: 0.902, green: 0.922, blue: 0.949)   // #E6EBF2
    static let secondary = Color(red: 0.553, green: 0.596, blue: 0.667) // #8D98AA
    /// 4.5:1 on the ground — subject lines, empty states.
    static let faint = Color(red: 0.463, green: 0.510, blue: 0.584)     // #768295
  }

  /// Construction lines: the box edges and the rules between rows.
  static let line = Color(red: 0.149, green: 0.188, blue: 0.275)        // #263046
  /// A box edge he should notice — the brief, the running task.
  static let lineStrong = Color(red: 0.204, green: 0.259, blue: 0.353)  // #34425A

  static let accent = Color(red: 0.357, green: 0.576, blue: 0.961)      // #5B93F5
  static let accentSoft = accent
  static let accentDeep = accent

  /// A task running. Blue, not green.
  static let running = accent
  /// Late work and failures. Only ever shown over its own tint.
  static let overdue = Color(red: 0.910, green: 0.455, blue: 0.431)     // #E8746E

  static let hairline = line
  static let hairlineBright = lineStrong

  /// How far a construction line runs past the corner it draws.
  static let overshoot: CGFloat = 9
  /// The gap between the screen edge and a box's vertical lines.
  static let margin: CGFloat = 18

  // MARK: type — all Dynamic Type

  static let screenTitle = Font.largeTitle.weight(.semibold).width(.expanded)
  static let cardTitle = Font.headline
  static let heading = Font.headline
  static let body = Font.body
  static let detail = Font.subheadline
  static let label = Font.footnote
  /// Section names: small, wide, quiet. Sentence case — never tracked capitals.
  static let caption = Font.footnote.weight(.medium).width(.expanded)
  static let mono = Font.footnote.monospacedDigit()
}

extension View {
  /// The screen's ground.
  func argonAmbience() -> some View {
    background(Argon.Ink.base.ignoresSafeArea())
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
    var p = Path()
    let o = overshoot
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
      ZStack {
        Rectangle().fill(fill).frame(width: box.width, height: box.height)
          .position(x: box.midX, y: box.midY)
        Path { p in
          // sides, extended past the box only where the box ends
          let top = position.opens ? 0 : box.minY
          let bottom = position.closes ? g.size.height : box.maxY
          p.move(to: CGPoint(x: box.minX, y: top)); p.addLine(to: CGPoint(x: box.minX, y: bottom))
          p.move(to: CGPoint(x: box.maxX, y: top)); p.addLine(to: CGPoint(x: box.maxX, y: bottom))
          if position.opens {
            p.move(to: CGPoint(x: box.minX - o, y: box.minY))
            p.addLine(to: CGPoint(x: box.maxX + o, y: box.minY))
          }
          if position.closes {
            p.move(to: CGPoint(x: box.minX - o, y: box.maxY))
            p.addLine(to: CGPoint(x: box.maxX + o, y: box.maxY))
          } else {
            // the rule between rows stays inside the box
            p.move(to: CGPoint(x: box.minX, y: box.maxY))
            p.addLine(to: CGPoint(x: box.maxX, y: box.maxY))
          }
        }
        .stroke(stroke, lineWidth: 1)
      }
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
  @ViewBuilder var content: Content

  var body: some View {
    content
      .padding(padding)
      .frame(maxWidth: .infinity, alignment: .leading)
      .overlay(DraftFrame().stroke(stroke, lineWidth: 1))
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
      .foregroundStyle(Argon.accent)
      .frame(maxWidth: .infinity, minHeight: 48)
      .background(prominent ? Argon.accent.opacity(configuration.isPressed ? 0.22 : 0.12)
                            : Color.clear)
      .overlay(DraftFrame(overshoot: 6).stroke(Argon.accent.opacity(0.7), lineWidth: 1))
      .padding(6)
  }
}
