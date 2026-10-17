// JVM contract for the Kotlin high-level client and license binding (no Android, no hardware).
import com.carenco.fisica.*
import com.sun.net.httpserver.HttpServer
import java.io.File
import java.net.InetSocketAddress
import java.security.MessageDigest
import java.util.Base64
import java.util.Properties
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

const val DAY = 86400L
const val ALL_FEATURES = FisicaFeature.RAW_DATA or FisicaFeature.EXPORT or FisicaFeature.ADVANCED or FisicaFeature.FULL_RATE

fun hex(text: String) = ByteArray(text.length / 2) { text.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
fun <T> await(name: String, body: ((T?, Throwable?) -> Unit) -> Unit): Pair<T?, Throwable?> {
    val latch = CountDownLatch(1); var result: Pair<T?, Throwable?> = null to null
    body { value, error -> result = value to error; latch.countDown() }
    check(latch.await(5, TimeUnit.SECONDS)) { "$name did not complete" }
    return result
}
fun waitFor(name: String, condition: () -> Boolean) {
    val deadline = System.nanoTime() + 5_000_000_000
    while (!condition()) { check(System.nanoTime() < deadline) { "$name timed out" }; Thread.sleep(10) }
}
fun ack(command: ByteArray) = byteArrayOf(0x24, command[1], command[2], 1, 0, 0, 0xff.toByte(), 0xf0.toByte())
fun scale(core: FisicaCore, packet: ByteArray, frames: Int = 12): MockTransport {
    val transport = MockTransport()
    transport.reply(core.command("GET_FIRMWARE_VERSION"), hex("24434701322e302e31fff0"))
    for (name in listOf("SET_DEVICE_USB", "SET_DEVICE_BLE", "START_CONTINUOUS_MEASUREMENT", "STOP_CONTINUOUS_MEASUREMENT", "SET_DEVICE_STOP")) {
        val command = core.command(name)
        var reply = ack(command)
        if (name == "START_CONTINUOUS_MEASUREMENT") repeat(frames) { reply += packet }
        transport.reply(command, reply)
    }
    return transport
}
/** One measurement over a mock Scale; returns the client (still connected) and the frames it handed out. */
fun measure(core: FisicaCore, transport: MockTransport, license: FisicaEntitlement?): Pair<FisicaClient, Int> {
    val client = FisicaClient(core, transport, license)
    var received = 0; client.onFrame = { received++ }
    check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
    check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
    check(await<SessionEvent>("stop") { client.stop(it) }.second == null)
    return client to received
}
/** A connected client the app forgets to close; only the returned weak reference remains. */
fun abandon(core: FisicaCore, packet: ByteArray): java.lang.ref.WeakReference<FisicaClient> {
    val client = FisicaClient(core, scale(core, packet))
    check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
    return java.lang.ref.WeakReference(client)
}
fun clientThreads() = Thread.getAllStackTraces().keys.count { it.name == "fisica-client" && it.isAlive }
fun sha256(text: String) = MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }
/** The developer portal's check of an activation (FAC1) or release (FRC1) code; returns its JSON body. */
fun readCode(code: String, prefix: String): String {
    val parts = code.split('.')
    check(parts.size == 3 && parts[0] == prefix) { "Not a $prefix code: $code" }
    val material = (if (prefix == "FRC1") "fisica-release-code-v1|" else "") + "$prefix.${parts[1]}"
    check(parts[2] == sha256(material).substring(0, 8)) { "Wrong check value in $code" }
    return String(Base64.getUrlDecoder().decode(parts[1]), Charsets.UTF_8)
}

