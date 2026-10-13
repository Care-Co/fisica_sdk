package com.carenco.fisica

import java.util.ArrayDeque

/**
 * Splits outgoing bytes by the ATT MTU actually agreed with the Scale. Kept free of
 * Android types so the rule can be verified without a device.
 */
internal class BleWriteQueue {
    /** 23 until a negotiation succeeds; a write carries at most [mtu] - 3 bytes. */
    var mtu = DEFAULT_MTU; private set
    private val pending = ArrayDeque<ByteArray>()
    val isEmpty get() = pending.isEmpty()
    /** Outcome of an MTU exchange. A refused or failed request leaves the current value in use. */
    fun negotiated(size: Int, success: Boolean) { if (success) mtu = size.coerceAtLeast(DEFAULT_MTU) }
    fun enqueue(bytes: ByteArray) {
        var offset = 0
        while (offset < bytes.size) { val end = minOf(offset + mtu - 3, bytes.size); pending.add(bytes.copyOfRange(offset, end)); offset = end }
    }
    fun next(): ByteArray? = pending.pollFirst()
    fun clear() = pending.clear()
    /** A new connection starts from the default MTU with nothing queued. */
    fun reset() { pending.clear(); mtu = DEFAULT_MTU }
    companion object {
        const val DEFAULT_MTU = 23
        /** Largest ATT MTU Android will negotiate; the Scale answers with what it supports. */
        const val REQUESTED_MTU = 517
    }
}
