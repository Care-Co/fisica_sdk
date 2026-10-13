package com.carenco.fisica

import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/** Byte link to one Scale. The app or SDK adapter owns the OS I/O; no protocol rules here. */
interface FisicaTransport {
    /** Reads and writes both work. */
    var onReady: () -> Unit
    var onBytes: (ByteArray) -> Unit
    /** The link ended or could not be established. */
    var onLost: () -> Unit
    /** A write did not reach the Scale; the command outcome is unknown. */
    var onSendFailure: (Throwable) -> Unit
    var onError: (Throwable) -> Unit
    fun open()
    fun send(bytes: ByteArray)
    fun close()
}

/**
 * Scripted link for development and tests without hardware: request bytes -> response chunks.
 * It contains no device behavior; unknown requests are recorded and get no reply.
 */
class MockTransport(val exchanges: MutableMap<String, List<ByteArray>> = mutableMapOf()) : FisicaTransport {
    override var onReady: () -> Unit = {}
    override var onBytes: (ByteArray) -> Unit = {}
    override var onLost: () -> Unit = {}
    override var onSendFailure: (Throwable) -> Unit = {}
    override var onError: (Throwable) -> Unit = {}
    val sent = mutableListOf<ByteArray>()
    @Volatile var isOpen = false; private set
    /** Set false to model a link that never becomes ready. */
    @Volatile var becomesReady = true
    fun reply(request: ByteArray, vararg chunks: ByteArray) { exchanges[key(request)] = chunks.toList() }
    override fun open() { isOpen = true; if (becomesReady) onReady() }
    override fun send(bytes: ByteArray) {
        if (!isOpen) { onSendFailure(FisicaException(1001, "Mock transport is closed")); return }
        synchronized(sent) { sent.add(bytes.copyOf()) }
        exchanges[key(bytes)]?.forEach { onBytes(it) }
    }
    fun feed(bytes: ByteArray) = onBytes(bytes)
    fun drop() { isOpen = false; onLost() }
    override fun close() { isOpen = false }
    private fun key(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }
}

/** A command the Scale rejected or did not confirm: SDK status in [code], the Scale's 16-bit code in [deviceCode]. */
class FisicaCommandException(val event: SessionEvent) :
    RuntimeException("Command rejected or unconfirmed; device code ${event.deviceError}") {
    val code get() = event.status
    val deviceCode get() = event.deviceError
}

/** Result of the shared analysis over the collected frames. Centimetres use the nominal 1 cm cell; not clinically calibrated. */
class SessionReport(val weight: Double, val leftWidth: Double, val leftLength: Double, val rightWidth: Double,
                    val rightLength: Double, val frames: List<Measurement>) {
    val frameCount get() = frames.size
    val batteryValid get() = false
}

/** Link kind passed to the core; it selects the Scale's measurement mode command. */
object FisicaLink { const val BLE = 1L; const val USB = 2L }

/**
 * Connect, measure, stop and report over any transport. Command order, ACK waits, timeouts
 * and recovery decisions live in the shared core; this class only runs the clock, moves
 * bytes and turns core events into callbacks.
 *
 * Work is serialized on an internal thread. Callbacks and completions run on [callbacks]
 * (for example the Android main executor); they receive either an event or an error.
 */
