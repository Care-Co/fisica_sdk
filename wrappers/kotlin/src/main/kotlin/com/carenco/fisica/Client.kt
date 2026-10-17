package com.carenco.fisica

import java.lang.ref.WeakReference
import java.util.concurrent.Executor
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ScheduledThreadPoolExecutor
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
 * A transport that knows which Scale link it carries ([FisicaLink.BLE] or [FisicaLink.USB],
 * null when it can carry either). [FisicaClient.connect] uses it as the default link and
 * rejects a different one before anything is sent. A transport without a declared link
 * must be given one.
 */
interface FisicaLinkTransport : FisicaTransport {
    val link: Int?
}

/**
 * Scripted link for development and tests without hardware: request bytes -> response chunks.
 * It contains no device behavior; unknown requests are recorded and get no reply.
 */
class MockTransport(val exchanges: MutableMap<String, List<ByteArray>> = mutableMapOf()) : FisicaLinkTransport {
    /** Set to model a transport that carries only one link kind. */
    @Volatile override var link: Int? = null
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

/**
 * Result of the shared analysis of one measurement. Centimetres use the nominal 1 cm cell; not
 * clinically calibrated. [frames] are the frames handed out (in basic mode at most one a second,
 * without raw data); the analysis itself covers every packet the Scale sent, in every mode.
 */
class SessionReport @JvmOverloads constructor(
    val weight: Double, val leftWidth: Double, val leftLength: Double, val rightWidth: Double,
    val rightLength: Double, val frames: List<Measurement>,
    /** The client's last [FisicaClient.readBattery] reading in percent, or null. Packets carry no battery field. */
    val battery: Int? = null,
) {
    val frameCount get() = frames.size
    val batteryValid get() = battery != null
}

/**
 * Connect, measure, stop and report over any transport. Command order, ACK waits, timeouts
 * and recovery decisions live in the shared core; this class only runs the clock, moves
 * bytes and turns core events into callbacks.
 *
 * [entitlement] is the license applied to the measurement session (null: basic mode). The
 * core limits what the session hands out: in basic mode at most one frame a second without
 * raw data, in full mode every frame (with raw data when the key opens it). The license is
 * applied before every start, whenever it changes (import, activation, renewal, release)
 * and every 30 seconds.
 *
 * Work is serialized on an internal thread. Callbacks and completions run on [callbacks]
 * (for example the Android main executor); they receive either an event or an error. A
 * callback or completion that throws is reported through [onError]. Every call completes:
 * after [close], with a [FisicaStatus.CANCELLED] error ([close] itself completes normally).
 * A client dropped without [close] does not keep its thread or native session alive.
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
    /** Transport, core and license errors, and exceptions thrown by callbacks and completions. */
    @Volatile var onError: (Throwable) -> Unit = {}
    @Volatile var firmwareVersion: String? = null; private set
    /** Battery level in percent from the last [readBattery], or null. Measurement packets carry no battery field. */
    @Volatile var battery: Int? = null; private set
    @Volatile var state = FisicaSessionState.DISCONNECTED; private set
    /** [FisicaFeature] bits the session opened when the license was last applied; 0 in basic mode. */
    @Volatile var licenseFeatures = 0; private set

    private val worker = ScheduledThreadPoolExecutor(1) { Thread(it, "fisica-client").apply { isDaemon = true } }
        .apply { removeOnCancelPolicy = true }
    private val session = FisicaSession(core)
    private val started = System.nanoTime()
    private val waiters = HashMap<Int, (SessionEvent?, Throwable?) -> Unit>()
    private val collected = ArrayDeque<Measurement>()
    private var dropped = 0
    private var limit = FisicaLimit.ANALYSIS_FRAMES
    private var ticker: ScheduledFuture<*>? = null
    private var readyDeadline: ScheduledFuture<*>? = null
    private var linkReady = false
    private var closed = false
    private var licensed = false
    private var licenseAt = 0L
    private var licenseGeneration = 0
    // The core's analysis of the last measurement, kept when the session is released.
    @Volatile private var closedAnalysis: DoubleArray? = null

