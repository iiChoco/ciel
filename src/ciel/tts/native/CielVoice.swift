// CielVoice — Apple's speech synthesizer as a streaming subprocess.
//
// Built by ciel/tts/native.py with `swiftc` on first use (no Xcode project,
// only the command-line tools), then driven over pipes:
//
//   stdin  ← one JSON object per line
//            {"op":"speak","id":N,"text":"...","rate":0.53}
//            {"op":"cancel","id":N}
//   stdout → binary frames:  u8 kind · u32 id · u32 len · payload   (little-endian)
//            kind 1 begin  payload u32 sample rate
//            kind 2 pcm    payload int16 mono samples
//            kind 3 end    (also after a cancel)
//            kind 4 error  payload utf-8 message
//            kind 5 ready  payload utf-8 JSON {"voice","identifier","rate"} — once, at start
//
// Why a subprocess and not PyObjC: AVSpeechSynthesizer's buffer callback
// wants a run loop and fires on its own queue; a tiny Swift process with a
// main loop is simpler to get right than threading that through asyncio,
// and it dies with Ciel (the parent closes stdin, we exit).
//
// Why write(_:toBufferCallback:) and not `say -o`: the callback hands over
// PCM as it is synthesized, so the first chunk leaves before the sentence
// is finished — the same reason piper beat `say` on first-audio latency.

import AVFoundation
import Foundation

// MARK: - stdout frames

let outLock = NSLock()

func writeFrame(kind: UInt8, id: UInt32, payload: Data) {
    var frame = Data(capacity: 9 + payload.count)
    frame.append(kind)
    var i = id.littleEndian
    var n = UInt32(payload.count).littleEndian
    withUnsafeBytes(of: &i) { frame.append(contentsOf: $0) }
    withUnsafeBytes(of: &n) { frame.append(contentsOf: $0) }
    frame.append(payload)
    outLock.lock()
    defer { outLock.unlock() }
    frame.withUnsafeBytes { raw in
        var off = 0
        while off < raw.count {
            let wrote = fwrite(raw.baseAddress! + off, 1, raw.count - off, stdout)
            if wrote <= 0 { exit(0) }  // the parent is gone
            off += wrote
        }
    }
    fflush(stdout)
}

func u32(_ v: UInt32) -> Data {
    var x = v.littleEndian
    return withUnsafeBytes(of: &x) { Data($0) }
}

// MARK: - the voice

func pickVoice(named wanted: String) -> AVSpeechSynthesisVoice? {
    let voices = AVSpeechSynthesisVoice.speechVoices()
    // An identifier is exact; a name prefers the best quality installed
    // under that name (Premium > Enhanced > compact), since the compact one
    // is what ships and the good one is what was downloaded on purpose.
    if let v = voices.first(where: { $0.identifier == wanted }) { return v }
    let byName = voices.filter { $0.name.caseInsensitiveCompare(wanted) == .orderedSame }
    let order: [AVSpeechSynthesisVoiceQuality] = [.premium, .enhanced, .default]
    for q in order {
        if let v = byName.first(where: { $0.quality == q }) { return v }
    }
    return nil
}