class FisicaClient(
    val core: FisicaCore,
    val transport: FisicaTransport,
    val entitlement: FisicaEntitlement? = null,
    private val callbacks: Executor = Executor { it.run() },
) {
    @Volatile var onFrame: (Measurement) -> Unit = {}
    @Volatile var onEvent: (SessionEvent) -> Unit = {}
    /** The link dropped. Reopen the same Scale with [connect]; the core resumes a measurement it still wants. */
    @Volatile var onLinkLost: () -> Unit = {}
    @Volatile var onError: (Throwable) -> Unit = {}
    @Volatile var firmwareVersion: String? = null; private set
    @Volatile var state = FisicaSessionState.DISCONNECTED; private set

    private val worker = Executors.newSingleThreadScheduledExecutor { Thread(it, "fisica-client").apply { isDaemon = true } }
    private val session = FisicaSession(core)
    private val started = System.nanoTime()
    private val waiters = HashMap<Int, (SessionEvent?, Throwable?) -> Unit>()
    private val collected = ArrayList<Measurement>()
    private var ticker: ScheduledFuture<*>? = null
    private var readyDeadline: ScheduledFuture<*>? = null
    private var linkReady = false
    private var closed = false

    private val now get() = (System.nanoTime() - started) / 1_000_000
    private fun post(block: () -> Unit) { try { callbacks.execute(block) } catch (_: Exception) {} }
    private fun serial(block: () -> Unit) { try { worker.execute { try { block() } catch (error: Throwable) { post { onError(error) } } } } catch (_: Exception) {} }
    private fun finish(operation: Int, event: SessionEvent?, error: Throwable?) {
        val waiter = waiters.remove(operation) ?: return
        // Release everything before the caller hears that close finished, confirmed or not.
        if (operation == FisicaSessionOperation.CLOSE) teardown()
        post { waiter(event, error) }
    }

    /** Frames collected since the last [clearFrames]. */
    val frames: List<Measurement> get() = synchronized(collected) { ArrayList(collected) }
    fun clearFrames() = synchronized(collected) { collected.clear() }

    /**
     * Open the transport and probe firmware. Completes with the probe result; an unanswered
     * probe is reported in its status and does not prevent use.
     */
    @JvmOverloads
    fun connect(link: Long = FisicaLink.BLE, timeoutMs: Long = 15000, completion: (SessionEvent?, Throwable?) -> Unit) = serial {
        if (closed || linkReady || waiters.containsKey(FisicaSessionOperation.PROBE)) {
            post { completion(null, FisicaException(FisicaStatus.BUSY, "Already connected or connecting")) }; return@serial
        }
        waiters[FisicaSessionOperation.PROBE] = completion
        transport.onReady = { serial { ready(link) } }
        transport.onBytes = { bytes -> serial { received(bytes) } }
        transport.onLost = { serial { lost(false) } }
        transport.onSendFailure = { error -> serial { post { onError(error) }; lost(true) } }
        transport.onError = { error -> post { onError(error) } }
        readyDeadline = worker.schedule({
            if (!linkReady && !closed) {
                transport.close()
                finish(FisicaSessionOperation.PROBE, null, FisicaException(1001, "Transport was not ready in time"))
            }
        }, timeoutMs, TimeUnit.MILLISECONDS)
        entitlement?.markUsed()
        transport.open()
    }

    private fun ready(link: Long) {
        if (closed || linkReady) return
        linkReady = true; readyDeadline?.cancel(false)
        if (ticker == null) ticker = worker.scheduleWithFixedDelay({ step { session.tick(now) } }, 20, 20, TimeUnit.MILLISECONDS)
        step { session.action(FisicaSessionAction.CONNECTED, link, now) }
    }
    private fun received(bytes: ByteArray) {
        if (!linkReady) return
        // The core accepts at most 65536 bytes per call.
        var offset = 0
        while (offset < bytes.size) { val end = minOf(offset + 65536, bytes.size); val part = bytes.copyOfRange(offset, end); step { session.feed(part, now) }; offset = end }
    }
    private fun lost(sendFailure: Boolean) {
        if (closed) return
        if (!linkReady) {
            // Never became ready: nothing to recover, report the failed connection.
            readyDeadline?.cancel(false)
            finish(FisicaSessionOperation.PROBE, null, FisicaException(1001, "Transport could not connect")); return
        }
        linkReady = false
        step { session.action(FisicaSessionAction.LOST, if (sendFailure) 1 else 0, now) }
    }

    /** Run one core call, then deliver everything it produced. */
    private fun step(call: () -> Unit) {
        if (closed) return
        try { call(); session.drain().forEach(::deliver) } catch (error: Throwable) { post { onError(error) } }
    }
    private fun deliver(event: SessionEvent) {
        state = event.state
        when (event.kind) {
            FisicaSessionEvent.TRANSMIT -> transport.send(event.payload)
            FisicaSessionEvent.MEASUREMENT -> {
                val frame = core.decode(event.payload)
                synchronized(collected) { collected.add(frame) }
                post { onFrame(frame) }
            }
            FisicaSessionEvent.RESULT -> {
                val probe = event.operation == FisicaSessionOperation.PROBE
                if (probe && event.status == 0) {
                    firmwareVersion = String(core.responsePayload(event.payload), Charsets.UTF_8).trim('\u0000', ' ', '\r', '\n').ifEmpty { null }
                }
                // A failed firmware probe is informational; the core lets use continue.
                if (event.status == 0 || probe) finish(event.operation, event, null)
                else finish(event.operation, null, FisicaCommandException(event))
            }
            FisicaSessionEvent.RECONNECT -> post { onLinkLost() }
        }
        post { onEvent(event) }
    }

    private fun request(action: Int, value: Long, waitFor: Int, completion: (SessionEvent?, Throwable?) -> Unit) = serial {
        if (closed || waiters.containsKey(waitFor)) {
            post { completion(null, FisicaException(FisicaStatus.BUSY, "Another operation is pending")) }; return@serial
        }
        waiters[waitFor] = completion
        try { session.action(action, value, now); session.drain().forEach(::deliver) }
        catch (error: Throwable) { finish(waitFor, null, error) }
    }
    /** Start measuring; null [durationMs] measures until [stop]. Completes when the Scale confirmed the start. */
    @JvmOverloads
    fun start(durationMs: Long? = null, completion: (SessionEvent?, Throwable?) -> Unit) {
        entitlement?.markUsed()
        request(FisicaSessionAction.START, durationMs ?: -1, FisicaSessionOperation.BEGIN, completion)
    }
    fun stop(completion: (SessionEvent?, Throwable?) -> Unit) = request(FisicaSessionAction.STOP, 0, FisicaSessionOperation.END, completion)

    /**
     * Stop if needed, release the Scale's measurement mode, then close the transport.
     * Resources are released even when the Scale does not confirm.
     */
    @JvmOverloads
    fun close(completion: (SessionEvent?, Throwable?) -> Unit = { _, _ -> }) = serial {
        if (closed) { post { completion(null, null) }; return@serial }
        if (!linkReady) { teardown(); post { completion(null, null) }; return@serial }
        waiters[FisicaSessionOperation.CLOSE] = completion
        try { session.action(FisicaSessionAction.DISCONNECT, 0, now); session.drain().forEach(::deliver) }
        catch (error: Throwable) { finish(FisicaSessionOperation.CLOSE, null, error) }
    }
    private fun teardown() {
        if (closed) return
        closed = true; linkReady = false
        ticker?.cancel(false); readyDeadline?.cancel(false)
        transport.onReady = {}; transport.onBytes = {}; transport.onLost = {}; transport.onSendFailure = {}; transport.onError = {}
        transport.close(); session.close()
        val pending = ArrayList(waiters.values); waiters.clear()
        pending.forEach { post { it(null, FisicaException(FisicaStatus.CANCELLED, "Client closed")) } }
        worker.shutdown()
    }

    /** Analyze the frames collected so far; null without a stable measurement. */
    fun report(): SessionReport? {
        val snapshot = frames
        if (snapshot.isEmpty()) return null
        entitlement?.markUsed()
        val result = core.analyzePackets(snapshot.map { it.packet }) ?: return null
        return SessionReport(result[0], result[1], result[2], result[3], result[4], snapshot)
    }
}
