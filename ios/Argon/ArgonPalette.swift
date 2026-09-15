import SwiftUI

/// The palette the Foqos screens already speak.
///
/// v1's Argon theme quietly became the whole app's theme — `ArgonPalette` is
/// referenced 164 times across Foqos's own views, so deleting the v1 Argon
/// layer would break files that have nothing to do with Argon.
///
/// Rather than edit twenty-three unrelated files, this keeps the exact API and
/// re-points it at the new slate system. Deleting the old layer then costs
/// nothing, and Foqos's screens pick up the overhaul for free instead of
/// sitting next to it in a different blue.
///
/// Every name here is load-bearing somewhere in Foqos. Do not rename one
/// without grepping the app target first.
enum ArgonPalette {
  // Grounds — cooler and deeper than v1's, so glass has something to refract.
  static let canvas = Argon.Ink.deep
  static let canvasLifted = Argon.Ink.slate
  static let surface = Argon.Ink.slate
  static let surfaceRaised = Argon.Ink.raised
  static let hairline = Color.white.opacity(0.10)

  // Accents. v1 had four blues doing similar jobs; they now resolve onto the
  // one steel blue plus a dimmer partner, which is what stops a slate UI
  // turning into a light show.
  static let electricBlue = Argon.accent
  static let iceBlue = Color(red: 0.561, green: 0.761, blue: 0.898)
  static let cobalt = Argon.accentDim
  static let cyan = Color(red: 0.388, green: 0.784, blue: 0.910)

  // Ink.
  static let ink = Argon.Text.primary
  static let mutedInk = Argon.Text.secondary

  // States.
  static let warning = Color(red: 0.929, green: 0.635, blue: 0.298)
  static let danger = Argon.overdue
}

/// v1's widget palette, same treatment — the widget target reads it 21 times.
enum ArgonWidgetPalette {
  static let canvas = Argon.Ink.void
  static let surface = Argon.Ink.slate
  static let accent = Argon.accent
  static let ink = Argon.Text.primary
  static let mutedInk = Argon.Text.secondary
  static let danger = Argon.overdue
  static let running = Argon.running
}