final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
    let synth = AVSpeechSynthesizer()
    let voice: AVSpeechSynthesisVoice
    var queue: [(id: UInt32, text: String, rate: Float)] = []
    var current: UInt32? = nil
    var ended = Set<UInt32>()
    var sampleRate: UInt32 = 0

    init(voice: AVSpeechSynthesisVoice) {
        self.voice = voice
        super.init()
        synth.delegate = self
    }

    func enqueue(id: UInt32, text: String, rate: Float) {
        queue.append((id, text, rate))
        pump()
    }

    func cancel(id: UInt32) {
        if current == id {
            synth.stopSpeaking(at: .immediate)
            // The callback may or may not deliver a final buffer after a
            // stop; `didCancel` below closes the frame stream either way.
        } else {
            queue.removeAll { $0.id == id }
            end(id)
        }
    }

    private func end(_ id: UInt32) {
        if ended.contains(id) { return }
        ended.insert(id)
        writeFrame(kind: 3, id: id, payload: Data())
        if current == id { current = nil }
        pump()
    }

    private func pump() {
        guard current == nil, let next = queue.first else { return }
        queue.removeFirst()
        current = next.id
        let id = next.id
        let utt = AVSpeechUtterance(string: next.text)
        utt.voice = voice
        utt.rate = max(AVSpeechUtteranceMinimumSpeechRate,
                       min(AVSpeechUtteranceMaximumSpeechRate, next.rate))
        utt.prefersAssistiveTechnologySettings = false
        var begun = false
        synth.write(utt) { [weak self] buffer in
            guard let self = self else { return }
            guard let pcm = buffer as? AVAudioPCMBuffer else { return }
            if !begun {
                begun = true
                self.sampleRate = UInt32(pcm.format.sampleRate)
                writeFrame(kind: 1, id: id, payload: u32(self.sampleRate))
            }
            let frames = Int(pcm.frameLength)
            if frames == 0 {
                self.end(id)  // the synthesizer's own "done"
                return
            }
            // Float32 non-interleaved mono (what every voice reports); int16
            // is a fallback in case a voice ever hands over integers.
            var out = Data(count: frames * 2)
            out.withUnsafeMutableBytes { raw in
                let dst = raw.bindMemory(to: Int16.self)
                if let f = pcm.floatChannelData {
                    let src = f[0]
                    for i in 0..<frames {
                        let s = max(-1.0, min(1.0, src[i]))
                        dst[i] = Int16(s * 32767.0)
                    }
                } else if let s16 = pcm.int16ChannelData {
                    let src = s16[0]
                    for i in 0..<frames { dst[i] = src[i] }
                }
            }
            writeFrame(kind: 2, id: id, payload: out)
        }
    }

    // MARK: delegate — the end of an utterance, however it ended.

    func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        if let id = current { end(id) }
    }

    func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) {
        if let id = current { end(id) }
    }
}

// MARK: - main

let args = CommandLine.arguments
let wanted = args.count > 1 ? args[1] : "Jamie"

guard let voice = pickVoice(named: wanted) else {
    let names = AVSpeechSynthesisVoice.speechVoices()
        .filter { $0.quality != .default }
        .map { "\($0.name) (\($0.quality == .premium ? "Premium" : "Enhanced"), \($0.language))" }
    let msg = "voice \(wanted) is not installed; enhanced/premium voices here: "
        + (names.isEmpty ? "none" : names.joined(separator: ", "))
    writeFrame(kind: 4, id: 0, payload: msg.data(using: .utf8)!)
    exit(1)
}

let speaker = Speaker(voice: voice)
let rate = (voice.audioFileSettings[AVSampleRateKey] as? NSNumber)?.uint32Value ?? 0
let hello: [String: Any] = ["voice": voice.name, "identifier": voice.identifier,
                            "quality": voice.quality == .premium ? "premium"
                                : voice.quality == .enhanced ? "enhanced" : "compact",
                            "rate": rate]
writeFrame(kind: 5, id: 0, payload: try! JSONSerialization.data(withJSONObject: hello))

// stdin on its own thread; the synthesizer on the main queue, whose run
// loop `dispatchMain` services.
let reader = Thread {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let op = obj["op"] as? String,
              let id = (obj["id"] as? NSNumber)?.uint32Value else { continue }
        DispatchQueue.main.async {
            switch op {
            case "speak":
                let text = obj["text"] as? String ?? ""
                let r = (obj["rate"] as? NSNumber)?.floatValue ?? AVSpeechUtteranceDefaultSpeechRate
                speaker.enqueue(id: id, text: text, rate: r)
            case "cancel":
                speaker.cancel(id: id)
            default:
                break
            }
        }
    }
    exit(0)  // stdin closed: the parent is gone
}
reader.start()
dispatchMain()
