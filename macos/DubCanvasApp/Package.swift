// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DubCanvasApp",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "DubCanvasApp", targets: ["DubCanvasApp"])
    ],
    targets: [
        .executableTarget(
            name: "DubCanvasApp",
            path: "Sources/DubCanvasApp",
            linkerSettings: [
                .linkedFramework("Security")
            ]
        ),
        .testTarget(name: "DubCanvasAppTests", dependencies: ["DubCanvasApp"])
    ]
)
