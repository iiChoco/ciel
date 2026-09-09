// A nonmuting device tap and the microphone share one Core Audio clock.
// No voice-processing unit or output gain is used. The three float channels
// sent to Python are microphone, reference left, reference right at 16 kHz.
import AVFoundation
import CoreAudio
import Foundation
import Darwin

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}
func checked(_ status: OSStatus, _ operation: String) throws {
    if status != noErr { throw NSError(domain: operation, code: Int(status)) }
}
func property<T: BitwiseCopyable>(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector, _ initial: T, scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> T {
    var value = initial
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    try withUnsafeMutablePointer(to: &value) { pointer in
        try checked(AudioObjectGetPropertyData(id, &address, 0, nil, &size, pointer), "read audio property")
    }
    return value
}
func stringProperty(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> String {
    var value: CFString = "" as CFString
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: 0)
    var size = UInt32(MemoryLayout<CFString>.size)
    try withUnsafeMutablePointer(to: &value) { pointer in
        try checked(AudioObjectGetPropertyData(id, &address, 0, nil, &size, pointer), "read audio UID")
    }
    return value as String
}
func channels(_ id: AudioObjectID) throws -> [Int] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: kAudioObjectPropertyScopeInput, mElement: 0)
    var size: UInt32 = 0
    try checked(AudioObjectGetPropertyDataSize(id, &address, 0, nil, &size), "read channel layout")
    let storage = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
    defer { storage.deallocate() }
    try checked(AudioObjectGetPropertyData(id, &address, 0, nil, &size, storage), "read channel layout")
    return UnsafeMutableAudioBufferListPointer(storage.assumingMemoryBound(to: AudioBufferList.self)).map { Int($0.mNumberChannels) }
}
func send(_ kind: UInt32, _ payload: Data) {
    var k = kind.littleEndian, n = UInt32(payload.count).littleEndian
    var data = Data(bytes: &k, count: 4)
    data.append(Data(bytes: &n, count: 4)); data.append(payload)
    data.withUnsafeBytes { bytes in
        var offset = 0
        while offset < bytes.count {
            let count = Darwin.write(STDOUT_FILENO, bytes.baseAddress! + offset, bytes.count - offset)
            if count < 0 && errno == EINTR { continue }
            if count <= 0 { exit(0) }
            offset += count
        }
    }
}

final class Ring {
    let lock = NSLock(), signal = DispatchSemaphore(value: 0)
    let capacity = 4096, slots = 32
    let storage: UnsafeMutablePointer<Float>
    var sizes = [Int](repeating: 0, count: 32)
    var times = [Double](repeating: 0, count: 32)
    var head = 0, count = 0
    let failed = DispatchSemaphore(value: 0)
    let micChannels: Int, expected: [Int]
    init(_ expected: [Int], _ micChannels: Int) {
        self.expected = expected; self.micChannels = micChannels
        storage = .allocate(capacity: 32 * 4096 * 3)
        storage.initialize(repeating: 0, count: 32 * 4096 * 3)
    }
    deinit { storage.deallocate() }
    func push(_ data: UnsafePointer<AudioBufferList>, _ timestamp: Double) {
        // A late callback loses all three channels together. Its timestamp gap
        // resets the paired DSP on the worker; the real-time thread never waits.
        guard lock.try() else { return }
        defer { lock.unlock(); signal.signal() }
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: data))
        guard buffers.count == expected.count else { failed.signal(); return }
        guard count < slots else { return }
        var frames = 0
        for (i, b) in buffers.enumerated() {
            guard b.mNumberChannels == expected[i], b.mData != nil, b.mDataByteSize % UInt32(4 * expected[i]) == 0 else { failed.signal(); return }
            let n = Int(b.mDataByteSize) / (4 * expected[i])
            if i == 0 { frames = n }
            guard n == frames, n > 0, n <= capacity else { failed.signal(); return }
        }
        let slot = (head + count) % slots
        let dest = storage + slot * capacity * 3
        var channel = 0
        for b in buffers {
            let source = b.mData!.assumingMemoryBound(to: Float.self)
            let width = Int(b.mNumberChannels)
            for c in 0..<width {
                let absolute = channel + c
                let target = absolute == 0 ? 0 : absolute == micChannels ? 1 : absolute == micChannels + 1 ? 2 : -1
                if target >= 0 {
                    for i in 0..<frames { dest[i * 3 + target] = source[i * width + c] }
                }
            }
            channel += width
        }
        sizes[slot] = frames; times[slot] = timestamp; count += 1
    }
    func pop(_ target: UnsafeMutablePointer<Float>) -> (Int, Double)? {
        if failed.wait(timeout: .now()) == .success { die("Capture layout invalid or missing reference; echo protection stopped") }
        lock.lock()
        if count == 0 { lock.unlock(); return nil }
        let slot = head, frames = sizes[head], time = times[head]
        lock.unlock()
        // Keep the slot occupied until the copy finishes, without holding
        // the callback's lock across a potentially large memory copy.
        memcpy(target, storage + slot * capacity * 3, frames * 3 * 4)
        lock.lock()
        head = (head + 1) % slots; count -= 1
        lock.unlock()
        return (frames, time)
    }
}

