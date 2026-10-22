// Release-built host utilities. No user machine compiler or scripting runtime is needed.
import Foundation
import CryptoKit
import CoreFoundation
import Darwin

func fail(_ message: String) -> Never { FileHandle.standardError.write(Data(("symphony: " + message + "\n").utf8)); exit(1) }
func read(_ path: String) throws -> Data { try Data(contentsOf: URL(fileURLWithPath: path)) }
func json(_ data: Data) throws -> Any { try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) }
func object(_ value: Any?) -> [String: Any] { value as? [String: Any] ?? [:] }
func matches(_ value: String, _ pattern: String) -> Bool { value.range(of: pattern.replacingOccurrences(of: "$", with: "\\z"), options: .regularExpression) != nil }
func isBool(_ value: Any?, _ expected: Bool) -> Bool { guard let n = value as? NSNumber else { return false }; return CFGetTypeID(n) == CFBooleanGetTypeID() && n.boolValue == expected }
func integer(_ value: Any?) -> Int? { guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue == Double(n.intValue) else { return nil }; return n.intValue }
func checkedInfo(_ path: String, directory: Bool = false) -> stat {
    var info = stat()
    guard lstat(path, &info) == 0, info.st_uid == geteuid(), (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG), directory || info.st_nlink == 1 else { fail("unsafe or foreign metadata: \(path)") }
    return info
}
func validateManifest(_ path: String) throws -> [String: Any] {
    let manifest = object(try json(read(path)))
    guard integer(manifest["schema_version"]) == 1,
          let version = manifest["version"] as? String, matches(version, "^[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9.-]+)?$"),
          let revision = manifest["source_revision"] as? String, matches(revision, "^[a-f0-9]{40}$"),
          ["candidate", "stable"].contains(manifest["channel"] as? String ?? ""),
          manifest["host_architectures"] as? [String] == ["arm64", "x86_64"] else { fail("invalid or unsupported release manifest") }
    guard Set(object(manifest["runners"]).keys) == Set(["codex", "claude"]) else { fail("both runner providers are required") }
    for provider in ["codex", "claude"] {
        guard Set(object(object(manifest["runners"])[provider]).keys) == Set(["arm64", "x86_64"]) else { fail("both runner architectures are required") }
        for arch in ["arm64", "x86_64"] {
            let runner = object(object(object(manifest["runners"])[provider])[arch])
            let tools = object(runner["tool_versions"])
            for tool in [provider, "git", "node", "playwright"] { guard let value = tools[tool] as? String, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { fail("missing recorded tool version: \(tool)") } }
            guard runner["source_revision"] as? String == revision,
                  runner["platform"] as? String == (arch == "arm64" ? "linux/arm64" : "linux/amd64"),
                  let image = runner["image"] as? String,
                  matches(image, "^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$") else { fail("missing or invalid \(provider)/\(arch) release image") }
        }
    }
    let bundle = object(manifest["bundle"])
    guard let filename = bundle["filename"] as? String, filename == "symphony-\(version)-macos.tar.gz" else { fail("invalid bundle filename") }
    if let digest = bundle["sha256"] { guard let s = digest as? String, matches(s, "^[a-f0-9]{64}$") else { fail("invalid bundle checksum") } }
    return manifest
}
func gitValue(_ input: String) -> String {
    var result = "", quoted = false, escaped = false
    for ch in input {
        if escaped {
            switch ch { case "\\", "\"": result.append(ch); case "t": result.append("\t"); case "b": result.append("\u{8}"); case "n": result.append("\n"); default: fail("invalid origin URL escape") }
            escaped = false; continue
        }
        if ch == "\\" { escaped = true; continue }
        if ch == "\"" { quoted.toggle(); continue }
        if !quoted && (ch == "#" || ch == ";") { break }
        result.append(ch)
    }
    guard !quoted, !escaped else { fail("unterminated origin URL quoting") }
    let value = result.trimmingCharacters(in: .whitespaces)
    guard !value.isEmpty, !value.contains("\n"), !value.contains("\r") else { fail("invalid origin URL") }
    return value
}
func repository(_ input: String) throws -> (String, String) {
    var root = URL(fileURLWithPath: input).standardizedFileURL.resolvingSymlinksInPath()
    while root.path != "/" {
        let marker = root.appendingPathComponent(".git")
        var directory: ObjCBool = false
        if FileManager.default.fileExists(atPath: marker.path, isDirectory: &directory) {
            var git = marker
            if !directory.boolValue {
                let text = try String(contentsOf: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                guard text.hasPrefix("gitdir: ") else { fail("invalid repository .git file") }
                git = URL(fileURLWithPath: String(text.dropFirst(8)), relativeTo: root).standardizedFileURL
            }
            let common = git.appendingPathComponent("commondir")
            if FileManager.default.fileExists(atPath: common.path) {
                let value = try String(contentsOf: common, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                git = URL(fileURLWithPath: value, relativeTo: URL(fileURLWithPath: git.path, isDirectory: true)).standardizedFileURL
            }
            let config = try String(contentsOf: git.appendingPathComponent("config"), encoding: .utf8)
            var origin = false
            var remote: String? = nil
            for raw in config.components(separatedBy: .newlines) {
                let line = raw.trimmingCharacters(in: .whitespaces)
                if matches(line, "^\\[(?i:include|includeIf)([ \\t\\]]).*") { fail("packaged project identity requires an explicit local origin; Git config includes are unsupported") }
                if line.hasPrefix("[") { origin = matches(line, "^\\[(?i:remote)[ \\t]+\"origin\"\\][ \\t]*([#;].*)?$"); continue }
                if origin, matches(line, "^(?i:url)[ \\t]*=") {
                    let value = gitValue(String(line.drop(while: { $0 != "=" }).dropFirst()).trimmingCharacters(in: .whitespaces))
                    remote = value
                }
            }
            guard let found = remote else { fail("the repository needs an explicit origin remote in its local Git config") }
            guard let resolved = realpath(root.path, nil) else { fail("cannot resolve repository root") }; defer { free(resolved) }
            return (String(cString: resolved), found)
        }
        root.deleteLastPathComponent()
    }
    fail("run this command inside an existing Git repository")
}
let args = Array(CommandLine.arguments.dropFirst())
let arity = ["validate-manifest": 2, "get": 3, "repository-root": 2, "repository-remote": 2, "hash-blob": 2, "lock-directory": 2, "lock": 3, "legacy-lock": 2, "identity-migration": 1, "verify-files": 2, "json": 2, "replace-link": 3, "owned-directory": 2, "build-revision": 1]
guard let command = args.first, args.count >= (arity[command] ?? 1) else { fail("host helper requires an operation") }
do {
    switch command {
    case "build-revision": print(compiledRevision)
    case "replace-link":
        var info = stat()
        guard lstat(args[1], &info) == 0, info.st_mode & S_IFMT == S_IFLNK, info.st_uid == geteuid() else { fail("activation source is not an owned symbolic link") }
        if lstat(args[2], &info) == 0 { guard info.st_mode & S_IFMT == S_IFLNK, info.st_uid == geteuid() else { fail("refusing to replace an unmanaged activation path") } }
        else if errno != ENOENT { fail("cannot inspect activation path") }
        guard rename(args[1], args[2]) == 0 else { fail("cannot activate release link") }
    case "validate-manifest": _ = try validateManifest(args[1])
    case "get":
        var value: Any = try json(read(args[1]))
        for part in args[2].split(separator: ".") { guard let next = object(value)[String(part)] else { fail("missing release field: \(args[2])") }; value = next }
        if let s = value as? String { print(s) }
        else if let n = value as? NSNumber { print(CFGetTypeID(n) == CFBooleanGetTypeID() ? (n.boolValue ? "true" : "false") : n.stringValue) }
        else { FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])); print() }
    case "repository-root", "repository-remote":
        let (root, remote) = try repository(args[1]); print(command == "repository-root" ? root : remote)
    case "hash-blob":
        let value = Data(args[1].utf8); let header = Data("blob \(value.count)\0".utf8)
        print(Insecure.SHA1.hash(data: header + value).map { String(format: "%02x", $0) }.joined())
    case "owned-directory":
        let info = checkedInfo(args[1], directory: true)
        guard info.st_mode & 0o777 == 0o700 else { fail("unsafe owned directory") }
    case "lock-directory":
        if mkdir(args[1], 0o700) != 0 && errno != EEXIST { fail("cannot create runner lock directory") }
        let info = checkedInfo(args[1], directory: true)
        guard info.st_mode & 0o777 == 0o700 else { fail("unsafe runner lock directory") }
    case "lock":
        let path = args[1]; guard let fd = Int32(args[2]) else { fail("invalid lock descriptor") }
        let current = checkedInfo(path); var info = stat()
        guard fstat(fd, &info) == 0, info.st_dev == current.st_dev, info.st_ino == current.st_ino else { fail("updater lock cannot be verified") }
        if args.count > 3 && args[3] == "inherited" {
            let probe = open(path, O_WRONLY | O_NOFOLLOW); guard probe >= 0 else { fail("cannot verify updater ownership") }; defer { close(probe) }
            guard flock(probe, LOCK_EX | LOCK_NB) != 0, errno == EWOULDBLOCK else { fail("update continuation is missing source ownership") }
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { fail("another updater is active") }
    case "legacy-lock":
        // Packaged installs have no directory-lock predecessor. Refuse ambiguous old state rather than guessing ownership.
        fail("legacy updater directory needs recovery through its original source installation")
    case "identity-migration": fail("source installation identity migration must be completed before packaged installation")
    case "verify-files":
        let root = URL(fileURLWithPath: args[1]); let lines = try String(contentsOf: root.appendingPathComponent("files.sha256"), encoding: .utf8).split(separator: "\n")
        guard !lines.isEmpty else { fail("missing bundle inventory") }
        for line in lines {
            guard line.count > 66 else { fail("invalid bundle inventory") }
            let digest = String(line.prefix(64)); let path = String(line.dropFirst(66))
            guard matches(digest, "^[a-f0-9]{64}$"), !path.hasPrefix("/"), !path.split(separator: "/").contains("..") else { fail("unsafe bundle inventory path") }
            let url = root.appendingPathComponent(path); _ = checkedInfo(url.path)
            let observed = SHA256.hash(data: try read(url.path)).map { String(format: "%02x", $0) }.joined()
            guard observed == digest else { fail("bundle checksum mismatch: \(path)") }
        }
    case "json":
        let data = FileHandle.standardInput.readDataToEndOfFile(); let state = object(try json(data)); let agent = object(state["agent"]); let mode = args[1]
        switch mode {
        case "profile":
            guard data.count <= 4096, isBool(state["ready"], true), let profile = state["profile"] as? String, matches(profile, "^[a-f0-9]{64}$") else { exit(1) }; print(profile)
        case "legacy-running": guard let running = integer(object(state["counts"])["running"]), running >= 0, state["error"] == nil else { exit(1) }
        case "prior-ready": guard agent["admission"] == nil, isBool(agent["draining"], false), isBool(object(agent["readiness"])["ready"], true), agent["pause"] is NSNull else { exit(1) }
        case "admission":
            let a = state["agent"] == nil ? state : agent; let d = object(a["admission"])
            if isBool(a["draining"], false), isBool(d["ready"], true) { print("operational") }
            else if isBool(a["draining"], false), isBool(d["ready"], false), d["reason"] as? String == "quota_exhausted", integer(d["retry_at"]) != nil { print("quota_paused") }
            else { print("unhealthy") }
        case "idle": guard isBool(agent["draining"], true), integer(object(state["counts"])["running"]) == 0 else { exit(1) }
        case "workflow-mount":
            guard args.count >= 3 else { fail("workflow mount check requires a path") }
            guard let mounts = try json(data) as? [[String: Any]] else { exit(1) }
            let found = mounts.filter { $0["Destination"] as? String == "/etc/symphony/WORKFLOW.md" }
            guard found.count == 1, found[0]["Type"] as? String == "bind", isBool(found[0]["RW"], false), let source = found[0]["Source"] as? String, [args[2], "/host_mnt" + args[2]].contains(source) else { exit(1) }
        default: fail("unknown JSON predicate")
        }
    default: fail("unknown host helper operation: \(command)")
    }
} catch { fail("\(command) failed: \(error.localizedDescription)") }
