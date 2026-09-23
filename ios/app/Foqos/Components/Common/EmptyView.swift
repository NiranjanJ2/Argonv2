import SwiftUI

struct EmptyView: View {
  let iconName: String
  let headingText: String

  var body: some View {
    VStack {
      Spacer()

      Image(systemName: iconName)
        .resizable()
        .aspectRatio(contentMode: .fit)
        .frame(width: 44, height: 44)
        .foregroundStyle(Argon.Tone.faint)

      Text(headingText)
        .font(Argon.body)
        .multilineTextAlignment(.center)
        .foregroundStyle(Argon.Tone.secondary)
        .padding()

      Spacer()
    }
  }
}

#Preview {
  EmptyView(iconName: "tray", headingText: "No items in your list")
}
