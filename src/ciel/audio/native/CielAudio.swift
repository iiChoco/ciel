// Apple's voice processing owns both ends of the room's audio.
// stdin/stdout: u8 kind, u32 id, u32 size (little endian), then payload.
// Commands: 1 play (mono int16 at the argument's rate), 2 stop, 7 begin, 8 end.
// Events: 3 capture (mono int16, 16 kHz), 4 played (one byte: completed),
//         5 ready, 6 error, 9 underrun. No audio is written to disk.
// A bounded handoff keeps capture independent of a blocked parent pipe.

import AVFoundation
import CoreAudio
import Foundation
import Darwin

let playKind: UInt8 = 1, stopKind: UInt8 = 2, captureKind: UInt8 = 3
let playedKind: UInt8 = 4, readyKind: UInt8 = 5, errorKind: UInt8 = 6
let beginKind: UInt8 = 7, endKind: UInt8 = 8, underrunKind: UInt8 = 9
let outputLock = NSLock()

func send(_ kind: UInt8, _ id: UInt32 = 0, _ payload: Data = Data()) {
    var data = Data([kind])
    var littleID = id.littleEndian, count = UInt32(payload.count).littleEndian
    withUnsafeBytes(of: &littleID) { data.append(contentsOf: $0) }
    withUnsafeBytes(of: &count) { data.append(contentsOf: $0) }
    data.append(payload)
    outputLock.lock()
    defer { outputLock.unlock() }
    data.withUnsafeBytes { bytes in
        var offset = 0
        while offset < bytes.count {
            let written = Darwin.write(STDOUT_FILENO, bytes.baseAddress! + offset, bytes.count - offset)
            if written < 0 && errno == EINTR { continue }
            if written <= 0 { exit(0) }
            offset += written
        }
    }
}

func fail(_ message: String) -> Never {
    send(errorKind, 0, Data(message.utf8))
    exit(1)
}

func readExactly(_ count: Int) -> Data? {
    var result = Data(count: count)
    let ok = result.withUnsafeMutableBytes { bytes -> Bool in
        var offset = 0
        while offset < count {
            let got = Darwin.read(STDIN_FILENO, bytes.baseAddress! + offset, count - offset)
            if got < 0 && errno == EINTR { continue }
            if got <= 0 { return false }
            offset += got
        }
        return true
    }
    return ok ? result : nil
}

// The tap only copies to preallocated storage and never waits for the worker.
// The worker copies into its own buffer before releasing a slot, so conversion
// never races a new input callback. Overflow is a failure, not stale speech.
final class CaptureRing {
    let lock = NSLock()
    let signal = DispatchSemaphore(value: 0)
    let contention = DispatchSemaphore(value: 0)
    let slots: [AVAudioPCMBuffer]
    let capacity: AVAudioFrameCount = 8192
    var head = 0, count = 0
    var overflow = false

    init(_ format: AVAudioFormat) {
        slots = (0..<16).map { _ in AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8192)! }
    }

    func copy(_ source: AVAudioPCMBuffer, _ target: AVAudioPCMBuffer) {
        target.frameLength = source.frameLength
        for channel in 0..<Int(source.format.channelCount) {
            memcpy(target.floatChannelData![channel], source.floatChannelData![channel], Int(source.frameLength) * MemoryLayout<Float>.size)
        }
    }

    func push(_ buffer: AVAudioPCMBuffer) {
        guard lock.try() else {
            // The callback cannot wait, but its lost frame must wake the worker
            // with a failure. A semaphore gives this handoff atomic ownership.
            contention.signal()
            signal.signal()
            return
        }
        if buffer.frameLength > capacity || count == slots.count {
            overflow = true
        } else {
            copy(buffer, slots[(head + count) % slots.count])
            count += 1
        }
        lock.unlock()
        signal.signal()
    }

    func pop(_ target: AVAudioPCMBuffer) -> Bool {
        if contention.wait(timeout: .now()) == .success {
            fail("Apple capture lost a frame to lock contention; restart the audio backend")
        }
        lock.lock()
        defer { lock.unlock() }
        if overflow { fail("Apple capture fell behind; restart the audio backend") }
        guard count > 0 else { return false }
        copy(slots[head], target)
        head = (head + 1) % slots.count
        count -= 1
        return true
    }
}

