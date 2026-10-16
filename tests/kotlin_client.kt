// JVM contract for the Kotlin high-level client and entitlement binding (no Android, no hardware).
import com.carenco.fisica.*
import java.io.File
import java.util.Properties
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

fun hex(text: String) = ByteArray(text.length / 2) { text.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
fun <T> await(name: String, body: ((T?, Throwable?) -> Unit) -> Unit): Pair<T?, Throwable?> {
    val latch = CountDownLatch(1); var result: Pair<T?, Throwable?> = null to null
    body { value, error -> result = value to error; latch.countDown() }
    check(latch.await(5, TimeUnit.SECONDS)) { "$name did not complete" }
    return result
}
fun scale(core: FisicaCore, packet: ByteArray, frames: Int = 12): MockTransport {
    val transport = MockTransport()
    transport.reply(core.command("GET_FIRMWARE_VERSION"), hex("24434701322e302e31fff0"))
    for (name in listOf("SET_DEVICE_USB", "SET_DEVICE_BLE", "START_CONTINUOUS_MEASUREMENT", "STOP_CONTINUOUS_MEASUREMENT", "SET_DEVICE_STOP")) {
        val command = core.command(name)
        var ack = byteArrayOf(0x24, command[1], command[2], 1, 0, 0, 0xff.toByte(), 0xf0.toByte())
        if (name == "START_CONTINUOUS_MEASUREMENT") repeat(frames) { ack += packet }
        transport.reply(command, ack)
    }
    return transport
}

fun main(args: Array<String>) {
    val values = Properties().apply { File(args[1]).inputStream().use { load(it) } }
    val core = FisicaCore(args[0]) { System.err.println(it) }
    val packet = hex(values.getProperty("packet"))

    // Connect -> measure -> stop -> report -> close.
    run {
        val transport = scale(core, packet); val client = FisicaClient(core, transport)
        var received = 0; client.onFrame = { received++ }
        check(await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }.second == null)
        check(client.firmwareVersion == "2.0.1")
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        check(client.state == FisicaSessionState.MEASURING)
        check(await<SessionEvent>("stop") { client.stop(it) }.second == null)
        check(received == 12 && client.frames.size == 12)
        val report = client.report()!!
        check(report.weight == 70.5 && report.frameCount == 12)
        check(report.frames[0].raw.size == 638 && report.frames[0].display.size == 841 && report.frames[0].packet.size == 645)
        check(await<SessionEvent>("close") { client.close(it) }.second == null)
        check(!transport.isOpen && transport.sent.last().contentEquals(core.command("SET_DEVICE_STOP")))
        check(transport.sent.any { it.contentEquals(core.command("SET_DEVICE_USB")) })
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
    // Start clears the previous measurement; frames stay bounded.
    run {
        val transport = scale(core, packet, frames = 8); val client = FisicaClient(core, transport)
        check(runCatching { client.maxFrames = FisicaLimit.ANALYSIS_FRAMES + 1 }.isFailure)
        client.maxFrames = 5
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        await<SessionEvent>("stop") { client.stop(it) }
        check(client.frames.size == 5 && client.droppedFrames == 3 && client.report()!!.weight == 70.5)
        val start = core.command("START_CONTINUOUS_MEASUREMENT")
        transport.reply(start, byteArrayOf(0x24, start[1], start[2], 1, 0, 0, 0xff.toByte(), 0xf0.toByte()))
        check(await<SessionEvent>("restart") { client.start(completion = it) }.second == null)
        check(client.frames.isEmpty() && client.droppedFrames == 0)
        await<SessionEvent>("close") { client.close(it) }
    }
    // Entitlement is status only and never blocks measurement.
    run {
        var clock = values.getProperty("now").toLong(); val now = clock
        val entitlement = FisicaEntitlement(core) { clock }
        var seen = 0; entitlement.onWarning = { bits, _ -> seen = seen or bits }
        check(entitlement.status().contract == FisicaContract.UNVERIFIED)
        check(entitlement.importToken(values.getProperty("tampered")) == FisicaEntitlementOutcome.REJECTED_SIGNATURE)
        check(entitlement.status().company == "")
        check(entitlement.importToken(values.getProperty("valid")) == FisicaEntitlementOutcome.ACCEPTED)
        var status = entitlement.status()
        check(status.baseLicense == FisicaBaseLicense.GRANTED && status.verification == FisicaVerification.FRESH && status.contract == FisicaContract.ACTIVE)
        check(status.company == "acme" && status.contractReference == "ctr-1" && status.keyEnvironment == FisicaKeyEnvironment.TEST)
        check(entitlement.importToken(values.getProperty("older")) == FisicaEntitlementOutcome.IGNORED_OLDER)
        clock = now + 31 * 86400
        status = entitlement.status()
        check(status.baseLicense == FisicaBaseLicense.GRANTED && status.verification == FisicaVerification.REFRESH_DUE && status.contract == FisicaContract.ACTIVE)
        check(entitlement.importToken(values.getProperty("revoked")) == FisicaEntitlementOutcome.ACCEPTED)
        check(entitlement.status().contract == FisicaContract.REVOKED)
        val transport = scale(core, packet); val client = FisicaClient(core, transport, entitlement)
        await<SessionEvent>("connect") { client.connect(FisicaLink.USB, completion = it) }
        check(await<SessionEvent>("start") { client.start(completion = it) }.second == null)
        await<SessionEvent>("stop") { client.stop(it) }
        check(client.report()!!.weight == 70.5)
        await<SessionEvent>("close") { client.close(it) }
        // Using the SDK leaves no trace: the stored state is still only the signed token.
        check(String(entitlement.exportState()) == "FSS1\nC " + values.getProperty("revoked") + "\n")
        val restored = FisicaEntitlement(core) { clock }
        restored.restoreState(entitlement.exportState()); check(restored.status().company == "acme")
        val expected = FisicaEntitlementWarning.IMPORT_REJECTED or FisicaEntitlementWarning.TEST_KEY or FisicaEntitlementWarning.CONTRACT_REVOKED
        check(seen and expected == expected)
        entitlement.close(); restored.close()
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
    // Online confirmation and token storage against the reference mock server.
    values.getProperty("endpoint")?.let { endpoint ->
        val directory = File(values.getProperty("storage")); var clock = values.getProperty("server_now").toLong()
        val entitlement = FisicaEntitlement(core, endpoint, directory) { clock }
        check(entitlement.importToken(values.getProperty("valid")) == FisicaEntitlementOutcome.ACCEPTED)
        check(entitlement.syncNow() == null) { "sync failed: ${entitlement.lastSyncError}" }
        check(entitlement.status().verifiedAt == clock) { "refresh did not adopt the server-signed time" }
        entitlement.checkStatus()
        check(entitlement.syncNow() == null)
        // A later start with the app's older token still finds the newer stored confirmation.
        val reopened = FisicaEntitlement(core, null, directory) { clock }
        reopened.importToken(values.getProperty("valid"))
        check(reopened.status().verifiedAt == clock && reopened.syncNow() == null)
        // A communication failure is recorded, raises nothing and changes no state.
        val offline = FisicaEntitlement(core, "http://127.0.0.1:9", File(directory, "offline")) { clock }
        offline.importToken(values.getProperty("valid")); offline.checkStatus()
        check(offline.syncNow()!!.startsWith("network:"))
        check(offline.status().contract == FisicaContract.ACTIVE && offline.status().verification == FisicaVerification.FRESH)
        entitlement.close(); offline.close(); reopened.close()
    }
    println("Kotlin client, transport and entitlement contract passed")
}