final class Timeline {
    var expected: Double?
    func advance(_ frames: Int, _ time: Double) -> Bool {
        guard time.isFinite else { die("Invalid capture timestamp; echo protection stopped") }
        let gap = expected.map { abs(time - $0) > 0.5 } ?? false
        expected = time + Double(frames)
        return gap
    }
}

final class Resampler {
    let converter: AVAudioConverter
    let source: AVAudioPCMBuffer
    let target: AVAudioPCMBuffer
    init(_ rate: Double) {
        let layout = AVAudioChannelLayout(layoutTag: kAudioChannelLayoutTag_DiscreteInOrder | 3)!
        let from = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, interleaved: false, channelLayout: layout)
        let to = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, interleaved: false, channelLayout: layout)
        guard let converter = AVAudioConverter(from: from, to: to),
              let source = AVAudioPCMBuffer(pcmFormat: from, frameCapacity: 4096),
              let target = AVAudioPCMBuffer(pcmFormat: to, frameCapacity: 8192) else { die("Cannot create paired audio resampler") }
        self.converter = converter; self.source = source; self.target = target
        converter.primeMethod = .none
    }
    func convert(_ samples: UnsafePointer<Float>, _ frames: Int) -> Data {
        source.frameLength = AVAudioFrameCount(frames)
        for c in 0..<3 { for i in 0..<frames { source.floatChannelData![c][i] = samples[i * 3 + c] } }
        var supplied = false, error: NSError?
        let status = converter.convert(to: target, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true; state.pointee = .haveData; return self.source
        }
        if status == .error || error != nil { die("Paired audio conversion failed") }
        let n = Int(target.frameLength)
        var packet = Data(count: n * 3 * 4)
        packet.withUnsafeMutableBytes { raw in
            let out = raw.bindMemory(to: Float.self)
            for c in 0..<3 { for i in 0..<n { out[i * 3 + c] = target.floatChannelData![c][i] } }
        }
        return packet
    }
}

