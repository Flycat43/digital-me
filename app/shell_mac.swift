// digital-me 桌宠外壳（macOS）
// 零第三方依赖：Cocoa + WebKit 直接起一个「无边框 / 透明 / 常驻最前」的窗口，
// 里面加载本地 server.py 提供的页面。
//
// 编译：swiftc -O -framework Cocoa -framework WebKit shell_mac.swift -o bin/digital-me-pet
// 运行：./bin/digital-me-pet            （需要先启动 server.py）

import Cocoa
import WebKit

func dlog(_ s: String) { FileHandle.standardError.write((s+"\n").data(using: .utf8)!) }

let PORT = ProcessInfo.processInfo.environment["DM_PORT"] ?? "8765"
let QUERY = ProcessInfo.processInfo.environment["DM_QUERY"] ?? ""
let URL_STR = "http://127.0.0.1:\(PORT)/\(QUERY.isEmpty ? "" : "?" + QUERY)"

final class DragView: NSView {
    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ note: Notification) {
        dlog("didFinishLaunching")
        let cfg = WKWebViewConfiguration()
        let ucc = WKUserContentController()
        ucc.add(self, name: "pet")
        cfg.userContentController = ucc

        dlog("cfg ok")
        web = WKWebView(frame: .zero, configuration: cfg)
        dlog("webview ok")
        // 透明底（macOS 12+ 公开 API；旧的 setValue("drawsBackground") 在 macOS 27 上会卡死）
        web.underPageBackgroundColor = .clear
        web.wantsLayer = true
        web.layer?.backgroundColor = NSColor.clear.cgColor

        let rect = NSRect(x: 0, y: 0, width: 400, height: 620)
        dlog("window try")
        window = NSWindow(contentRect: rect,
                          styleMask: [.borderless],
                          backing: .buffered,
                          defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = .floating                       // 永远在最前
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isMovableByWindowBackground = true
        window.contentView = web

        dlog("window ready")
        // 右下角落位
        if let screen = NSScreen.main {
            let vf = screen.visibleFrame
            window.setFrameOrigin(NSPoint(x: vf.maxX - rect.width - 40,
                                          y: vf.minY + 40))
        }

        // 鼠标按下角色/标题栏 → 原生拖动（WKWebView 会吞掉背景拖动事件）
        let js = """
        (function(){
          function hook(sel){
            document.querySelectorAll(sel).forEach(function(el){
              el.addEventListener('mousedown', function(e){
                if (e.button !== 0) return;
                if (e.target.closest('input,button,textarea')) return;
                window.webkit.messageHandlers.pet.postMessage('drag');
              });
            });
          }
          function boot(){
            hook('#pet svg, #panelHead');
          }
          if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', boot);
          } else { boot(); }
        })();
        """
        let script = WKUserScript(source: js, injectionTime: .atDocumentEnd, forMainFrameOnly: true)
        cfg.userContentController.addUserScript(script)

        dlog("loading \(URL_STR)")
        web.load(URLRequest(url: URL(string: URL_STR)!))
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // 截图/录帧：不依赖系统的「屏幕录制」权限，直接把窗口内容渲染成 PNG
        let env = ProcessInfo.processInfo.environment
        if let snapDir = env["DM_SNAP_DIR"] {
            let interval = Double(env["DM_SNAP_INTERVAL"] ?? "0.5") ?? 0.5
            let maxFrames = Int(env["DM_SNAP_FRAMES"] ?? "20") ?? 20
            FileHandle.standardError.write("snapshot → \(snapDir) every \(interval)s x\(maxFrames)\n".data(using: .utf8)!)
            var idx = 0
            Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { timer in
                if idx >= maxFrames { timer.invalidate(); return }
                let n = idx
                self.web.takeSnapshot(with: nil) { image, err in
                    if err != nil { FileHandle.standardError.write("snap err: \(err!)\n".data(using: .utf8)!) }
                    guard let image,
                          let tiff = image.tiffRepresentation,
                          let rep = NSBitmapImageRep(data: tiff),
                          let png = rep.representation(using: .png, properties: [:]) else {
                        FileHandle.standardError.write("snap \(n) failed\n".data(using: .utf8)!)
                        return
                    }
                    let url = URL(fileURLWithPath: snapDir)
                        .appendingPathComponent(String(format: "frame_%03d.png", n))
                    do { try png.write(to: url) } catch {
                        FileHandle.standardError.write("snap write \(url.path) failed: \(error)\n".data(using: .utf8)!)
                    }
                }
                idx += 1
            }
        }
        if let quitAfter = Double(env["DM_QUIT_AFTER"] ?? "") {
            DispatchQueue.main.asyncAfter(deadline: .now() + quitAfter) { NSApp.terminate(nil) }
        }

        // 服务没起来时给人看，而不是白窗口
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            self.web.evaluateJavaScript("document.title") { _, err in
                if err != nil { NSLog("pet page not loaded: \(String(describing: err))") }
            }
        }
    }

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        if (message.body as? String) == "drag", let ev = NSApp.currentEvent {
            window.performDrag(with: ev)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
}

dlog("top-level start")
let app = NSApplication.shared
app.setActivationPolicy(.accessory)                  // 不占 Dock，就是个桌宠

// 最小菜单，保住 ⌘Q
let mainMenu = NSMenu()
let appItem = NSMenuItem()
mainMenu.addItem(appItem)
let appMenu = NSMenu()
appMenu.addItem(withTitle: "退出 颜小二", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
appItem.submenu = appMenu
app.mainMenu = mainMenu

let delegate = AppDelegate()
app.delegate = delegate
app.run()