fun main(args: Array<String>) {
    val values = Properties().apply { File(args[1]).inputStream().use { load(it) } }
    val core = FisicaCore(args[0]) { System.err.println(it) }
    val packet = hex(values.getProperty("packet"))
    val now = values.getProperty("now").toLong()
    // Command-line programs are bound to the PC; the core reads its hash itself.
    val target = values.getProperty("target")
    val targetName = values.getProperty("target_type")
    val targetType = if (targetName == "pc") FisicaBindType.PC else FisicaBindType.APP
    val seat = "{\"k\":\"key_test_1\",\"t\":\"$targetName\",\"b\":\"$target\"}"
    fun vector(name: String): String = values.getProperty(name) ?: error("Missing vector $name")
    fun license(name: String? = null, storage: File? = null, clock: () -> Long = { now }) =
        FisicaEntitlement(core, null, storage, true, clock).also { context ->
            if (name != null) check(context.importToken(vector(name)) == FisicaEntitlementOutcome.ACCEPTED) { name }
        }
    val temporary = java.nio.file.Files.createTempDirectory("fisica-kotlin-license").toFile()

    // Basic mode (no key): connect -> measure -> stop -> report -> close. The 12 packets arrive
    // within one second, so one frame without raw data; the core still analyzes every packet.
    run {
        val transport = scale(core, packet)
        val (client, received) = measure(core, transport, null)
        check(client.firmwareVersion == "2.0.1" && client.state == FisicaSessionState.IDLE && client.licenseFeatures == 0)
        check(received == 1 && client.frames.size == 1)
        val frame = client.frames[0]
        check(frame.raw == null && frame.packet == null && !frame.hasRawData && frame.display.size == 841 && frame.weight == 70.5)
        val report = client.report()!!
        check(report.weight == 70.5 && report.frameCount == 1) { "basic report ${report.weight}" }
        check(await<SessionEvent>("close") { client.close(it) }.second == null)
        check(!transport.isOpen && transport.sent.last().contentEquals(core.command("SET_DEVICE_STOP")))
        check(transport.sent.any { it.contentEquals(core.command("SET_DEVICE_USB")) })
        check(client.report()!!.weight == 70.5)  // the analysis outlives the closed session
    }
    // Full mode: every packet as a frame, with the raw grid and the packet as received.
    run {
        val license = license("full"); val transport = scale(core, packet)
        val (client, received) = measure(core, transport, license)
        check(received == 12 && client.frames.size == 12 && client.licenseFeatures == ALL_FEATURES)
        val reference = core.decode(packet, license)
        for (frame in client.frames) {
            check(frame.packet!!.contentEquals(packet) && frame.raw!!.contentEquals(reference.raw!!))
            check(frame.display.contentEquals(reference.display) && frame.weight == reference.weight)
        }
        val report = client.report()!!
        check(report.weight == 70.5 && report.frameCount == 12 && report.frames[0].raw!!.size == 638)
        await<SessionEvent>("close") { client.close(it) }
        license.close()
    }
    // A key whose features leave out raw data: every frame, but no raw grid or packet.
    run {
        val license = license("limited"); val transport = scale(core, packet)
        val (client, received) = measure(core, transport, license)
        check(client.licenseFeatures == FisicaFeature.EXPORT or FisicaFeature.FULL_RATE && !license.hasFeature(FisicaFeature.RAW_DATA))
        check(received == 12 && client.frames.all { !it.hasRawData } && client.report()!!.weight == 70.5)
        await<SessionEvent>("close") { client.close(it) }
        license.close()
    }
    // The client picks up a license change during a measurement, and a release.
    run {
        val license = license(); val transport = scale(core, packet)
        val client = FisicaClient(core, transport, license)
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        waitFor("basic frame") { client.frames.size == 1 }
        check(client.licenseFeatures == 0)
        check(license.importToken(vector("full")) == FisicaEntitlementOutcome.ACCEPTED)
        waitFor("license applied") { client.licenseFeatures == ALL_FEATURES }
        repeat(5) { transport.feed(packet) }
        waitFor("full-rate frames") { client.frames.size == 6 }
        check(client.frames.drop(1).all { it.hasRawData })
        await<SessionEvent>("stop") { client.stop(it) }
        // Started in basic mode: the core analyzed all 17 packets.
        check(client.report()!!.weight == 70.5)
        check(readCode(license.release(), "FRC1") == seat)
        waitFor("basic mode again") { client.licenseFeatures == 0 }
        await<SessionEvent>("close") { client.close(it) }
        license.close()
    }
    // Frames by mode on the session's own clock: basic at most one per 1000 ms, full every packet.
    run {
        val full = license("full")
        val reference = core.decode(packet, full)
        for ((context, expected) in listOf(null to 3, full to 10)) {
            val session = FisicaSession(core)
            try {
                var time = 1000L
                session.action(FisicaSessionAction.CONNECTED, FisicaLink.USB.toLong(), time)
                session.feed(hex("24434701322e302e31fff0"), time)
                check(session.license(context, time) == (if (context == null) 0 else ALL_FEATURES))
                session.action(FisicaSessionAction.START, -1, time)
                for (name in listOf("SET_DEVICE_USB", "START_CONTINUOUS_MEASUREMENT")) session.feed(ack(core.command(name)), time)
                session.drain()
                val frames = mutableListOf<Measurement>()
                repeat(10) {
                    time += 300; session.feed(packet, time)
                    session.drain().filter { it.kind == FisicaSessionEvent.MEASUREMENT }.mapTo(frames) { Measurement.fromEvent(it) }
                }
                check(frames.size == expected) { "${frames.size} frames, expected $expected" }
                check(frames.all { it.weight == reference.weight && it.display.contentEquals(reference.display) && it.hasRawData == (context != null) })
                if (context != null) check(frames[0].packet!!.contentEquals(packet) && frames[0].raw!!.contentEquals(reference.raw!!))
                check(session.analyze()!![0] == reference.weight)  // every packet, in every mode
            } finally { session.close() }
        }
        check(runCatching { core.decode(packet, null) }.exceptionOrNull() is FisicaLicenseException)
        full.close()
    }
    // Packets carry no battery field: the level comes from GET_BATTERY_LEVEL, idle or measuring.
    run {
        val transport = scale(core, packet); val battery = core.command("GET_BATTERY_LEVEL")
        transport.reply(battery, hex("243147010050fff0"))
        val client = FisicaClient(core, transport)
        check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
        check(client.battery == null && transport.sent.none { it.contentEquals(battery) })
        check(await<Int>("battery") { client.readBattery(it) }.first == 80 && client.battery == 80)
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        check(await<Int>("battery while measuring") { client.readBattery(it) }.first == 80 && client.state == FisicaSessionState.MEASURING)
        check(await<SessionEvent>("stop") { client.stop(it) }.second == null)
        val report = client.report()!!
        check(report.battery == 80 && report.batteryValid && !report.frames[0].batteryValid)
        transport.reply(battery, hex("243147000001fff0"))
        check(await<Int>("refused battery") { client.readBattery(it) }.second is FisicaCommandException)
        check(client.state == FisicaSessionState.IDLE)
        check(await<SessionEvent>("start after refusal") { client.start(completion = it) }.second == null)
        await<SessionEvent>("close") { client.close(it) }
    }
    // A transport that never becomes ready fails instead of hanging.
    run {
        val transport = MockTransport().apply { becomesReady = false }; val client = FisicaClient(core, transport)
        val error = await<SessionEvent>("timeout") { client.connect(FisicaLink.BLE, 200, it) }.second
        check((error as FisicaException).code == 1001 && !transport.isOpen)
        client.close()
    }
    // A rejected start is an error carrying the device code; close still releases.
    run {
        val transport = scale(core, packet); val start = core.command("START_CONTINUOUS_MEASUREMENT")
        transport.reply(start, byteArrayOf(0x24, start[1], start[2], 0, 0, 7, 0xff.toByte(), 0xf0.toByte()))
        val client = FisicaClient(core, transport)
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        val error = await<SessionEvent>("start") { client.start(completion = it) }.second as FisicaCommandException
        check(error.deviceCode == 7 && error.code == FisicaStatus.DEVICE_REJECTED)
        await<SessionEvent>("close") { client.close(it) }
        check(!transport.isOpen)
    }
    // Link loss asks the host to reconnect the same Scale.
    run {
        val transport = scale(core, packet); val client = FisicaClient(core, transport)
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        val lost = CountDownLatch(1); client.onLinkLost = { lost.countDown() }
        transport.drop(); check(lost.await(5, TimeUnit.SECONDS) && client.state == FisicaSessionState.RECOVERING)
        check(await<SessionEvent>("reconnect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
        check(client.state == FisicaSessionState.IDLE)
        await<SessionEvent>("close") { client.close(it) }
    }
    // The link comes from the transport; a different one fails before anything is sent.
    run {
        val transport = scale(core, packet).apply { link = FisicaLink.USB }; val client = FisicaClient(core, transport)
        val error = await<SessionEvent>("mismatch") { client.connect(FisicaLink.BLE, completion = it) }.second
        check((error as FisicaException).code == FisicaStatus.INVALID_ARGUMENT && !transport.isOpen && transport.sent.isEmpty())
        check(await<SessionEvent>("connect") { client.connect(completion = it) }.second == null)
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        check(transport.sent.any { it.contentEquals(core.command("SET_DEVICE_USB")) })
        await<SessionEvent>("close") { client.close(it) }
    }
    // A transport that does not declare its link must be given one; nothing is sent without it.
    run {
        val transport = scale(core, packet); val client = FisicaClient(core, transport)
        val error = await<SessionEvent>("missing link") { client.connect(completion = it) }.second
        check((error as FisicaException).code == FisicaStatus.INVALID_ARGUMENT && !transport.isOpen && transport.sent.isEmpty())
        check(await<SessionEvent>("given link") { client.connect(FisicaLink.BLE, completion = it) }.second == null)
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        check(transport.sent.any { it.contentEquals(core.command("SET_DEVICE_BLE")) })
        await<SessionEvent>("close") { client.close(it) }
    }
    // Callbacks and completions that throw reach onError; every call completes, also after close.
    run {
        val transport = scale(core, packet); val client = FisicaClient(core, transport)
        val errors = java.util.concurrent.ConcurrentLinkedQueue<String>()
        client.onError = { errors.add(it.message ?: ""); throw IllegalStateException("error handler") }
        client.onFrame = { throw IllegalStateException("frame handler") }
        client.onEvent = { throw IllegalStateException("event handler") }
        check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        val stopped = CountDownLatch(1)
        client.stop { _, _ -> stopped.countDown(); throw IllegalStateException("completion") }
        check(stopped.await(5, TimeUnit.SECONDS))
        waitFor("completion error") { "completion" in errors }
        check(errors.containsAll(listOf("frame handler", "event handler")) && "error handler" !in errors) { "$errors" }
        client.clearFrames()
        check(await<SessionEvent>("close") { client.close(it) }.second == null)
        // The report comes from the core's analysis, not from the frames, and outlives the session.
        check(client.frames.isEmpty() && client.report()!!.weight == 70.5)
        fun cancelled(error: Throwable?) = (error as? FisicaException)?.code == FisicaStatus.CANCELLED
        check(cancelled(await<SessionEvent>("connect after close") { client.connect(FisicaLink.USB, completion = it) }.second))
        check(cancelled(await<SessionEvent>("start after close") { client.start(completion = it) }.second))
        check(cancelled(await<SessionEvent>("stop after close") { client.stop(it) }.second))
        check(cancelled(await<Int>("battery after close") { client.readBattery(it) }.second))
        check(await<SessionEvent>("close after close") { client.close(it) }.let { it.first == null && it.second == null })
    }
    // A second close while the first waits for the Scale is Busy; the first still releases everything.
    run {
        val transport = scale(core, packet).apply { reply(core.command("SET_DEVICE_STOP")) }  // never confirmed
        val client = FisicaClient(core, transport)
        check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        check(await<SessionEvent>("stop") { client.stop(it) }.second == null)
        val first = CountDownLatch(1)
        client.close { _, _ -> first.countDown() }
        val second = await<SessionEvent>("second close") { client.close(it) }.second
        check((second as FisicaException).code == FisicaStatus.BUSY)
        check(first.await(5, TimeUnit.SECONDS) && !transport.isOpen && client.report()!!.weight == 70.5)
    }
    // A client dropped without close(): the ticker does not keep it alive, so it is collected, its
    // thread ends and its native session is freed.
    run {
        val threads = clientThreads(); val sessions = NativeCleaner.pending
        val dropped = abandon(core, packet)
        val deadline = System.nanoTime() + 10_000_000_000
        while (dropped.get() != null || NativeCleaner.pending > sessions || clientThreads() > threads) {
            check(System.nanoTime() < deadline) {
                "abandoned client kept: ${dropped.get() != null}, sessions ${NativeCleaner.pending}/$sessions, threads ${clientThreads()}/$threads"
            }
            System.gc(); Thread.sleep(50)
        }
    }
    // Start clears the previous measurement; frames stay bounded (full mode: every packet is a frame).
    run {
        val license = license("full")
        val transport = scale(core, packet, frames = 8); val client = FisicaClient(core, transport, license)
        check(runCatching { client.maxFrames = FisicaLimit.ANALYSIS_FRAMES + 1 }.isFailure)
        client.maxFrames = 5
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        await<SessionEvent>("stop") { client.stop(it) }
        check(client.frames.size == 5 && client.droppedFrames == 3 && client.report()!!.weight == 70.5)
        val start = core.command("START_CONTINUOUS_MEASUREMENT")
        transport.reply(start, ack(start))
        check(await<SessionEvent>("restart") { client.start(completion = it) }.second == null)
        check(client.frames.isEmpty() && client.droppedFrames == 0)
        await<SessionEvent>("close") { client.close(it) }
        license.close()
    }
    // No key: basic mode. The core reads this PC itself; rejected keys never become the license.
    run {
        val context = license()
        var status = context.status()
        check(status.mode == FisicaMode.BASIC && status.reason == FisicaModeReason.NO_KEY && status.reasonName == "no_key")
        check(status.features == 0 && status.company == null && status.key == null && status.activationCode == null && status.message != null)
        check(status.targetType == targetType && status.target == target && context.activationCode() == null)
        check(!context.hasFeature(FisicaFeature.RAW_DATA) && status.toString() == "basic mode (no_key)")
        check(String(context.exportState()) == "FSS1\nT $now\n")
        check(context.importToken(vector("tampered")) == FisicaEntitlementOutcome.REJECTED_SIGNATURE)
        check(context.importToken(vector("unknown_key")) == FisicaEntitlementOutcome.REJECTED_KEY)
        check(context.importToken("not a key") == FisicaEntitlementOutcome.REJECTED_FORMAT)
        status = context.status()
        check(status.reason == FisicaModeReason.NO_KEY && status.company == null && status.warnings and FisicaEntitlementWarning.IMPORT_REJECTED != 0)
        // A contract confirmation is not a seat key.
        check(context.importToken(vector("legacy")) == FisicaEntitlementOutcome.ACCEPTED)
        status = context.status()
        check(!status.isFull && status.reason == FisicaModeReason.LEGACY_TOKEN && status.contract == FisicaContract.ACTIVE && status.company == "acme")
        context.close()
    }
    // A key not activated yet: basic mode, with the FAC1 code the developer portal accepts.
    run {
        val context = license("unbound")
        val status = context.status()
        check(!status.isFull && status.reason == FisicaModeReason.NOT_ACTIVATED && status.bindType == FisicaBindType.NONE)
        check(status.key == "key_test_1" && status.track == FisicaTrack.TRIAL && status.fullUntil == now + 14 * DAY)
        check(status.keyEnvironment == FisicaKeyEnvironment.TEST && status.warnings and FisicaEntitlementWarning.TEST_KEY != 0)
        check(readCode(status.activationCode!!, "FAC1") == seat && context.activationCode() == status.activationCode)
        check(context.importToken(vector("unbound_older")) == FisicaEntitlementOutcome.IGNORED_OLDER)
        check((runCatching { context.release() }.exceptionOrNull() as FisicaException).code == FisicaStatus.INVALID_STATE)
        context.close()
    }
    // A key activated for this PC: full mode with every feature until the trial ends.
    run {
        var clock = now
        val context = license("full", clock = { clock })
        var warned = 0; var notices = 0
        context.onWarning = { bits, _ -> warned = warned or bits }; context.onStatus = { notices++ }
        var status = context.status()
        check(status.isFull && status.reason == FisicaModeReason.NONE && status.reasonName == null && status.message == null)
        check(status.features == ALL_FEATURES && status.hasFeature(FisicaFeature.EXPORT or FisicaFeature.ADVANCED) && context.hasFeature(FisicaFeature.RAW_DATA))
        check(status.bindType == targetType && status.bind == target && status.key == "key_test_1" && status.track == FisicaTrack.TRIAL)
        check(status.company == "acme" && status.contractReference == "ctr-1" && status.verifiedAt == now && status.activationCode == null)
        check(status.keyUntil == now + 90 * DAY && status.fullUntil == now + 14 * DAY && status.toString() == "full mode")
        check(context.importToken(vector("full_older")) == FisicaEntitlementOutcome.IGNORED_OLDER)
        // A key activated for another PC is rejected and never replaces this PC's key.
        check(context.importToken(vector("other_target")) == FisicaEntitlementOutcome.REJECTED_TARGET)
        status = context.status()
        check(status.isFull && status.key == "key_test_1" && status.warnings and FisicaEntitlementWarning.IMPORT_REJECTED != 0)
        // No usage record: the state is the signed key and the latest time seen.
        check(String(context.exportState()) == "FSS1\nC ${vector("full")}\nT $now\n")
        clock = now + 8 * DAY; context.checkStatus()
        check(context.status().isFull && warned and FisicaEntitlementWarning.FULL_ENDING != 0)
        clock = now + 14 * DAY; context.checkStatus()
        status = context.status()
        check(!status.isFull && status.reason == FisicaModeReason.FULL_ENDED && status.features == 0 && notices >= 2)
        clock = now + 2 * DAY
        check(context.status().reason == FisicaModeReason.CLOCK_ROLLBACK)  // a clock set back more than an hour
        context.close()
    }
    // Release on the device: the FRC1 code for the portal, no key and no stored state afterwards.
    run {
        val directory = File(temporary, "release")
        val context = license("full", directory)
        val stored = directory.listFiles()!!.single { it.name.endsWith(".state") }
        check(readCode(context.release(), "FRC1") == seat)
        val status = context.status()
        check(status.reason == FisicaModeReason.NO_KEY && status.key == null && !stored.exists())
        check(runCatching { context.release() }.isFailure)
        context.close()
    }
    // Offline activation: the stored activated key wins over the original key file at the next start.
    run {
        val directory = File(temporary, "offline")
        val original = license("unbound_older", directory)
        check(original.status().reason == FisicaModeReason.NOT_ACTIVATED)
        val file = File(temporary, "activated.fse").apply { writeText(vector("full") + "\n") }  // from the portal
        check(original.importFile(file) == FisicaEntitlementOutcome.ACCEPTED && original.status().isFull)
        check(original.syncNow() == null)  // no endpoint: nothing leaves the device
        val reopened = license(null, directory)  // a later start loads the activated key by itself
        check(reopened.status().isFull && reopened.status().bind == target)
        check(reopened.importToken(vector("unbound_older")) == FisicaEntitlementOutcome.IGNORED_OLDER && reopened.status().isFull)
        original.close(); reopened.close()
    }
    // A renewal that reports the key released or revoked: the device lets go of it.
    run {
        val requests = mutableListOf<String>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            val body = exchange.requestBody.readBytes().toString(Charsets.UTF_8)
            synchronized(requests) { requests.add("${exchange.requestURI.path} $body ${exchange.requestHeaders.getFirst("Authorization")}") }
            val response = "{\"token\":\"${vector("revoked")}\"}".toByteArray()
            exchange.sendResponseHeaders(200, response.size.toLong()); exchange.responseBody.use { it.write(response) }
        }
        server.start()
        try {
            val directory = File(temporary, "revoked")
            val context = FisicaEntitlement(core, "http://127.0.0.1:${server.address.port}/", directory) { now + DAY }
            check(context.importToken(vector("full")) == FisicaEntitlementOutcome.ACCEPTED && context.status().isFull)
            val stored = directory.listFiles()!!.single()
            check(context.syncNow(force = true) == null) { "refresh failed: ${context.lastSyncError}" }
            val status = context.status()
            check(!status.isFull && status.reason == FisicaModeReason.REVOKED && !stored.exists())
            check(synchronized(requests) { requests.toList() } == listOf("/sdk/v1/entitlement/refresh {} Bearer ${vector("full")}"))
            context.close()
        } finally { server.stop(0) }
    }
    // BLE write splitting follows the MTU actually negotiated (no Android device needed for the rule).
    run {
        val queue = BleWriteQueue()
        fun sizes(): List<Int> = generateSequence { queue.next() }.map { it.size }.toList()
        queue.enqueue(ByteArray(45)); check(sizes() == listOf(20, 20, 5))               // default MTU 23
        queue.negotiated(247, true); check(queue.mtu == 247)
        queue.enqueue(ByteArray(500)); check(sizes() == listOf(244, 244, 12))
        queue.negotiated(100, false); check(queue.mtu == 247)                          // a failed exchange changes nothing
        queue.negotiated(10, true); check(queue.mtu == 23)                             // never below the ATT minimum
        queue.enqueue(ByteArray(30)); queue.reset()
        check(queue.isEmpty && queue.mtu == BleWriteQueue.DEFAULT_MTU)                 // reconnect starts clean
        val order = ByteArray(50) { it.toByte() }; queue.enqueue(order)
        check(generateSequence { queue.next() }.flatMap { it.asSequence() }.toList() == order.toList())
    }
    // Online activation and renewal against the reference mock developer portal.
    values.getProperty("endpoint")?.let { endpoint ->
        val directory = File(values.getProperty("storage")); val clock = values.getProperty("server_now").toLong()
        val context = FisicaEntitlement(core, endpoint, directory) { clock }
        // Importing a key not activated yet activates it for this PC in the background.
        check(context.importToken(vector("unbound")) == FisicaEntitlementOutcome.ACCEPTED)
        check(context.syncNow() == null) { "activation failed: ${context.lastSyncError}" }
        val status = context.status()
        check(status.isFull && status.bindType == targetType && status.bind == target && status.verifiedAt == clock) { "not activated: $status" }
        check(status.keyUntil == clock + 90 * DAY && status.activationCode == null && status.lastSyncError == null)
        check(context.syncNow(force = true) == null && context.status().isFull)  // renewal: an empty request
        // The client measures at full rate with the activated key.
        val transport = scale(core, packet)
        val (client, received) = measure(core, transport, context)
        check(received == 12 && client.report()!!.weight == 70.5)
        await<SessionEvent>("close") { client.close(it) }
        // A later start finds the activated key stored for this PC; the original key file does not replace it.
        val reopened = FisicaEntitlement(core, null, directory) { clock }
        check(reopened.status().isFull && reopened.status().bind == target)
        check(reopened.importToken(vector("unbound")) == FisicaEntitlementOutcome.IGNORED_OLDER && reopened.status().isFull)
        // Pairing without a key: the mock portal approves every code at once with an unbound key of the contract.
        val paired = File(directory, "paired")
        FisicaEntitlement(core, endpoint, paired) { clock }.use { fresh ->
            check(fresh.status().reason == FisicaModeReason.NO_KEY)
            var shown: PairingCode? = null
            check(fresh.pair({ shown = it }) == PairingResult.APPROVED) { "pairing failed: ${fresh.lastSyncError}" }
            check(shown?.userCode?.length == 9 && fresh.status().key == "key_pair_1" && fresh.status().bind == target)
        }
        FisicaEntitlement(core, null, paired) { clock }.use { later -> check(later.status().isFull && later.status().key == "key_pair_1") }
        check(runCatching { license().pair({}) }.exceptionOrNull() is IllegalStateException)  // no endpoint
        // A communication failure is recorded, raises nothing and changes no license.
        val offline = FisicaEntitlement(core, "http://127.0.0.1:9", File(directory, "offline")) { clock }
        check(offline.importToken(vector("unbound")) == FisicaEntitlementOutcome.ACCEPTED); offline.checkStatus()
        check(offline.syncNow()!!.startsWith("network:"))
        check(offline.status().reason == FisicaModeReason.NOT_ACTIVATED && offline.status().lastSyncError != null)
        context.close(); reopened.close(); offline.close()
    }
    temporary.deleteRecursively()
    println("Kotlin client, transport and license contract passed")
}
