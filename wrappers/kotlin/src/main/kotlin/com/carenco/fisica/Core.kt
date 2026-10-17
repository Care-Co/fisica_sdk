package com.carenco.fisica

import java.lang.ref.PhantomReference
import java.lang.ref.ReferenceQueue
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap

/** A core error; [code] is a [FisicaStatus] value. */
open class FisicaException(val code: Int, message: String): RuntimeException(message)
/**
 * The feature needs full mode: an SDK key activated for this app or PC whose features include
 * it ([FisicaStatus.FEATURE_NOT_LICENSED]). A license decision, not a measurement or
 * communication error. See [FisicaEntitlement].
 */
class FisicaLicenseException(message: String): FisicaException(FisicaStatus.FEATURE_NOT_LICENSED, message)
internal object Bridge {
    external fun version(): String
    external fun abi(): Int
    external fun policy(): String
    external fun checkWrapper(name: String, sdk: String, version: String): Int
    external fun create(): Long
    external fun destroy(handle: Long)
    external fun action(handle: Long, action: Int, value: Long, now: Long)
    external fun tick(handle: Long, now: Long)
    external fun feed(handle: Long, bytes: ByteArray, now: Long)
    external fun command(handle: Long, name: String, value: Double, payload: ByteArray, now: Long): ByteArray
    external fun poll(handle: Long, metadata: IntArray): ByteArray?
    external fun sessionLicense(handle: Long, license: Long, nowS: Long, nowMs: Long)
    external fun sessionAnalyze(handle: Long): DoubleArray?
    external fun decode(license: Long, nowS: Long, bytes: ByteArray): DoubleArray
    external fun analyze(matrices: DoubleArray, weights: DoubleArray, rows: Int, cols: Int): DoubleArray?
    external fun analyzePackets(packets: ByteArray, count: Int): DoubleArray?
    external fun responsePayload(packet: ByteArray): ByteArray
    external fun entitlementCreate(): Long
    external fun entitlementDestroy(handle: Long)
    external fun entitlementImport(handle: Long, token: ByteArray, now: Long): Int
    external fun entitlementStatus(handle: Long, now: Long): LongArray
    external fun entitlementText(handle: Long, field: Int): ByteArray?
    external fun entitlementNextExchange(handle: Long, now: Long): Int
    external fun entitlementRelease(handle: Long): ByteArray
    external fun entitlementExport(handle: Long): ByteArray
    external fun entitlementRestore(handle: Long, state: ByteArray)
}
/**
 * One measurement frame: [weight] in kg and [display], the processed 29x29 grid used for
 * analysis (relative units, not kPa). [raw] (the 29x22 sensor grid) and [packet] (the 645
 * bytes as received) are raw data: present only in full mode with
 * [FisicaFeature.RAW_DATA], null in basic mode.
 */
