import SwiftUI
import WidgetKit

/// The two things this app puts outside itself.
///
/// A widget for the Lock Screen and Home Screen, which reports, and a Control
/// Center toggle, which acts. Neither is decoration: the whole point of the
/// tunnel is that the app is closed, so everything the user learns and every
/// button they press while it is working has to be somewhere other than the
/// app's own screen.
@main
struct DormWiFiWidgets: WidgetBundle {

    @WidgetBundleBuilder
    var body: some Widget {
        SessionWidget()

        // Control Center controls arrived in iOS 18. The app itself still runs
        // on 17, so on 17 this simply is not offered.
        if #available(iOS 18.0, *) {
            TunnelControl()
        }
    }
}
