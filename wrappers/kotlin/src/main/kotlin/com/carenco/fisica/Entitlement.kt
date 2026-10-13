package com.carenco.fisica

/** Three independent facts. Nothing here enables or disables SDK features. Times are Unix seconds, 0 when not set. */
class EntitlementStatus(
    /** Granted with genuine hardware; it has no date expiry. See [FisicaBaseLicense]. */
    val baseLicense: Int,
    /** How recently the server confirmed this information (30 days at most). See [FisicaVerification]. */
    val verification: Int,
    /** State of the verified company's contract, by server-signed dates only. See [FisicaContract]. */
    val contract: Int,
    val contractKind: Int, val company: String, val contractReference: String, val grants: List<String>,
    val verifiedAt: Long, val refreshBy: Long, val contractStart: Long, val contractEnd: Long, val revokedAt: Long,
    val lastImport: Int, val keyEnvironment: Int,
    /** Bit set of [FisicaEntitlementWarning]. */
    val warnings: Int,
)

/**
 * One SDK instance's verified company/contract context. Import a server-signed token or
 * file; status and warnings never block connection, measurement or analysis.
 */
class FisicaEntitlement(
    @Suppress("unused") private val core: FisicaCore,
    private val clock: () -> Long = { System.currentTimeMillis() / 1000 },
) : AutoCloseable {
    private var handle = Bridge.entitlementCreate()
    private var warned = 0
    /** Called with newly raised warning bits so the app can show or log them. */
    @Volatile var onWarning: (Int, EntitlementStatus) -> Unit = { _, _ -> }

    @Synchronized override fun close() { if (handle != 0L) { Bridge.entitlementDestroy(handle); handle = 0 } }
    private fun open(): Long { check(handle != 0L) { "Closed entitlement context" }; return handle }

    /** Returns a [FisicaEntitlementOutcome]. A rejected or older token leaves verified information untouched. */
    @Synchronized fun importToken(token: String): Int {
        val outcome = Bridge.entitlementImport(open(), token.toByteArray(Charsets.UTF_8), clock())
        notifyWarnings(); return outcome
    }
    /** Offline setup: a file downloaded on another PC works on a device that was never online. */
    fun importFile(file: java.io.File): Int = importToken(file.readText(Charsets.UTF_8))

    @Synchronized fun status(): EntitlementStatus {
        val values = Bridge.entitlementStatus(open(), clock())
        val text = { field: Int -> String(Bridge.entitlementText(handle, field), Charsets.UTF_8) }
        return EntitlementStatus(FisicaBaseLicense.GRANTED, values[1].toInt(), values[2].toInt(), values[3].toInt(),
            text(FisicaEntitlementText.COMPANY), text(FisicaEntitlementText.CONTRACT),
            text(FisicaEntitlementText.GRANTS).split(',').filter { it.isNotEmpty() },
            values[4], values[5], values[6], values[7], values[8], values[9].toInt(), values[10].toInt(), values[11].toInt())
    }
    private fun notifyWarnings() {
        val status = status(); val fresh = status.warnings and warned.inv(); warned = status.warnings
        if (fresh != 0) onWarning(fresh, status)
    }
    /** Record real feature use (connect, measure, analyze). Does nothing without a verified contract. */
    @Synchronized fun markUsed() { if (handle != 0L) { Bridge.entitlementMarkUsed(handle, clock()); notifyWarnings() } }

    class Report(val token: String, val body: ByteArray)
    /**
     * Next pending usage report: bearer token and JSON body for the host to send.
     * [reportId] is 16 random bytes naming this report for retry de-duplication only.
     */
    @Synchronized fun pendingReport(reportId: ByteArray): Report? {
        val body = arrayOfNulls<ByteArray>(1)
        val token = Bridge.entitlementReport(open(), reportId, body) ?: return null
        return Report(String(token, Charsets.US_ASCII), body[0]!!)
    }
    /** Call only after the server confirmed the report; the local record is then cleared. */
    @Synchronized fun reportDone() = Bridge.entitlementReportDone(open())
    /** Opaque state for the host to store. Restoring verifies every stored token again. */
    @Synchronized fun exportState(): ByteArray = Bridge.entitlementExport(open())
    @Synchronized fun restoreState(state: ByteArray) { Bridge.entitlementRestore(open(), state); notifyWarnings() }
}
