import WidgetKit

/// Tells everything outside the app that what it is showing has gone out of
/// date.
///
/// Three processes need this - the app, the tunnel, and the Control Center
/// toggle - and the widget and the control are reloaded by two different calls,
/// one of which exists only from iOS 18. Saying that once beats saying it three
/// times and having one of them drift.
///
/// It carries no information. The status file is still the whole story; this
/// only says "read it again".
enum Displays {

    static func refresh() {
        WidgetCenter.shared.reloadAllTimelines()

        if #available(iOS 18.0, *) {
            ControlCenter.shared.reloadAllControls()
        }
    }
}
