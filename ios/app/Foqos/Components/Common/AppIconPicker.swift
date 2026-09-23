import SwiftUI

struct AppIconPicker: View {
  let selectionColor: Color

  @State private var selectedIconName = UIApplication.shared.alternateIconName
  @State private var isChangingIcon = false
  @State private var showError = false
  @State private var errorMessage = ""

  private let columns = Array(
    repeating: GridItem(.flexible(), spacing: 12),
    count: 3
  )

  /// Two List rows: the section's name, then one draft box holding the grid.
  /// Must sit inside a plain `List`.
  var body: some View {
    Text("App icon").font(Argon.caption).foregroundStyle(Argon.Tone.secondary)
      .draftLabelRow()

    VStack(alignment: .leading, spacing: 8) {
      LazyVGrid(columns: columns, spacing: 12) {
        ForEach(AppIconOption.allCases) { icon in
          Button {
            select(icon)
          } label: {
            VStack(spacing: 8) {
              Image(icon.previewAssetName)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 64, height: 64)
                // The icon keeps its own squircle — it is the artwork, not
                // chrome. The selection is a square drawn around it.
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .padding(5)
                .overlay {
                  Rectangle().strokeBorder(
                    isSelected(icon) ? Argon.accent : Color.clear, lineWidth: 1)
                }

              Text(icon.displayName)
                .font(Argon.label)
                .foregroundStyle(isSelected(icon) ? Argon.accent : Argon.Tone.secondary)
                .lineLimit(1)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          .disabled(isChangingIcon || isSelected(icon))
          .accessibilityLabel("Use the \(icon.displayName) app icon")
          .accessibilityAddTraits(isSelected(icon) ? .isSelected : [])
        }
      }

      if !UIApplication.shared.supportsAlternateIcons {
        Text("Alternate app icons are not available on this device.")
          .font(Argon.label)
          .foregroundStyle(Argon.Tone.faint)
      }
    }
    .padding(.vertical, 12)
    .draftRow(.single)
    .alert("Unable to Change App Icon", isPresented: $showError) {
      Button("OK") {}
    } message: {
      Text(errorMessage)
    }
    .onAppear {
      selectedIconName = UIApplication.shared.alternateIconName
    }
  }

  private func isSelected(_ icon: AppIconOption) -> Bool {
    selectedIconName == icon.alternateIconName
  }

  private func select(_ icon: AppIconOption) {
    guard UIApplication.shared.supportsAlternateIcons else {
      errorMessage = "Alternate app icons are not available on this device."
      showError = true
      return
    }

    isChangingIcon = true
    UIApplication.shared.setAlternateIconName(icon.alternateIconName) { error in
      Task { @MainActor in
        isChangingIcon = false

        if let error {
          errorMessage = error.localizedDescription
          showError = true
          selectedIconName = UIApplication.shared.alternateIconName
          return
        }

        selectedIconName = UIApplication.shared.alternateIconName
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
      }
    }
  }
}

private enum AppIconOption: String, CaseIterable, Identifiable {
  case defaultIcon
  case blue
  case cat
  case pop
  case original

  var id: String { rawValue }

  var displayName: String {
    switch self {
    case .defaultIcon: "Default"
    case .blue: "Blue"
    case .cat: "Cat"
    case .pop: "Pop"
    case .original: "Original"
    }
  }

  var alternateIconName: String? {
    switch self {
    case .defaultIcon: nil
    case .blue: "foqos-icon-blue"
    case .cat: "foqos-icon-cat"
    case .pop: "foqos-icon-pop"
    case .original: "foqos-icon-original"
    }
  }

  var previewAssetName: String {
    switch self {
    case .defaultIcon: "AppIconDefaultPreview"
    case .blue: "AppIconBluePreview"
    case .cat: "AppIconCatPreview"
    case .pop: "AppIconPopPreview"
    case .original: "AppIconOriginalPreview"
    }
  }
}

#Preview {
  List {
    AppIconPicker(selectionColor: Argon.accent)
  }
}
