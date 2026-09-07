import Foundation

struct Options {
    let title: String
    let body: String
    let command: String
    let timeout: TimeInterval
}

final class Delegate: NSObject, NSUserNotificationCenterDelegate {
    let command: String

    init(command: String) {
        self.command = command
    }

    func userNotificationCenter(_ center: NSUserNotificationCenter, shouldPresent notification: NSUserNotification) -> Bool {
        return true
    }

    func userNotificationCenter(_ center: NSUserNotificationCenter, didActivate notification: NSUserNotification) {
        if notification.activationType == .contentsClicked {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sh")
            process.arguments = ["-lc", command]
            try? process.run()
        }
        CFRunLoopStop(CFRunLoopGetMain())
    }
}

func parseOptions() -> Options? {
    var title = ""
    var body = ""
    var command = ""
    var timeout = 120.0
    var iterator = CommandLine.arguments.dropFirst().makeIterator()
    while let key = iterator.next() {
        guard let value = iterator.next() else { return nil }
        switch key {
        case "--title":
            title = value
        case "--body":
            body = value
        case "--command":
            command = value
        case "--timeout":
            timeout = Double(value) ?? timeout
        default:
            return nil
        }
    }
    if title.isEmpty || body.isEmpty || command.isEmpty {
        return nil
    }
    return Options(title: title, body: body, command: command, timeout: timeout)
}

guard let options = parseOptions() else {
    exit(2)
}

let delegate = Delegate(command: options.command)
let center = NSUserNotificationCenter.default
center.delegate = delegate

let notification = NSUserNotification()
notification.title = options.title
notification.informativeText = options.body
center.deliver(notification)

RunLoop.current.run(until: Date(timeIntervalSinceNow: options.timeout))