    private val now get() = (System.nanoTime() - started) / 1_000_000
    private fun closedError() = FisicaException(FisicaStatus.CANCELLED, "Client closed")
    private fun busy(message: String) = FisicaException(FisicaStatus.BUSY, message)
    /** Run app code on [callbacks]; if it throws, [onError] hears about it. */
    private fun post(block: () -> Unit) = dispatch { try { block() } catch (error: Throwable) { quietly(error) } }
    private fun report(error: Throwable) = dispatch { quietly(error) }
    // An onError that throws has nowhere left to report to.
    private fun quietly(error: Throwable) { try { onError(error) } catch (_: Throwable) {} }
    private fun dispatch(block: () -> Unit) { try { callbacks.execute(block) } catch (_: Exception) { /* The app's executor is gone. */ } }
    /** Run [block] on the worker; once the client is closed (worker shut down) [rejected] runs instead, on [callbacks]. */
    private fun serial(rejected: () -> Unit = {}, block: () -> Unit) {
        try { worker.execute { try { block() } catch (error: Throwable) { report(error) } } }
        catch (_: RejectedExecutionException) { post(rejected) }
    }
    private fun finish(operation: Int, event: SessionEvent?, error: Throwable?) {
        val waiter = waiters.remove(operation) ?: return
        // Release everything before the caller hears that close finished, confirmed or not.
        if (operation == FisicaSessionOperation.CLOSE) teardown()
        post { waiter(event, error) }
    }

    /** Frames of the current measurement. [start] clears them; a reconnect keeps them. Only the latest [maxFrames] are kept. */
    val frames: List<Measurement> get() = synchronized(collected) { ArrayList(collected) }
    /** Oldest frames dropped beyond [maxFrames] since the last [start] or [clearFrames]. */
    val droppedFrames: Int get() = synchronized(collected) { dropped }
    /** How many recent frames to keep, 1..[FisicaLimit.ANALYSIS_FRAMES] (the most one analysis accepts). Also bounds memory. */
    var maxFrames: Int
        get() = synchronized(collected) { limit }
        set(value) {
            require(value in 1..FisicaLimit.ANALYSIS_FRAMES) { "maxFrames must be 1..${FisicaLimit.ANALYSIS_FRAMES}" }
            synchronized(collected) { limit = value; trim() }
        }
    fun clearFrames() = synchronized(collected) { collected.clear(); dropped = 0 }
    private fun trim() { while (collected.size > limit) { collected.removeFirst(); dropped++ } }

    /**
     * Open the transport and probe firmware. Completes with the probe result; an unanswered
     * probe is reported in its status and does not prevent use.
     *
     * [link] defaults to the transport's own link ([FisicaLinkTransport]); a transport that
     * does not declare one must be given it. A missing link, or one other than the
     * transport's, fails with [FisicaStatus.INVALID_ARGUMENT] before anything is sent.
     */
    @JvmOverloads
    fun connect(link: Int? = null, timeoutMs: Long = 15000, completion: (SessionEvent?, Throwable?) -> Unit) =
        serial({ completion(null, closedError()) }) {
            if (closed) { post { completion(null, closedError()) }; return@serial }
            if (linkReady || waiters.containsKey(FisicaSessionOperation.PROBE)) {
                post { completion(null, busy("Already connected or connecting")) }; return@serial
            }
            val carried = (transport as? FisicaLinkTransport)?.link
            val selected = link ?: carried
            val problem = when {
                selected == null -> "This transport does not declare its link: pass FisicaLink.BLE or FisicaLink.USB"
                selected != FisicaLink.BLE && selected != FisicaLink.USB -> "Use FisicaLink.BLE or FisicaLink.USB"
                carried != null && carried != selected -> "This transport carries ${name(carried)}, not ${name(selected)}"
                else -> null
            }
            if (problem != null || selected == null) {
                post { completion(null, FisicaException(FisicaStatus.INVALID_ARGUMENT, problem ?: "")) }; return@serial
            }
            waiters[FisicaSessionOperation.PROBE] = completion
            transport.onReady = { serial { ready(selected) } }
            transport.onBytes = { bytes -> serial { received(bytes) } }
            transport.onLost = { serial { lost(false) } }
            transport.onSendFailure = { error -> serial { report(error); lost(true) } }
            transport.onError = { error -> report(error) }
            val deadline = worker.schedule({
                if (!linkReady && !closed) {
                    closeTransport()
                    finish(FisicaSessionOperation.PROBE, null, FisicaException(1001, "Transport was not ready in time"))
                }
            }, timeoutMs, TimeUnit.MILLISECONDS)
            readyDeadline = deadline
            entitlement?.checkStatus()
            try { transport.open() }
            catch (error: Exception) { deadline.cancel(false); finish(FisicaSessionOperation.PROBE, null, error) }
        }