class Measurement(val weight: Double, val display: DoubleArray, val raw: DoubleArray?, val packet: ByteArray?) {
    val hasRawData get() = raw != null && packet != null
    /** Measurement packets carry no battery field; read it with [FisicaClient.readBattery]. */
    val batteryValid get() = false
    companion object {
        private const val DISPLAY = 841; private const val RAW = 638; private const val PACKET = 645
        /** The frame of a session [FisicaSessionEvent.MEASUREMENT] event. */
        @JvmStatic fun fromEvent(event: SessionEvent): Measurement = fromPayload(event.payload)
        /**
         * MEASUREMENT payload (C ABI 2): little-endian doubles [weight, 841 display cells], then,
         * only when the session's license opens raw data, 638 raw cells and the 645-byte packet.
         */
        @JvmStatic fun fromPayload(payload: ByteArray): Measurement {
            val base = 8 * (1 + DISPLAY)
            if (payload.size < base) throw FisicaException(FisicaStatus.INVALID_ARGUMENT, "Not a measurement payload")
            // Absolute reads only: Buffer.position() differs between JDK 8/Android and JDK 9+.
            val values = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN).asDoubleBuffer()
            val display = DoubleArray(DISPLAY) { values.get(1 + it) }
            if (payload.size < base + 8 * RAW + PACKET) return Measurement(values.get(0), display, null, null)
            val raw = DoubleArray(RAW) { values.get(1 + DISPLAY + it) }
            return Measurement(values.get(0), display, raw, payload.copyOfRange(base + 8 * RAW, base + 8 * RAW + PACKET))
        }
    }
}
data class SessionEvent(val metadata: IntArray, val payload: ByteArray) {
    val kind get() = metadata[0]; val state get() = metadata[1]
    val status get() = metadata[2]; val deviceError get() = metadata[3]; val operation get() = metadata[4]
}
class FisicaCore(libraryPath: String? = null, warn: (String)->Unit = { System.err.println(it) }) {
    init {
        if (libraryPath == null) System.loadLibrary("fisica_jni") else System.load(libraryPath)
        val abi = Bridge.abi()
        if (abi ushr 16 != 2) throw FisicaException(FisicaStatus.INCOMPATIBLE_VERSION,"Require core ABI 2.x")
        val notices = Bridge.checkWrapper("kotlin",FisicaRelease.SDK_VERSION,FisicaRelease.WRAPPER_VERSION)
        if (notices and 1 != 0) warn("SDK ${FisicaRelease.SDK_VERSION} is deprecated")
        if (notices and 2 != 0) warn("Kotlin wrapper ${FisicaRelease.WRAPPER_VERSION} is deprecated")
    }
    val version get() = Bridge.version()
    val policyJSON get() = Bridge.policy()
    fun command(name: String,value: Double=0.0,payload: ByteArray=byteArrayOf()): ByteArray {
        require('\u0000' !in name); return Bridge.command(0,name,value,payload,0)
    }
    /**
     * Decode one 645-byte measurement packet into its raw and processed grids. That is raw data:
     * it needs [license] in full mode with [FisicaFeature.RAW_DATA], otherwise (null, basic mode)
     * it throws [FisicaLicenseException]. A [FisicaClient] hands out frames in every mode.
     */
    fun decode(bytes: ByteArray, license: FisicaEntitlement?): Measurement {
        val values = license?.locked { handle, now -> Bridge.decode(handle, now, bytes) }
            ?: Bridge.decode(0, System.currentTimeMillis() / 1000, bytes)
        return Measurement(values[1479], values.copyOfRange(638,1479), values.copyOfRange(0,638), bytes.copyOf())
    }
    /** Payload bytes of a fixed or variable-length command response. */
    fun responsePayload(packet: ByteArray): ByteArray = Bridge.responsePayload(packet)
    /**
     * Weight and foot dimensions from measurement packets as received: [weight_kg, left_width_cm,
     * left_length_cm, right_width_cm, right_length_cm], or null without a stable measurement.
     * The analysis itself runs in the shared core and works in every license mode.
     */
    fun analyzePackets(packets: List<ByteArray>): DoubleArray? {
        require(packets.isNotEmpty() && packets.all { it.size==645 }) { "Expected 645-byte measurement packets" }
        val joined=ByteArray(packets.size*645); packets.forEachIndexed { i,p -> p.copyInto(joined,i*645) }
        return Bridge.analyzePackets(joined,packets.size)
    }
    /** The same analysis from count row-major [rows]x[cols] matrices and their weights (for example frames' [Measurement.display]). */
    fun analyze(matrices: DoubleArray,weights: DoubleArray,rows: Int,cols: Int): DoubleArray? {
        require(rows>0 && cols>0 && weights.size.toLong()*rows*cols==matrices.size.toLong())
        return Bridge.analyze(matrices,weights,rows,cols)
    }
}
/**
 * Frees the native memory of objects the app dropped without close(): a PhantomReference
 * queue drained by one daemon thread (Android API 23 has no java.lang.ref.Cleaner). An
 * action must not reference its owner, or the owner is never collected.
 */
