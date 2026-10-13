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
 *
 * [endpoint]: server base URL for online confirmation and the minimal usage report. Without
 * it no network is used; [online] false keeps a configured endpoint unused. The app needs
 * the INTERNET permission for it. [storage]: directory for the state file (for example
 * `context.filesDir`); null keeps state in memory only.
 */
class FisicaEntitlement @JvmOverloads constructor(
    @Suppress("unused") private val core: FisicaCore,
    endpoint: String? = null,
    private val storage: java.io.File? = null,
    private val online: Boolean = true,
    private val clock: () -> Long = { System.currentTimeMillis() / 1000 },
) : AutoCloseable {
    private var handle = Bridge.entitlementCreate()
    private var warned = 0
    private val endpoint = endpoint?.trimEnd('/')
    private var syncing = false
    private val syncLock = Any()  // One exchange at a time, so a report is never confirmed twice.
    private var nextSync = 0L
    /** Last storage or network problem, or null. Never an entitlement state. */
    @Volatile var lastSyncError: String? = null; private set
    /** Called with newly raised warning bits so the app can show or log them. */
    @Volatile var onWarning: (Int, EntitlementStatus) -> Unit = { _, _ -> }

    @Synchronized override fun close() { if (handle != 0L) { Bridge.entitlementDestroy(handle); handle = 0 } }
    private fun open(): Long { check(handle != 0L) { "Closed entitlement context" }; return handle }

    /** Returns a [FisicaEntitlementOutcome]. A rejected or older token leaves verified information untouched. */
    fun importToken(token: String): Int {
        val outcome = synchronized(this) {
            val bytes = token.toByteArray(Charsets.UTF_8)
            val result = Bridge.entitlementImport(open(), bytes, clock())
            if (result == FisicaEntitlementOutcome.ACCEPTED) {
                // Stored usage and a possibly newer confirmation win over an older file.
                try { stateFile()?.takeIf { it.isFile }?.let { Bridge.entitlementRestore(handle, it.readBytes()); Bridge.entitlementImport(handle, bytes, clock()) } }
                catch (error: Exception) { lastSyncError = "storage: ${error.message}" }
                save()
            }
            notifyWarnings(); result
        }
        if (outcome == FisicaEntitlementOutcome.ACCEPTED) sync()
        return outcome
    }
    private fun stateFile(): java.io.File? {
        val directory = storage ?: return null
        val company = String(Bridge.entitlementText(handle, FisicaEntitlementText.COMPANY), Charsets.UTF_8)
        if (company.isEmpty()) return null
        val contract = String(Bridge.entitlementText(handle, FisicaEntitlementText.CONTRACT), Charsets.UTF_8)
        // Named by company and contract, the only attribution unit; never an installation or device id.
        val name = "$company-$contract".replace(Regex("[^A-Za-z0-9_-]"), "_")
        return java.io.File(directory, "entitlement-$name.state")
    }
    private fun save() {
        try {
            val file = stateFile() ?: return
            file.parentFile?.mkdirs()
            val temporary = java.io.File(file.path + ".tmp")
            temporary.writeBytes(Bridge.entitlementExport(handle))
            if (!temporary.renameTo(file)) { file.delete(); temporary.renameTo(file) }
        } catch (error: Exception) { lastSyncError = "storage: ${error.message}" }
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
    fun markUsed() {
        synchronized(this) { if (handle == 0L) return; Bridge.entitlementMarkUsed(handle, clock()); save(); notifyWarnings() }
        sync()
    }

    private fun post(path: String, token: String, body: ByteArray): ByteArray {
        val connection = java.net.URL(endpoint + path).openConnection() as java.net.HttpURLConnection
        try {
            connection.requestMethod = "POST"; connection.connectTimeout = 5000; connection.readTimeout = 5000; connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token"); connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            if (code !in 200..299) throw java.io.IOException("HTTP $code")
            return connection.inputStream.use { it.readBytes() }
        } finally { connection.disconnect() }
    }
    /**
     * Online confirmation, then the pending usage reports, on the calling thread. Returns null
     * or the problem. A failure changes no entitlement state: it is never an expiry or a revocation.
     */
    fun syncNow(): String? = synchronized(syncLock) { syncLocked() }
    private fun syncLocked(): String? {
        if (!online || endpoint == null) return null
        try {
            val token = synchronized(this) { if (handle == 0L) return null; String(Bridge.entitlementText(handle, FisicaEntitlementText.TOKEN), Charsets.US_ASCII) }
            if (token.isEmpty()) return null  // Nothing verified: nothing to refresh, nothing attributable to report.
            val response = String(post("/sdk/v1/entitlement/refresh", token, "{}".toByteArray()), Charsets.UTF_8)
            val refreshed = Regex("\"token\"\\s*:\\s*\"([A-Za-z0-9_.-]+)\"").find(response)?.groupValues?.get(1) ?: throw java.io.IOException("Unexpected refresh response")
            synchronized(this) { if (handle != 0L) Bridge.entitlementImport(handle, refreshed.toByteArray(Charsets.US_ASCII), clock()) }
            while (true) {
                val report = synchronized(this) { if (handle == 0L) null else pendingReport(ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }) } ?: break
                post("/sdk/v1/usage", report.token, report.body)
                synchronized(this) { if (handle != 0L) reportDone() }  // Cleared only after the server confirmed.
            }
            lastSyncError = null
        } catch (error: Exception) { lastSyncError = "network: ${error.message ?: error.javaClass.simpleName}" }
        synchronized(this) { if (handle != 0L) { save(); notifyWarnings() } }
        return lastSyncError
    }
    /** Background [syncNow], at most hourly unless [force]. Never blocks or throws into the caller. */
    @JvmOverloads fun sync(force: Boolean = false) {
        if (!online || endpoint == null) return
        synchronized(this) {
            val now = System.nanoTime() / 1_000_000
            if (syncing || (!force && now < nextSync)) return
            syncing = true; nextSync = now + 3_600_000
        }
        Thread({ try { syncNow() } finally { synchronized(this) { syncing = false } } }, "fisica-entitlement-sync").apply { isDaemon = true }.start()
    }

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
