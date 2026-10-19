import AppKit
import WebKit
import UniformTypeIdentifiers

extension NSToolbarItem.Identifier {
    static let readerSidebar = NSToolbarItem.Identifier("reader.sidebar")
    static let readerImport = NSToolbarItem.Identifier("reader.import")
    static let readerFinished = NSToolbarItem.Identifier("reader.finished")
    static let readerPeople = NSToolbarItem.Identifier("reader.people")
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSToolbarDelegate, NSUserInterfaceValidations, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var service: Process?
    var timer: Timer?
    var started = Date()
    var ready: URL!
    var logHandle: FileHandle?
    var sessionURL: URL?
    var importing = false
    var appearanceMenu: NSMenu?
    lazy var uploadSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 600
        return URLSession(configuration: config)
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        applyAppearance(UserDefaults.standard.string(forKey:"ReaderAppearance") ?? "system")
        buildMenu()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "finishedFiles")
        let hostScript = """
        (() => {
            const mark = () => {
                if (!document.documentElement) return false;
                document.documentElement.classList.add('native-host');
                return true;
            };
            if (!mark()) {
                const observer = new MutationObserver(() => { if (mark()) observer.disconnect(); });
                observer.observe(document, { childList: true, subtree: true });
            }
        })();
        """
        config.userContentController.addUserScript(WKUserScript(source:hostScript,injectionTime:.atDocumentStart,forMainFrameOnly:true))
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self; web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1380, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Statement Reader"
        window.toolbarStyle = .unifiedCompact
        window.backgroundColor = .windowBackgroundColor
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 960, height: 650)
        let material=NSVisualEffectView()
        material.material = .sidebar
        material.blendingMode = .behindWindow
        material.state = .followsWindowActiveState
        web.translatesAutoresizingMaskIntoConstraints=false
        material.addSubview(web)
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo:material.leadingAnchor),
            web.trailingAnchor.constraint(equalTo:material.trailingAnchor),
            web.topAnchor.constraint(equalTo:material.topAnchor),
            web.bottomAnchor.constraint(equalTo:material.bottomAnchor)])
        window.contentView = material
        let toolbar=NSToolbar(identifier:"StatementReaderToolbar")
        toolbar.delegate=self; toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization=true; toolbar.autosavesConfiguration=true
        window.toolbar=toolbar
        if !window.setFrameUsingName("StatementReaderMainWindow") { window.center() }
        window.setFrameAutosaveName("StatementReaderMainWindow")
        updateToolbar()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        launchService()
    }

    func buildMenu() {
        let menu=NSMenu(); let appItem=NSMenuItem(); menu.addItem(appItem)
        let appMenu=NSMenu(); appItem.submenu=appMenu
        appMenu.addItem(withTitle: "About Private Statement Reader", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(withTitle: "Show Local Data", action: #selector(showData), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit Private Statement Reader", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let fileItem=NSMenuItem(); fileItem.title="File"; menu.addItem(fileItem)
        let fileMenu=NSMenu(title:"File"); fileItem.submenu=fileMenu
        fileMenu.addItem(withTitle:"Import PDFs…",action:#selector(importPDFs),keyEquivalent:"o")
        fileMenu.addItem(withTitle:"Show Finished Files",action:#selector(showFinishedFiles),keyEquivalent:"e")
        let editItem=NSMenuItem(); editItem.title="Edit"; menu.addItem(editItem)
        let edit=NSMenu(title:"Edit"); editItem.submenu=edit
        for (title,action,key) in [("Undo","undo:","z"),("Cut","cut:","x"),("Copy","copy:","c"),("Paste","paste:","v"),("Select All","selectAll:","a")] {
            edit.addItem(withTitle:title,action:Selector(action),keyEquivalent:key)
        }
        edit.addItem(NSMenuItem.separator())
        edit.addItem(withTitle:"Find…",action:#selector(focusSearch),keyEquivalent:"f")
        let viewItem=NSMenuItem(); viewItem.title="View"; menu.addItem(viewItem)
        let viewMenu=NSMenu(title:"View"); viewItem.submenu=viewMenu
        let sidebarItem=viewMenu.addItem(withTitle:"Toggle Sidebar",action:#selector(toggleSidebar),keyEquivalent:"s")
        sidebarItem.keyEquivalentModifierMask=[.command,.option]
        viewMenu.addItem(withTitle:"People",action:#selector(showPeople),keyEquivalent:"")
        viewMenu.addItem(withTitle:"Reload View",action:#selector(reloadView),keyEquivalent:"r")
        viewMenu.addItem(NSMenuItem.separator())
        let appearanceItem=NSMenuItem(title:"Appearance",action:nil,keyEquivalent:"")
        let appearances=NSMenu(title:"Appearance"); appearanceItem.submenu=appearances
        appearanceMenu=appearances; viewMenu.addItem(appearanceItem)
        for (label,mode) in [("Follow System","system"),("Light","light"),("Dark","dark")] {
            let item=appearances.addItem(withTitle:label,action:#selector(chooseAppearance(_:)),keyEquivalent:"")
            item.representedObject=mode; item.target=self
            item.state=mode == (UserDefaults.standard.string(forKey:"ReaderAppearance") ?? "system") ? .on : .off
        }
        NSApp.mainMenu=menu
    }

    func applyAppearance(_ mode:String) {
        switch mode {
        case "light":NSApp.appearance=NSAppearance(named:.aqua)
        case "dark":NSApp.appearance=NSAppearance(named:.darkAqua)
        default:NSApp.appearance=nil
        }
    }
    @objc func chooseAppearance(_ sender:NSMenuItem) {
        guard let mode=sender.representedObject as? String else { return }
        UserDefaults.standard.set(mode,forKey:"ReaderAppearance"); applyAppearance(mode)
        for item in appearanceMenu?.items ?? [] { item.state=(item.representedObject as? String) == mode ? .on : .off }
    }

    func toolbarAllowedItemIdentifiers(_ toolbar:NSToolbar)->[NSToolbarItem.Identifier] {
        [.readerSidebar,.flexibleSpace,.space,.readerImport,.readerFinished,.readerPeople]
    }
    func toolbarDefaultItemIdentifiers(_ toolbar:NSToolbar)->[NSToolbarItem.Identifier] {
        [.readerSidebar,.flexibleSpace,.readerImport,.readerFinished,.readerPeople]
    }
    func toolbar(_ toolbar:NSToolbar,itemForItemIdentifier identifier:NSToolbarItem.Identifier,willBeInsertedIntoToolbar flag:Bool)->NSToolbarItem? {
        let options:[NSToolbarItem.Identifier:(String,String,Selector)] = [
            .readerSidebar:("Toggle Sidebar","sidebar.left",#selector(toggleSidebar)),
            .readerImport:("Import PDFs","doc.badge.plus",#selector(importPDFs)),
            .readerFinished:("Finished Files","folder",#selector(showFinishedFiles)),
            .readerPeople:("People","person.2",#selector(showPeople))]
        guard let (label,symbol,action)=options[identifier] else { return nil }
        let item=NSToolbarItem(itemIdentifier:identifier)
        item.label=label; item.paletteLabel=label; item.toolTip=label
        item.image=NSImage(systemSymbolName:symbol,accessibilityDescription:label)
        item.target=self; item.action=action
        return item
    }
    func validateUserInterfaceItem(_ item:NSValidatedUserInterfaceItem)->Bool {
        if item.action == #selector(importPDFs) { return sessionURL != nil && !importing }
        if [#selector(toggleSidebar),#selector(showPeople),#selector(focusSearch),#selector(reloadView)].contains(item.action) { return sessionURL != nil }
        return true
    }
    func updateToolbar() {
        for item in window.toolbar?.items ?? [] {
            if item.itemIdentifier == .readerImport {
                item.isEnabled=sessionURL != nil && !importing
                item.label=importing ? "Importing…" : "Import PDFs"
                item.toolTip=importing ? "Adding statements to the automatic queue" : "Import bank statement PDFs (⌘O)"
            } else if item.itemIdentifier != .readerFinished { item.isEnabled=sessionURL != nil }
        }
    }
    @objc func toggleSidebar() { web.evaluateJavaScript("document.documentElement.classList.toggle('sidebar-hidden')",completionHandler:nil) }
    @objc func showPeople() { web.evaluateJavaScript("if (typeof people === 'function') { people(); }",completionHandler:nil) }
    @objc func focusSearch() {
        web.evaluateJavaScript("(() => { const input=document.querySelector('#search'); if (input) { input.focus(); input.select(); } })()",completionHandler:nil)
        window.makeFirstResponder(web)
    }
    @objc func reloadView() { web.reload() }

    @objc func importPDFs() {
        guard sessionURL != nil, !importing else { return }
        let panel=NSOpenPanel(); panel.title="Import Bank Statements"
        panel.prompt="Import"; panel.allowsMultipleSelection=true
        panel.canChooseDirectories=false; panel.allowedContentTypes=[.pdf]
        panel.beginSheetModal(for:window) { [weak self] response in
            guard response == .OK, !panel.urls.isEmpty, let self=self else { return }
            self.importing=true; self.updateToolbar()
            self.uploadPDFs(panel.urls,failures:[])
        }
    }
    func uploadPDFs(_ urls:[URL],failures:[String]) {
        guard let file=urls.first else {
            importing=false; updateToolbar()
            if !failures.isEmpty {
                let alert=NSAlert(); alert.messageText="Some statements could not be imported"
                alert.informativeText=failures.joined(separator:"\n")
                alert.beginSheetModal(for:window,completionHandler:nil)
            }
            return
        }
        guard let base=sessionURL, let components=URLComponents(url:base,resolvingAgainstBaseURL:false),
              let token=components.queryItems?.first(where:{$0.name == "token"})?.value,
              let endpoint=URL(string:"/api/upload",relativeTo:base)?.absoluteURL else {
            importing=false; updateToolbar(); return
        }
        var request=URLRequest(url:endpoint); request.httpMethod="POST"
        request.setValue(token,forHTTPHeaderField:"X-Statement-Token")
        let safeName=String(file.lastPathComponent.unicodeScalars.map { (0x20...0x7e).contains($0.value) ? Character(String($0)) : Character("_") })
        request.setValue(safeName,forHTTPHeaderField:"X-Filename")
        request.setValue("application/pdf",forHTTPHeaderField:"Content-Type")
        let accessed=file.startAccessingSecurityScopedResource()
        uploadSession.uploadTask(with:request,fromFile:file) { [weak self] data,response,error in
            if accessed { file.stopAccessingSecurityScopedResource() }
            let payload=data.flatMap { try? JSONSerialization.jsonObject(with:$0) as? [String:Any] }
            let code=(response as? HTTPURLResponse)?.statusCode ?? 0
            let identifier=payload?["id"] as? String
            let failure=error?.localizedDescription ?? (payload?["error"] as? String) ?? "The local engine could not import this PDF."
            DispatchQueue.main.async {
                guard let self=self else { return }
                var nextFailures=failures
                if (200...299).contains(code), let id=identifier, UUID(uuidString:id) != nil,
                   let encoded=try? JSONSerialization.data(withJSONObject:[id]), let literal=String(data:encoded,encoding:.utf8) {
                    self.web.evaluateJavaScript("if (typeof openDoc === 'function') { openDoc(\(literal)[0]); }",completionHandler:nil)
                } else { nextFailures.append("\(file.lastPathComponent): \(failure)") }
                self.uploadPDFs(Array(urls.dropFirst()),failures:nextFailures)
            }
        }.resume()
    }

    var dataURL: URL {
        FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("Private Statement Reader")
    }

    @objc func about() {
        let a=NSAlert(); a.messageText="Statement Reader"; a.informativeText="Automatic bank statement extraction and reconciliation.\nSpreadsheets save automatically; interrupted work resumes on launch.\nVersion 0.5.1 — estimated readings are labeled."; a.runModal()
    }
    @objc func showData() { NSWorkspace.shared.open(dataURL) }
    @objc func showFinishedFiles() {
        let exports = dataURL.appendingPathComponent("exports", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: exports, withIntermediateDirectories: true)
            NSWorkspace.shared.open(exports)
        } catch {
            let alert=NSAlert(); alert.messageText="Cannot open finished files"; alert.informativeText=error.localizedDescription; alert.runModal()
        }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "finishedFiles", message.frameInfo.isMainFrame,
              let pageURL=message.frameInfo.request.url, pageURL.scheme == "http",
              pageURL.host == "127.0.0.1", let port=sessionURL?.port, pageURL.port == port,
              let body=message.body as? [String:String], body.count == 1,
              body["action"] == "openFinishedFiles" else { return }
        showFinishedFiles()
    }

    func launchService() {
        do {
            try FileManager.default.createDirectory(at:dataURL,withIntermediateDirectories:true)
            ready=dataURL.appendingPathComponent("launch-\(UUID().uuidString).json")
            guard let resources=Bundle.main.resourceURL else { throw fail("App resources are missing.") }
            let candidates=["/opt/homebrew/bin/python3","/usr/local/bin/python3"]
            guard let python=candidates.first(where:{FileManager.default.isExecutableFile(atPath:$0)}) else {
                throw fail("Python 3 is missing. Run the included Setup.command once, then reopen this app.")
            }
            let p=Process(); p.executableURL=URL(fileURLWithPath:python)
            p.arguments=[resources.appendingPathComponent("server/app.py").path]
            var env=ProcessInfo.processInfo.environment
            env["PATH"]="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            env["PYTHONPATH"]=resources.appendingPathComponent("python-packages").path
            env["PYTHONDONTWRITEBYTECODE"]="1"
            env["STATEMENT_DATA"]=dataURL.path
            env["STATEMENT_READY"]=ready.path
            env["STATEMENT_NATIVE"]="1"
            env["STATEMENT_VISION"]=resources.appendingPathComponent("VisionOCR").path
            let models=resources.appendingPathComponent("models")
            if FileManager.default.fileExists(atPath:models.appendingPathComponent("eng.traineddata").path) {
                env["STATEMENT_TESSDATA"]=models.path
            }
            let aiModel=resources.appendingPathComponent("ai-model")
            if FileManager.default.fileExists(atPath:aiModel.appendingPathComponent("config.json").path),
               FileManager.default.isExecutableFile(atPath:"/opt/homebrew/bin/python3.13") {
                env["STATEMENT_AI_MODEL"]=aiModel.path
                env["STATEMENT_AI_PYTHON"]="/opt/homebrew/bin/python3.13"
                env["STATEMENT_AI_PACKAGES"]=resources.appendingPathComponent("ai-packages").path
            }
            env["OMP_THREAD_LIMIT"]="2"
            env["STATEMENT_AI_INSTANCES"]="2"
            p.environment=env
            let log=dataURL.appendingPathComponent("app.log")
            FileManager.default.createFile(atPath:log.path,contents:nil)
            logHandle=try FileHandle(forWritingTo:log); p.standardOutput=logHandle; p.standardError=logHandle
            try p.run(); service=p; started=Date()
            timer=Timer.scheduledTimer(withTimeInterval:0.2,repeats:true) { [weak self] _ in self?.checkReady() }
        } catch { showError(error.localizedDescription) }
    }

    func checkReady() {
        if let data=try? Data(contentsOf:ready), let obj=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           let address=obj["url"] as? String, let url=URL(string:address) {
            timer?.invalidate(); sessionURL=url; updateToolbar(); web.load(URLRequest(url:url)); try? FileManager.default.removeItem(at:ready)
        } else if service?.isRunning == false || Date().timeIntervalSince(started)>30 {
            timer?.invalidate(); showError("The local engine could not start. Run Setup.command and reopen the app. Details are saved in app.log under Show Local Data.")
        }
    }

    func fail(_ message:String)->NSError { NSError(domain:"StatementReader",code:1,userInfo:[NSLocalizedDescriptionKey:message]) }
    func showError(_ message:String) { let a=NSAlert(); a.messageText="Cannot start statement reader"; a.informativeText=message; a.runModal() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool { false }
    func windowShouldClose(_ sender:NSWindow)->Bool { sender.orderOut(nil); return false }
    func applicationShouldHandleReopen(_ sender:NSApplication, hasVisibleWindows flag:Bool)->Bool {
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true); return true
    }
    func applicationWillTerminate(_ notification:Notification) {
        timer?.invalidate()
        uploadSession.invalidateAndCancel()
        if let p=service, p.isRunning { kill(-p.processIdentifier,SIGTERM); p.terminate() }
        try? logHandle?.close()
        if let ready=ready { try? FileManager.default.removeItem(at:ready) }
    }

    func webView(_ webView:WKWebView, runOpenPanelWith parameters:WKOpenPanelParameters, initiatedByFrame frame:WKFrameInfo,
                 completionHandler:@escaping ([URL]?)->Void) {
        let panel=NSOpenPanel(); panel.allowsMultipleSelection=parameters.allowsMultipleSelection
        panel.canChooseDirectories=false; panel.allowedContentTypes=[.pdf]
        panel.beginSheetModal(for:window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func webView(_ webView:WKWebView, runJavaScriptConfirmPanelWithMessage message:String,
                 initiatedByFrame frame:WKFrameInfo, completionHandler:@escaping(Bool)->Void) {
        let alert=NSAlert(); alert.messageText="Re-extract statement?"; alert.informativeText=message
        alert.addButton(withTitle:"Re-extract"); alert.addButton(withTitle:"Cancel")
        alert.beginSheetModal(for:window) { result in completionHandler(result == .alertFirstButtonReturn) }
    }

    func webView(_ webView:WKWebView, decidePolicyFor navigationAction:WKNavigationAction, decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
        guard let url=navigationAction.request.url else { decisionHandler(.cancel); return }
        guard url.scheme=="http", url.host=="127.0.0.1", url.port==sessionURL?.port else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload { decisionHandler(.download) }
        else { decisionHandler(.allow) }
    }
    func webView(_ webView:WKWebView, decidePolicyFor navigationResponse:WKNavigationResponse, decisionHandler:@escaping(WKNavigationResponsePolicy)->Void) {
        if !navigationResponse.canShowMIMEType { decisionHandler(.download) } else { decisionHandler(.allow) }
    }
    func webView(_ webView:WKWebView, navigationAction:WKNavigationAction, didBecome download:WKDownload) { download.delegate=self }
    func webView(_ webView:WKWebView, navigationResponse:WKNavigationResponse, didBecome download:WKDownload) { download.delegate=self }
    func download(_ download:WKDownload, decideDestinationUsing response:URLResponse, suggestedFilename:String, completionHandler:@escaping(URL?)->Void) {
        let panel=NSSavePanel(); panel.nameFieldStringValue=suggestedFilename
        panel.beginSheetModal(for:window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }
}

let app=NSApplication.shared
let delegate=AppDelegate()
app.delegate=delegate
app.setActivationPolicy(.regular)
app.run()