    private fun name(link: Int) = if (link == FisicaLink.USB) "USB" else "BLE"

    private fun ready(link: Int) {
        if (closed || linkReady) return
        linkReady = true; readyDeadline?.cancel(false)
        if (ticker == null) ticker = tick(WeakReference(this), worker)
        step { session.action(FisicaSessionAction.CONNECTED, link.toLong(), now) }
    }
    private fun pulse() = step { session.tick(now); refreshLicense() }
    private fun received(bytes: ByteArray) {
        if (!linkReady) return
        var offset = 0
        while (offset < bytes.size) { val end = minOf(offset + FisicaLimit.FEED_BYTES, bytes.size); val part = bytes.copyOfRange(offset, end); step { session.feed(part, now) }; offset = end }
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

    /** Copy the license into the session; a license that cannot be applied is basic mode (and reported). */
    private fun applyLicense() {
        licenseFeatures = try { session.license(entitlement, now) } catch (error: Exception) { report(error); session.license(null, now); 0 }
        licensed = true; licenseAt = now; licenseGeneration = entitlement?.generation ?: 0
    }
    /** Picks up imports, activation, renewal, release and the end of full mode. */
    private fun refreshLicense() {
        if (!licensed || now - licenseAt >= 30_000 || (entitlement?.generation ?: 0) != licenseGeneration) applyLicense()
    }

    /** Run one core call, then deliver everything it produced. */
    private fun step(call: () -> Unit) {
        if (closed) return
        try { call() } catch (error: Throwable) { report(error) }
        deliverQueued()
    }
    // Every queued event is delivered even when an earlier one failed, so results are never lost.
    private fun deliverQueued() {
        if (closed) return
        val events = try { session.drain() } catch (error: Throwable) { report(error); return }
        for (event in events) try { deliver(event) } catch (error: Throwable) { report(error) }
    }
    private fun deliver(event: SessionEvent) {
        state = event.state
        when (event.kind) {
            // A transport that throws instead of calling onSendFailure is handled the same way, after the events at hand.
            FisicaSessionEvent.TRANSMIT -> try { transport.send(event.payload) } catch (error: Exception) { serial { report(error); lost(true) } }
            FisicaSessionEvent.MEASUREMENT -> {
                val frame = Measurement.fromEvent(event)
                synchronized(collected) { collected.addLast(frame); trim() }
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

    /** Wait for [waitFor] after [call]; Busy while that operation is pending, CANCELLED once closed. */
    private fun request(waitFor: Int, completion: (SessionEvent?, Throwable?) -> Unit, call: () -> Unit) =
        serial({ completion(null, closedError()) }) {
            if (closed) { post { completion(null, closedError()) }; return@serial }
            if (waiters.containsKey(waitFor)) { post { completion(null, busy("Another operation is pending")) }; return@serial }
            waiters[waitFor] = completion
            try { call() } catch (error: Throwable) { finish(waitFor, null, error) }
            deliverQueued()
        }
    /**
     * Start measuring; null [durationMs] measures until [stop]. Completes when the Scale confirmed the start.
     * The current license is applied first. Frames of the previous measurement are cleared once the core
     * accepts the start.
     */
    @JvmOverloads
    fun start(durationMs: Long? = null, completion: (SessionEvent?, Throwable?) -> Unit) {
        entitlement?.checkStatus()
        request(FisicaSessionOperation.BEGIN, completion) {
            applyLicense(); session.action(FisicaSessionAction.START, durationMs ?: -1, now); clearFrames()
        }
    }
    fun stop(completion: (SessionEvent?, Throwable?) -> Unit) =
        request(FisicaSessionOperation.END, completion) { applyLicense(); session.action(FisicaSessionAction.STOP, 0, now) }

    /**
     * Ask the Scale for its battery level (GET_BATTERY_LEVEL), idle or during a measurement, without
     * interrupting it. A refused or unanswered query fails only this call; the session carries on.
     */
    fun readBattery(completion: (Int?, Throwable?) -> Unit) =
        request(FisicaSessionOperation.REQUEST, { event, error ->
            if (event == null) completion(null, error)
            else {
                val data = core.responsePayload(event.payload)
                val percent = if (data.size == 2) ((data[0].toInt() and 0xff) shl 8) or (data[1].toInt() and 0xff) else -1
                if (percent in 0..100) { battery = percent; completion(percent, null) }
                else completion(null, FisicaCommandException(event))
            }
        }) { session.command("GET_BATTERY_LEVEL", nowMs = now) }

    /**
     * Stop if needed, release the Scale's measurement mode, then close the transport.
     * Resources are released even when the Scale does not confirm. A second close while one
     * is pending fails with [FisicaStatus.BUSY]; once closed, close completes at once.
     */
    @JvmOverloads
    fun close(completion: (SessionEvent?, Throwable?) -> Unit = { _, _ -> }) = serial({ completion(null, null) }) {
        if (closed) { post { completion(null, null) }; return@serial }
        if (waiters.containsKey(FisicaSessionOperation.CLOSE)) { post { completion(null, busy("Close is already pending")) }; return@serial }
        if (!linkReady) { teardown(); post { completion(null, null) }; return@serial }
        waiters[FisicaSessionOperation.CLOSE] = completion
        try { session.action(FisicaSessionAction.DISCONNECT, 0, now) } catch (error: Throwable) { finish(FisicaSessionOperation.CLOSE, null, error) }
        deliverQueued()
    }
    private fun closeTransport() { try { transport.close() } catch (error: Exception) { report(error) } }
    private fun teardown() {
        if (closed) return
        closed = true; linkReady = false
        ticker?.cancel(false); readyDeadline?.cancel(false)
        transport.onReady = {}; transport.onBytes = {}; transport.onLost = {}; transport.onSendFailure = {}; transport.onError = {}
        // Keep the core's analysis of every packet for report() once the session is gone.
        closedAnalysis = try { session.analyze() } catch (_: Exception) { null }
        closeTransport(); session.close()
        val pending = ArrayList(waiters.values); waiters.clear()
        pending.forEach { post { it(null, closedError()) } }
        worker.shutdown()
    }

    /**
     * Weight and foot size of the current measurement; null without a stable measurement. The
     * core analyzes every packet it received since the start, in every mode ([frames] may hold
     * only some of them). After [close] it returns the last measurement's analysis.
     */
    fun report(): SessionReport? {
        entitlement?.checkStatus()
        val result = (try { session.analyze() } catch (_: IllegalStateException) { closedAnalysis }) ?: return null
        return SessionReport(result[0], result[1], result[2], result[3], result[4], frames, battery)
    }

    private companion object {
        /**
         * Tick every 20 ms. The task holds the client weakly: a client dropped without close()
         * is collected (its session's native memory with it), and the task then ends the thread.
         */
        fun tick(client: WeakReference<FisicaClient>, worker: ScheduledExecutorService): ScheduledFuture<*> =
            worker.scheduleWithFixedDelay({ client.get()?.pulse() ?: worker.shutdown() }, 20, 20, TimeUnit.MILLISECONDS)
    }
}
