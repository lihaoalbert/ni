// SSE 解析器 — 把字节流切分成事件
// SPEC:
//   - 事件之间用 `\n\n` 分隔
//   - 单事件内每行 `field: value`
//   - `data:` 行:多行用 `\n` 拼接
//   - UTF-8 字符可能跨 chunk
//   - 流结束(buffer 有残余)也要尝试解析
import Foundation

public struct SSEReader: Sendable {
    private var buffer: String = ""

    public init() {}

    /// 喂入一段 chunk,返回已就绪的事件数组。
    /// 残余(不完整的 frame)留在 buffer,等下次 feed。
    public mutating func feed(_ chunk: String) -> [SSEEvent] {
        buffer += chunk
        var events: [SSEEvent] = []
        while let range = buffer.range(of: "\n\n") {
            let frame = String(buffer[buffer.startIndex..<range.lowerBound])
            buffer = String(buffer[range.upperBound..<buffer.endIndex])
            if let event = parseFrame(frame) {
                events.append(event)
            }
        }
        return events
    }

    /// 流结束时调用,尝试解析 buffer 里残留的最后一帧。
    public mutating func endOfStream() -> SSEEvent? {
        guard !buffer.isEmpty else { return nil }
        let rest = buffer
        buffer = ""
        return parseFrame(rest)
    }

    private func parseFrame(_ frame: String) -> SSEEvent? {
        var dataLines: [String] = []
        for raw in frame.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if line.hasPrefix("data:") {
                var payload = line.dropFirst("data:".count)
                if payload.first == " " { payload = payload.dropFirst() }
                dataLines.append(String(payload))
            }
        }
        guard !dataLines.isEmpty else { return nil }
        let json = dataLines.joined(separator: "\n")
        guard let data = json.data(using: .utf8) else { return nil }
        guard let payload = try? JSONDecoder().decode(SSEServerPayload.self, from: data) else {
            return nil
        }
        return payload.toEvent()
    }
}

/// UTF-8 边界处理 — 累积的 bytes 流可能切碎多字节字符,要把已完整的字符切给 String,残余留给下个 chunk
public enum UTF8Boundary {
    public static func extract(_ data: inout Data) -> String {
        if data.isEmpty { return "" }
        // 找最后一个 lead byte,根据它的声明长度判断是否完整
        var i = data.count - 1
        while i >= 0 {
            let b = data[i]
            if b < 0x80 {
                // ASCII lead,完整
                break
            }
            if b >= 0xC0 {
                // multi-byte lead
                let needed: Int
                if b < 0xE0 { needed = 2 }
                else if b < 0xF0 { needed = 3 }
                else if b < 0xF8 { needed = 4 }
                else {
                    // invalid byte — 丢弃
                    data = Data()
                    return ""
                }
                let have = data.count - i
                if have < needed {
                    // 不完整,切掉完整部分
                    let validCount = i
                    let str = String(data: data.prefix(validCount), encoding: .utf8) ?? ""
                    data = data.suffix(data.count - validCount)
                    return str
                }
                break
            }
            // continuation byte 0x80..0xBF,继续向左
            i -= 1
        }
        // 全部完整
        let str = String(data: data, encoding: .utf8) ?? ""
        data = Data()
        return str
    }
}