internal object NativeCleaner {
    private val queue = ReferenceQueue<Any>()
    // Keeps each registration reachable until it has run.
    private val registered: MutableSet<Cleanable> = Collections.newSetFromMap(ConcurrentHashMap())
    /** Registrations whose owner is neither closed nor collected yet. */
    val pending get() = registered.size
    class Cleanable internal constructor(owner: Any, action: () -> Unit) : PhantomReference<Any>(owner, queue) {
        private var action: (() -> Unit)? = action
        /** Run the action now, once: on close() or after the owner was collected, whichever comes first. */
        fun clean() {
            val run = synchronized(this) { action.also { action = null } } ?: return
            registered.remove(this); clear(); run()
        }
    }
    fun register(owner: Any, action: () -> Unit): Cleanable = Cleanable(owner, action).also { registered.add(it) }
    init {
        Thread({
            while (true) {
                try { (queue.remove() as Cleanable).clean() } catch (_: InterruptedException) { } catch (_: Exception) { }
            }
        }, "fisica-cleaner").apply { isDaemon = true; start() }
    }
}
/**
 * Synchronized owning handle; no protocol rules, clocks or OS IO here. Close it when done;
 * a session dropped without close() is freed once it is collected.
 */
class FisicaSession(val core: FisicaCore): AutoCloseable {
    private var handle=Bridge.create()
    // Captures only the native handle (up to ~13 MB of packets), never this object.
    private val cleanable=handle.let { native -> NativeCleaner.register(this) { Bridge.destroy(native) } }
    private fun valid(now: Long) { check(handle!=0L) { "Closed session" }; require(now>=0) }
    @Synchronized override fun close() { if (handle!=0L) { handle=0; cleanable.clean() } }
    @Synchronized fun action(action: Int,value: Long=0,nowMs: Long) { valid(nowMs); Bridge.action(handle,action,value,nowMs) }
    @Synchronized fun tick(nowMs: Long) { valid(nowMs); Bridge.tick(handle,nowMs) }
    @Synchronized fun feed(bytes: ByteArray,nowMs: Long) { valid(nowMs); Bridge.feed(handle,bytes,nowMs) }
    @Synchronized fun command(name: String,value: Double=0.0,payload: ByteArray=byteArrayOf(),nowMs: Long) {
        valid(nowMs); require('\u0000' !in name); Bridge.command(handle,name,value,payload,nowMs)
    }
    /** Next event, or null. A MEASUREMENT event's frame: [Measurement.fromEvent]. */
    @Synchronized fun poll(): SessionEvent? { valid(0); val meta=IntArray(8); return Bridge.poll(handle,meta)?.let { SessionEvent(meta,it) } }
    @Synchronized fun drain(): List<SessionEvent> { val events=mutableListOf<SessionEvent>(); while (true) events.add(poll()?:return events) }
    /**
     * Copy [license]'s full/basic decision into this session (null, or a closed license: basic
     * mode). The session starts in basic mode: call this before START, after importing,
     * activating, renewing or releasing a key, and periodically. In basic mode the session hands
     * out at most one MEASUREMENT per [FisicaLimit.BASIC_FRAME_INTERVAL_MS] without raw data.
     * Returns the [FisicaFeature] bits now open (0 in basic mode).
     */
    @Synchronized fun license(license: FisicaEntitlement?, nowMs: Long): Int {
        valid(nowMs)
        return license?.locked { entitlement, nowS ->
            val status = if (entitlement != 0L) Bridge.entitlementStatus(entitlement, nowS) else null
            Bridge.sessionLicense(handle, entitlement, nowS, nowMs)
            if (status != null && status[0] == FisicaMode.FULL.toLong()) status[2].toInt() else 0
        } ?: run { Bridge.sessionLicense(handle, 0, System.currentTimeMillis() / 1000, nowMs); 0 }
    }
    /**
     * Weight and foot dimensions (as [FisicaCore.analyzePackets]) from every measurement packet
     * since the last START, in any mode; null without a stable measurement.
     */
    @Synchronized fun analyze(): DoubleArray? { valid(0); return Bridge.sessionAnalyze(handle) }
}