@available(macOS 14.2, *)
final class Capture {
    var tap: AudioObjectID = 0, aggregate: AudioObjectID = 0
    var io: AudioDeviceIOProcID?
    var started = false
    var ring: Ring?
    var observers: [(AudioObjectID, AudioObjectPropertyAddress, AudioObjectPropertyListenerBlock)] = []
    func start() throws {
        let system = AudioObjectID(kAudioObjectSystemObject)
        let input: AudioObjectID = try property(system, kAudioHardwarePropertyDefaultInputDevice, UInt32(0))
        let output: AudioObjectID = try property(system, kAudioHardwarePropertyDefaultOutputDevice, UInt32(0))
        let inputUID = try stringProperty(input, kAudioDevicePropertyDeviceUID)
        let outputUID = try stringProperty(output, kAudioDevicePropertyDeviceUID)
        let inputChannels = try channels(input).reduce(0, +)
        guard inputChannels > 0 else { die("Default microphone has no input channels") }
        let description = CATapDescription(excludingProcesses: [], deviceUID: outputUID, stream: 0)
        description.name = "Ciel echo reference"; description.isPrivate = true
        description.muteBehavior = .unmuted
        try checked(AudioHardwareCreateProcessTap(description, &tap), "create nonmuting output tap")
        let format: AudioStreamBasicDescription = try property(tap, kAudioTapPropertyFormat, AudioStreamBasicDescription())
        guard format.mFormatID == kAudioFormatLinearPCM, format.mChannelsPerFrame == 2, format.mBitsPerChannel == 32, format.mFormatFlags & kAudioFormatFlagIsFloat != 0 else {
            die("Echo reference requires a stereo float output device")
        }
        let composition: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Ciel microphone and echo reference",
            kAudioAggregateDeviceUIDKey: "ai.ciel.capture." + UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceMainSubDeviceKey: inputUID,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: inputUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString, kAudioSubTapDriftCompensationKey: true]],
            kAudioAggregateDeviceTapAutoStartKey: true,
        ]
        try checked(AudioHardwareCreateAggregateDevice(composition as CFDictionary, &aggregate), "create synchronized capture device")
        let rate: Double = try property(aggregate, kAudioDevicePropertyNominalSampleRate, Double(0))
        let layout = try channels(aggregate)
        guard layout.reduce(0, +) == inputChannels + 2, rate >= 16000, rate <= 96000 else { die("Unsupported aggregate capture layout") }
        // Validate the aggregate's actual input streams, not just the tap format.
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: kAudioObjectPropertyScopeInput, mElement: 0)
        var size: UInt32 = 0
        try checked(AudioObjectGetPropertyDataSize(aggregate, &address, 0, nil, &size), "read capture streams")
        var streams = [AudioStreamID](repeating: 0, count: Int(size) / 4)
        try checked(AudioObjectGetPropertyData(aggregate, &address, 0, nil, &size, &streams), "read capture streams")
        for stream in streams {
            let f: AudioStreamBasicDescription = try property(stream, kAudioStreamPropertyVirtualFormat, AudioStreamBasicDescription())
            guard f.mFormatID == kAudioFormatLinearPCM, f.mBitsPerChannel == 32, f.mFormatFlags & kAudioFormatFlagIsFloat != 0, f.mSampleRate == rate else { die("Unsupported capture stream format") }
        }
        let ring = Ring(layout, inputChannels); self.ring = ring
        try checked(AudioDeviceCreateIOProcIDWithBlock(&io, aggregate, nil) { _, data, time, _, _ in
            ring.push(data, time.pointee.mSampleTime)
        }, "create capture callback")
        let resampler = Resampler(rate)
        for (id, selector) in [(system, kAudioHardwarePropertyDefaultInputDevice), (system, kAudioHardwarePropertyDefaultOutputDevice), (aggregate, kAudioDevicePropertyNominalSampleRate), (aggregate, kAudioDevicePropertyDeviceIsAlive)] {
            var a = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: 0)
            let block: AudioObjectPropertyListenerBlock = { _, _ in
                do {
                    let changed: Bool
                    if selector == kAudioDevicePropertyNominalSampleRate {
                        changed = try property(id, selector, Double(0)) != rate
                    } else {
                        let original = selector == kAudioHardwarePropertyDefaultInputDevice ? input : selector == kAudioHardwarePropertyDefaultOutputDevice ? output : UInt32(1)
                        changed = try property(id, selector, UInt32(0)) != original
                    }
                    if changed { die("Audio route changed; reopen protected capture") }
                } catch { die("Audio route unavailable; reopen protected capture") }
            }
            try checked(AudioObjectAddPropertyListenerBlock(id, &a, .main, block), "observe audio route")
            observers.append((id, a, block))
        }
        send(1, try JSONSerialization.data(withJSONObject: ["rate": 16000, "channels": 3, "hardware_rate": rate, "nonmuting": true, "layout": layout]))
        DispatchQueue(label: "ciel.capture.writer").async {
            let scratch = UnsafeMutablePointer<Float>.allocate(capacity: 4096 * 3)
            defer { scratch.deallocate() }
            let timeline = Timeline()
            while true {
                ring.signal.wait()
                while let (frames, time) = ring.pop(scratch) {
                    if timeline.advance(frames, time) {
                        // Neither resampler history nor the echo filter's
                        // delayed mic may cross a missing interval.
                        resampler.converter.reset()
                        send(3, Data("discontinuity".utf8))
                    }
                    let packet = resampler.convert(scratch, frames)
                    if !packet.isEmpty { send(2, packet) }
                }
            }
        }
        try checked(AudioDeviceStart(aggregate, io), "start capture; allow Microphone and System Audio Recording in System Settings")
        started = true
    }
    func close() {
        if started { AudioDeviceStop(aggregate, io); started = false }
        if let io { AudioDeviceDestroyIOProcID(aggregate, io) }; io = nil
        for (id, address, block) in observers { var a = address; AudioObjectRemovePropertyListenerBlock(id, &a, .main, block) }
        observers.removeAll()
        if aggregate != 0 { AudioHardwareDestroyAggregateDevice(aggregate); aggregate = 0 }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap); tap = 0 }
    }
}
if CommandLine.arguments.contains("--self-test") || CommandLine.arguments.contains("--self-test-contention") || CommandLine.arguments.contains("--self-test-overflow") || CommandLine.arguments.contains("--self-test-invalid") {
    let ring = Ring([3, 2], 3)
    let buffers = AudioBufferList.allocate(maximumBuffers: 2)
    let mic = UnsafeMutablePointer<Float>.allocate(capacity: 48)
    let ref = UnsafeMutablePointer<Float>.allocate(capacity: 32)
    for i in 0..<16 { mic[i * 3] = 0.1; mic[i * 3 + 1] = 0.9; mic[i * 3 + 2] = 0.8; ref[i * 2] = 0.2; ref[i * 2 + 1] = -0.3 }
    buffers[0] = AudioBuffer(mNumberChannels: 3, mDataByteSize: 192, mData: mic)
    buffers[1] = AudioBuffer(mNumberChannels: 2, mDataByteSize: 128, mData: ref)
    let scratch = UnsafeMutablePointer<Float>.allocate(capacity: 4096 * 3)
    if CommandLine.arguments.contains("--self-test-contention") {
        let timeline = Timeline()
        ring.push(UnsafePointer(buffers.unsafePointer), 0)
        let first = ring.pop(scratch)!
        precondition(!timeline.advance(first.0, first.1))
        ring.lock.lock(); ring.push(UnsafePointer(buffers.unsafePointer), 16); ring.lock.unlock()
        precondition(ring.pop(scratch) == nil)
        ring.push(UnsafePointer(buffers.unsafePointer), 32)
        let next = ring.pop(scratch)!
        precondition(timeline.advance(next.0, next.1))
        precondition(!timeline.advance(16, 48))
        precondition(timeline.advance(16, 0))
        print("contention discards paired samples and marks one recovery boundary; passed")
        exit(0)
    }
    if CommandLine.arguments.contains("--self-test-overflow") {
        let timeline = Timeline()
        for i in 0...32 { ring.push(UnsafePointer(buffers.unsafePointer), Double(i * 16)) }
        for i in 0..<32 {
            let next = ring.pop(scratch)!
            precondition(next.1 == Double(i * 16) && !timeline.advance(next.0, next.1))
        }
        precondition(ring.pop(scratch) == nil)
        ring.push(UnsafePointer(buffers.unsafePointer), 33 * 16)
        let next = ring.pop(scratch)!
        precondition(timeline.advance(next.0, next.1))
        print("overflow preserves paired queued frames and marks the missing interval; passed")
        exit(0)
    }
    if CommandLine.arguments.contains("--self-test-invalid") {
        buffers[1].mData = nil
        ring.push(UnsafePointer(buffers.unsafePointer), 0)
        _ = ring.pop(scratch); exit(2)
    }
    ring.push(UnsafePointer(buffers.unsafePointer), 10)
    let item = ring.pop(scratch)!
    precondition(item.0 == 16 && item.1 == 10)
    for i in 0..<16 { precondition(scratch[i * 3] == 0.1 && scratch[i * 3 + 1] == 0.2 && scratch[i * 3 + 2] == -0.3) }
    for rate in [44100.0, 48000.0] {
        let converter = Resampler(rate)
        for i in 0..<4096 { scratch[i * 3] = 0.1; scratch[i * 3 + 1] = 0.2; scratch[i * 3 + 2] = -0.3 }
        var output = Data(), remaining = Int(rate)
        while remaining > 0 { let n = min(512, remaining); output.append(converter.convert(scratch, n)); remaining -= n }
        precondition(abs(output.count / 12 - 16000) < 20)
        output.withUnsafeBytes { bytes in
            let samples = bytes.bindMemory(to: Float.self)
            for i in 1000..<output.count / 12 - 100 {
                precondition(abs(samples[i * 3] - 0.1) < 0.001 && abs(samples[i * 3 + 1] - 0.2) < 0.001 && abs(samples[i * 3 + 2] + 0.3) < 0.001)
            }
        }
    }
    mic.deallocate(); ref.deallocate(); scratch.deallocate(); buffers.unsafeMutablePointer.deallocate()
    print("paired channel mapping and 44.1/48 kHz resampling passed")
    exit(0)
}
signal(SIGPIPE, SIG_IGN)
guard #available(macOS 14.2, *) else { die("Nonducking echo capture requires macOS 14.2 or later") }
switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .authorized: break
case .notDetermined:
    let permission = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .audio) { _ in permission.signal() }
    permission.wait()
    guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { die("Microphone permission denied; allow Ciel in System Settings") }
default: die("Microphone permission denied; allow Ciel in System Settings")
}
let capture = Capture()
do { try capture.start() } catch { capture.close(); die("Capture failed: \(error)") }
DispatchQueue(label: "ciel.capture.parent").async {
    var byte: UInt8 = 0
    _ = Darwin.read(STDIN_FILENO, &byte, 1)
    DispatchQueue.main.async { capture.close(); exit(0) }
}
let stop = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
signal(SIGTERM, SIG_IGN)
stop.setEventHandler { capture.close(); exit(0) }; stop.resume()
RunLoop.main.run()