final class CaptureConverter {
    let converter: AVAudioConverter
    let output: AVAudioPCMBuffer

    init?(_ format: AVAudioFormat, capacity: AVAudioFrameCount) {
        guard let target = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1),
              let converter = AVAudioConverter(from: format, to: target),
              let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(ceil(Double(capacity) * 16000 / format.sampleRate)) + 128) else { return nil }
        self.converter = converter
        self.output = output
    }

    func pcm(_ input: AVAudioPCMBuffer) -> Data {
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return input
        }
        if status == .error || error != nil { fail("Apple microphone conversion failed") }
        let samples = output.floatChannelData![0]
        var pcm = Data(capacity: Int(output.frameLength) * 2)
        for index in 0..<Int(output.frameLength) {
            let value = samples[index].isFinite ? samples[index] : 0
            var sample = Int16(max(-32768, min(32767, value * 32768))).littleEndian
            withUnsafeBytes(of: &sample) { pcm.append(contentsOf: $0) }
        }
        return pcm
    }
}

// An unconnected output node can report a zero-rate format until the graph
// starts. Ask the selected hardware rather than borrowing the microphone rate.
func defaultOutputRate() -> Double {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                                            mScope: kAudioObjectPropertyScopeGlobal,
                                            mElement: kAudioObjectPropertyElementMain)
    var device = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device) == noErr,
          device != kAudioObjectUnknown else { fail("Apple default speaker is unavailable") }
    address.mSelector = kAudioDevicePropertyNominalSampleRate
    var rate = 0.0
    size = UInt32(MemoryLayout<Double>.size)
    guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &rate) == noErr, rate > 0 else {
        fail("Apple speaker has no usable hardware rate")
    }
    return rate
}

// An audible receipt with an empty queue is normal at the end of speech.
// Only a refill inside the same open utterance establishes starvation. The
// elapsed time is a lower bound: downstream playback latency precedes receipts.
final class PlaybackContinuity {
    var active = false
    var emptyAt: Double?
    var count = 0

    func begin() {
        active = true
        emptyAt = nil
        count = 0
    }

    func end() {
        active = false
        emptyAt = nil
    }

    func drained(at time: Double) {
        if active { emptyAt = time }
    }

    func refill(at time: Double) -> Double? {
        guard active, let start = emptyAt else { return nil }
        emptyAt = nil
        count += 1
        return max(0, (time - start) * 1000)
    }
}

final class Room {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let playbackFormat: AVAudioFormat
    var pending = Set<UInt32>()
    let continuity = PlaybackContinuity()
    var configurationObserver: NSObjectProtocol?

