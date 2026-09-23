import SwiftUI

class ThemeManager: ObservableObject {
  static let shared = ThemeManager()

  /// One colour. Foqos shipped twenty — Forest Green, Hot Pink, Electric
  /// Lemonade — and each one repainted the whole Focus tab. The app has one
  /// blue now (`Argon.accent`), so the choice is gone; a stored name from the
  /// old list no longer matches and falls back to this.
  ///
  /// Hex rather than `Argon.accent`: the shield extension compiles this file
  /// without `ArgonTheme.swift`.
  static let availableColors: [(name: String, color: Color)] = [
    ("Argon Blue", Color(hex: "#5B93F5"))
  ]

  private static let defaultColorName = "Argon Blue"

  @AppStorage(
    "foqosThemeColorName", store: UserDefaults(suiteName: "group.com.niranjanj.argon"))
  private var themeColorName: String = defaultColorName

  var selectedColorName: String {
    get { themeColorName }
    set {
      themeColorName = newValue
      objectWillChange.send()
    }
  }

  var themeColor: Color {
    Self.availableColors.first(where: { $0.name == themeColorName })?.color
      ?? Self.availableColors.first!.color
  }

  func setTheme(named name: String) {
    selectedColorName = name
  }
}

extension Color {
  init(hex: String) {
    let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
    var int: UInt64 = 0
    Scanner(string: hex).scanHexInt64(&int)
    let a: UInt64
    let r: UInt64
    let g: UInt64
    let b: UInt64
    switch hex.count {
    case 3:  // RGB (12-bit)
      (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
    case 6:  // RGB (24-bit)
      (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
    case 8:  // ARGB (32-bit)
      (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
    default:
      (a, r, g, b) = (1, 1, 1, 0)
    }

    self.init(
      .sRGB,
      red: Double(r) / 255,
      green: Double(g) / 255,
      blue: Double(b) / 255,
      opacity: Double(a) / 255
    )
  }

  func toHex() -> String? {
    let uiColor = UIColor(self)
    var r: CGFloat = 0
    var g: CGFloat = 0
    var b: CGFloat = 0
    var a: CGFloat = 0

    guard uiColor.getRed(&r, green: &g, blue: &b, alpha: &a) else {
      return nil
    }

    let rgb: Int = (Int)(r * 255) << 16 | (Int)(g * 255) << 8 | (Int)(b * 255) << 0

    return String(format: "#%06x", rgb)
  }
}
