// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "nhannhtdormwifihack",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [
        .executable(
            name: "nhannhtdormwifihack",
            targets: ["nhannhtdormwifihackCLI"]
        ),
        .library(
            name: "nhannhtdormwifihackKit",
            targets: ["nhannhtdormwifihackKit"]
        ),
        // The menu bar app. macOS only; its sources compile to nothing
        // elsewhere, so the package still builds for iOS.
        .executable(
            name: "nhannhtdormwifihack-menubar",
            targets: ["nhannhtdormwifihackMenuBar"]
        ),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0")
    ],
    targets: [
        // The whole login and scheduling engine. No platform UI, so this builds
        // for iOS untouched if a phone app ever wants it.
        .target(name: "nhannhtdormwifihackKit"),

        .executableTarget(
            name: "nhannhtdormwifihackCLI",
            dependencies: [
                "nhannhtdormwifihackKit",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ]
        ),

        .executableTarget(
            name: "nhannhtdormwifihackMenuBar",
            dependencies: ["nhannhtdormwifihackKit"]
        ),

        .testTarget(
            name: "nhannhtdormwifihackKitTests",
            dependencies: ["nhannhtdormwifihackKit"]
        ),
    ]
)