    init(rate: Double, ducking: String, agc: Bool) throws {
        guard let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1) else {
            throw NSError(domain: "CielAudio", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid playback rate"])
        }
        playbackFormat = format
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)
        input.isVoiceProcessingAGCEnabled = agc
        guard input.isVoiceProcessingEnabled && engine.outputNode.isVoiceProcessingEnabled else {
            fail("Apple voice processing did not enable on both audio devices")
        }
        if #available(macOS 14.0, *) {
            let level: AVAudioVoiceProcessingOtherAudioDuckingConfiguration.Level
            switch ducking {
            case "min": level = .min
            case "mid": level = .mid
            case "max": level = .max
            default: fail("Unknown Apple audio ducking level")
            }
            input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: true, duckingLevel: level)
        } else {
            fail("Apple audio backend requires macOS 14 or later")
        }
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        // The aggregate device may expose reference channels and a different
        // output rate. Only the processed mono voice channel belongs in
        // capture; playback follows its own device rather than the microphone.
        let hardwareRate = input.outputFormat(forBus: 0).sampleRate
        guard hardwareRate > 0,
              let inputFormat = AVAudioFormat(standardFormatWithSampleRate: hardwareRate, channels: 1) else {
            fail("Apple microphone has no usable hardware format")
        }
        let outputRate = defaultOutputRate()
        guard outputRate > 0,
              let outputFormat = AVAudioFormat(standardFormatWithSampleRate: outputRate, channels: 1) else {
            fail("Apple speaker has no usable hardware format")
        }
        engine.connect(engine.mainMixerNode, to: engine.outputNode, format: outputFormat)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0,
              inputFormat.commonFormat == .pcmFormatFloat32, !inputFormat.isInterleaved,
              let converter = CaptureConverter(inputFormat, capacity: 8192) else {
            fail("Apple microphone format is unavailable or unsupported")
        }
        let ring = CaptureRing(inputFormat)
        let work = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: ring.capacity)!
        input.installTap(onBus: 0, bufferSize: 480, format: inputFormat) { buffer, _ in
            ring.push(buffer)
        }
        DispatchQueue(label: "ciel.audio.capture").async {
            while true {
                ring.signal.wait()
                while ring.pop(work) {
                    let pcm = converter.pcm(work)
                    if !pcm.isEmpty { send(captureKind, 0, pcm) }
                }
            }
        }
        engine.prepare()
        try engine.start()
        player.play()
        configurationObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { _ in
            // A stale format or echo reference is worse than a visible restart.
            fail("Apple audio device changed; restart Ciel to reopen the new route")
        }
        let ready: [String: Any] = ["rate": 16000, "echo_cancelled": true,
                                   "input_rate": input.outputFormat(forBus: 0).sampleRate,
                                   "output_rate": engine.outputNode.outputFormat(forBus: 0).sampleRate,
                                   "mixer_rate": engine.mainMixerNode.outputFormat(forBus: 0).sampleRate]
        send(readyKind, 0, try JSONSerialization.data(withJSONObject: ready))
    }

    func play(_ id: UInt32, _ pcm: Data) {
        guard !pcm.isEmpty, pcm.count % 2 == 0, pending.count < 20, !pending.contains(id) else {
            fail("Invalid or excessive Apple playback frame")
        }
        let count = pcm.count / 2
        let buffer = AVAudioPCMBuffer(pcmFormat: playbackFormat, frameCapacity: AVAudioFrameCount(count))!
        buffer.frameLength = AVAudioFrameCount(count)
        pcm.withUnsafeBytes { bytes in
            for index in 0..<count {
                let sample = Int16(littleEndian: bytes.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))
                buffer.floatChannelData![0][index] = Float(sample) / 32768
            }
        }
        if let gap = continuity.refill(at: ProcessInfo.processInfo.systemUptime) {
            let report: [String: Any] = ["count": continuity.count, "gap_ms": gap]
            if let payload = try? JSONSerialization.data(withJSONObject: report) {
                send(underrunKind, id, payload)
            }
        }
        pending.insert(id)
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in
            DispatchQueue.main.async {
                if self.pending.remove(id) != nil {
                    if self.pending.isEmpty { self.continuity.drained(at: ProcessInfo.processInfo.systemUptime) }
                    send(playedKind, id, Data([1]))
                }
            }
        }
    }

    func stop() {
        continuity.end()
        let cancelled = pending
        pending.removeAll()
        player.stop()
        player.play()
        for id in cancelled { send(playedKind, id, Data([0])) }
    }
}

