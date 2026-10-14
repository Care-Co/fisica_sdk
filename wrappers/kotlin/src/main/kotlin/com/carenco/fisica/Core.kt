package com.carenco.fisica

class FisicaException(val code: Int, message: String): RuntimeException(message)
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
    external fun decode(bytes: ByteArray): DoubleArray
    external fun analyze(matrices: DoubleArray, weights: DoubleArray, rows: Int, cols: Int): DoubleArray?
    external fun analyzePackets(packets: ByteArray, count: Int): DoubleArray?
    external fun responsePayload(packet: ByteArray): ByteArray
    external fun entitlementCreate(): Long
    external fun entitlementDestroy(handle: Long)
    external fun entitlementImport(handle: Long, token: ByteArray, now: Long): Int
    external fun entitlementStatus(handle: Long, now: Long): LongArray
    external fun entitlementText(handle: Long, field: Int): ByteArray
    external fun entitlementExport(handle: Long): ByteArray
    external fun entitlementRestore(handle: Long, state: ByteArray)
}
/**
 * One decoded measurement. [packet] is the 645 bytes as received, [raw] the 29x22 sensor
 * grid and [display] the processed 29x29 grid used for analysis. Relative units, not kPa.
 */
class Measurement(val packet: ByteArray, val raw: DoubleArray, val display: DoubleArray, val metrics: DoubleArray) {
    val weight get() = metrics[0]
    val batteryValid get() = false
}
data class SessionEvent(val metadata: IntArray, val payload: ByteArray) {
    val kind get() = metadata[0]; val state get() = metadata[1]
    val status get() = metadata[2]; val deviceError get() = metadata[3]; val operation get() = metadata[4]
}
class FisicaCore(libraryPath: String? = null, warn: (String)->Unit = { System.err.println(it) }) {
    init {
        if (libraryPath == null) System.loadLibrary("fisica_jni") else System.load(libraryPath)
        val abi = Bridge.abi()
        if (abi ushr 16 != 1 || (abi and 65535) < 3) throw FisicaException(12,"Require ABI 1.3+")
        val notices = Bridge.checkWrapper("kotlin",FisicaRelease.SDK_VERSION,FisicaRelease.WRAPPER_VERSION)
        if (notices and 1 != 0) warn("SDK ${FisicaRelease.SDK_VERSION} is deprecated")
        if (notices and 2 != 0) warn("Kotlin wrapper ${FisicaRelease.WRAPPER_VERSION} is deprecated")
    }
    val version get() = Bridge.version()
    val policyJSON get() = Bridge.policy()
    fun command(name: String,value: Double=0.0,payload: ByteArray=byteArrayOf()): ByteArray {
        require('\u0000' !in name); return Bridge.command(0,name,value,payload,0)
    }
    fun decode(bytes: ByteArray): Measurement {
        val values=Bridge.decode(bytes)
        return Measurement(bytes.copyOf(),values.copyOfRange(0,638),values.copyOfRange(638,1479),values.copyOfRange(1479,1481))
    }
    /** Payload bytes of a fixed or variable-length command response. */
    fun responsePayload(packet: ByteArray): ByteArray = Bridge.responsePayload(packet)
    /**
     * Weight and foot dimensions from measurement packets as received: [weight_kg, left_width_cm,
     * left_length_cm, right_width_cm, right_length_cm], or null without a stable measurement.
     * The analysis itself runs in the shared core.
     */
    fun analyzePackets(packets: List<ByteArray>): DoubleArray? {
        require(packets.isNotEmpty() && packets.all { it.size==645 }) { "Expected 645-byte measurement packets" }
        val joined=ByteArray(packets.size*645); packets.forEachIndexed { i,p -> p.copyInto(joined,i*645) }
        return Bridge.analyzePackets(joined,packets.size)
    }
    fun analyze(matrices: DoubleArray,weights: DoubleArray,rows: Int,cols: Int): DoubleArray? {
        require(rows>0 && cols>0 && weights.size.toLong()*rows*cols==matrices.size.toLong())
        return Bridge.analyze(matrices,weights,rows,cols)
    }
}
/** Synchronized owning handle; no protocol rules, clocks or OS IO here. */
class FisicaSession(val core: FisicaCore): AutoCloseable {
    private var handle=Bridge.create()
    private fun valid(now: Long) { check(handle!=0L); require(now>=0) }
    @Synchronized override fun close() { if (handle!=0L) { Bridge.destroy(handle); handle=0 } }
    @Synchronized fun action(action: Int,value: Long=0,nowMs: Long) { valid(nowMs); Bridge.action(handle,action,value,nowMs) }
    @Synchronized fun tick(nowMs: Long) { valid(nowMs); Bridge.tick(handle,nowMs) }
    @Synchronized fun feed(bytes: ByteArray,nowMs: Long) { valid(nowMs); Bridge.feed(handle,bytes,nowMs) }
    @Synchronized fun command(name: String,value: Double=0.0,payload: ByteArray=byteArrayOf(),nowMs: Long) {
        valid(nowMs); require('\u0000' !in name); Bridge.command(handle,name,value,payload,nowMs)
    }
    @Synchronized fun poll(): SessionEvent? { valid(0); val meta=IntArray(8); return Bridge.poll(handle,meta)?.let { SessionEvent(meta,it) } }
    @Synchronized fun drain(): List<SessionEvent> { val events=mutableListOf<SessionEvent>(); while (true) events.add(poll()?:return events) }
}