signal(SIGPIPE, SIG_IGN)
if CommandLine.arguments == [CommandLine.arguments[0], "--permission-status"] {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: print("authorized")
    case .notDetermined: print("not determined")
    case .denied: print("denied")
    case .restricted: print("restricted")
    @unknown default: print("unknown")
    }
    exit(0)
}
if CommandLine.arguments == [CommandLine.arguments[0], "--self-test-contention"] {
    let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!
    let ring = CaptureRing(format)
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
    buffer.frameLength = 480
    ring.lock.lock()
    ring.push(buffer)
    ring.lock.unlock()
    _ = ring.pop(buffer)
    exit(2)
}
if CommandLine.arguments == [CommandLine.arguments[0], "--self-test"] {
    let continuity = PlaybackContinuity()
    continuity.begin()
    precondition(continuity.refill(at: 1) == nil)
    continuity.drained(at: 2)
    precondition(abs(continuity.refill(at: 2.3)! - 300) < 0.001 && continuity.count == 1)
    precondition(continuity.refill(at: 2.4) == nil)
    continuity.end()
    continuity.drained(at: 3)
    precondition(continuity.refill(at: 4) == nil)
    continuity.begin()
    precondition(continuity.refill(at: 5) == nil && continuity.count == 0)
    let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!
    let ring = CaptureRing(format)
    let input = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
    let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8192)!
    input.frameLength = 480
    for index in 0..<480 { input.floatChannelData![0][index] = Float(index) / 480 }
    ring.push(input)
    precondition(ring.pop(output) && output.frameLength == 480)
    precondition(output.floatChannelData![0][479] == input.floatChannelData![0][479])
    precondition(!ring.pop(output))
    for _ in 0..<17 { ring.push(input) }
    precondition(ring.count == 16 && ring.overflow)
    for rate in [44100.0, 48000.0] {
        let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1)!
        let converter = CaptureConverter(format, capacity: 480)!
        let block = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
        block.frameLength = 480
        var pcm = Data()
        for batch in 0..<100 {
            for index in 0..<480 {
                block.floatChannelData![0][index] = Float(sin(2 * Double.pi * 440 * Double(batch * 480 + index) / rate) * 0.1)
            }
            pcm.append(converter.pcm(block))
        }
        let expected = Int(48000 * 16000 / rate)
        precondition(abs(pcm.count / 2 - expected) < 160)
        precondition(pcm.contains(where: { $0 != 0 }))
    }
    print("Apple capture: ring copy, framing, empty, bounded overflow, playback continuity, and 44.1/48 kHz conversion passed")
    exit(0)
}
guard CommandLine.arguments.count == 4, let rate = Double(CommandLine.arguments[1]),
      rate >= 8000 && rate <= 96000 else { fail("Expected playback rate, ducking level, and AGC flag") }
func startRoom() {
    let room: Room
    do {
        room = try Room(rate: rate, ducking: CommandLine.arguments[2], agc: CommandLine.arguments[3] == "true")
    } catch {
        fail("Apple audio could not start: \(error.localizedDescription)")
    }
    DispatchQueue(label: "ciel.audio.commands").async {
        while let header = readExactly(9) {
            let kind = header[0]
            let id = header.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: 1, as: UInt32.self)) }
            let count = header.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: 5, as: UInt32.self)) }
            guard count <= 65536, let payload = readExactly(Int(count)) else { exit(1) }
            DispatchQueue.main.async {
                switch kind {
                case playKind: room.play(id, payload)
                case stopKind where payload.isEmpty: room.stop()
                case beginKind where payload.isEmpty && room.pending.isEmpty: room.continuity.begin()
                case endKind where payload.isEmpty: room.continuity.end()
                default: fail("Unknown Apple audio command")
                }
            }
        }
        exit(0)
    }
}
switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .authorized: startRoom()
case .notDetermined:
    AVCaptureDevice.requestAccess(for: .audio) { allowed in
        DispatchQueue.main.async {
            if allowed { startRoom() }
            else { fail("Allow Ciel microphone access in System Settings > Privacy & Security > Microphone") }
        }
    }
default: fail("Allow Ciel microphone access in System Settings > Privacy & Security > Microphone")
}
RunLoop.main.run()
