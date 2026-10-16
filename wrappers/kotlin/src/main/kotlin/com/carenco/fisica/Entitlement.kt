package com.carenco.fisica

import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest

/**
 * The license of one [FisicaEntitlement] at one moment. Codes are the generated constants;
 * times are Unix seconds, 0 when not set.
 */
class EntitlementStatus(
    /** [FisicaMode.FULL] or [FisicaMode.BASIC]. */
    val mode: Int,
    /** Why the SDK is in basic mode ([FisicaModeReason]); NONE in full mode. */
    val reason: Int,
    /** [FisicaFeature] bits open now; 0 in basic mode. */
    val features: Int,
    /** The key's contract by its signed dates ([FisicaContract]). */
    val contract: Int,
    /** [FisicaContractKind]. */
    val contractKind: Int,
    /** [FisicaTrack]: trial, extension or contract. */
    val track: Int,
    /** What the key is activated for ([FisicaBindType]); NONE while it is not activated. */
    val bindType: Int,
    /** What this app or PC is ([FisicaBindType]); NONE when the core cannot read it. */
    val targetType: Int,
    val company: String?,
    val contractReference: String?,
    /** The key (seat) ID. */
    val key: String?,
    val grants: List<String>,
    /** App ID or PC hash the key is activated for. */
    val bind: String?,
    /** This app's ID or this PC's hash, as the core reads it. */
    val target: String?,
    val verifiedAt: Long,
    /** Renew online or import a renewal file by then. */
    val refreshBy: Long,
    /** The key's expiry; 0 for a key without one. */
    val keyUntil: Long,
    /** End of full mode for the key's trial or contract (contract end plus grace). */
    val fullUntil: Long,
    val contractStart: Long,
    val contractEnd: Long,
    val revokedAt: Long,
    /** Outcome of the last import ([FisicaEntitlementOutcome]). */
    val lastImport: Int,
    /** [FisicaKeyEnvironment]: production or TEST signing key. */
    val keyEnvironment: Int,
    /** [FisicaEntitlementWarning] bits. */
    val warnings: Int,
    /** FAC1 code to enter in the developer portal for offline activation, while the key is not activated. */
    val activationCode: String?,
    /** Last storage or network problem, or null. Never a license state. */
    val lastSyncError: String?,
) {
    val isFull get() = mode == FisicaMode.FULL
    /** True while full mode opens every bit of [feature] ([FisicaFeature]). */
    fun hasFeature(feature: Int) = feature != 0 && features and feature == feature
    val modeName get() = if (isFull) "full" else "basic"
    /** [reason] as a name such as "no_key" or "not_activated"; null in full mode. */
    val reasonName: String? get() = REASONS.getOrNull(reason)
    /** What basic mode means for this [reason] and what to do; null in full mode. */
    val message: String? get() = reasonName?.let { MESSAGES[it] + " " + BASIC }
    /** Messages for the [warnings] bits. */
    val warningMessages: List<String> get() = WARNINGS.filter { warnings and it.key != 0 }.map { it.value }
    override fun toString() = "$modeName mode" + (reasonName?.let { " ($it)" } ?: "")

    companion object {
        private val REASONS = listOf(null, "no_key", "legacy_token", "not_activated", "no_target", "other_target", "revoked",
            "clock_rollback", "key_expired", "not_started", "full_ended")
        private const val BASIC = "Basic mode keeps connection, measurement, weight and foot size; raw data and full-rate frames need full mode."
        private val MESSAGES = mapOf(
            "no_key" to "No SDK key. Create one in the developer portal and import it.",
            "legacy_token" to "This is a contract confirmation, not an SDK key. Create an SDK key in the developer portal.",
            "not_activated" to "The SDK key is not activated yet: connect to the internet, or enter the activation code in the developer portal and import the file it gives.",
            "no_target" to "The SDK cannot read this app's ID or this PC's identity, so a key cannot be activated here.",
            "other_target" to "This SDK key is activated for another app or PC.",
            "revoked" to "The SDK key was released or its contract was revoked.",
            "clock_rollback" to "This device's clock is earlier than a time the SDK has already seen; correct the clock.",
            "key_expired" to "The SDK key expired: connect to renew it, or import a renewal file from the developer portal.",
            "not_started" to "The contract of this SDK key has not started yet.",
            "full_ended" to "The trial or contract of this SDK key has ended.",
        )
        private val WARNINGS = linkedMapOf(
            FisicaEntitlementWarning.RENEW_DUE to "The SDK key is due for renewal: connect to the internet or import a renewal file.",
            FisicaEntitlementWarning.GRACE to "The contract has ended; full mode continues during the grace period.",
            FisicaEntitlementWarning.FULL_ENDING to "Full mode ends within 7 days (trial or contract end).",
            FisicaEntitlementWarning.IMPORT_REJECTED to "The last SDK key or file was rejected (format, unknown signing key, signature or another app/PC).",
            FisicaEntitlementWarning.TEST_KEY to "The SDK key is signed with a TEST key; this is not a production license.",
        )
    }
}

/**
 * One SDK instance's license: its SDK key, the key's activation for this app or PC, and renewal.
 *
 * The native core verifies the server-signed key, reads the target itself (the package name on
 * Android, this PC's hash on a desktop JVM) and decides the mode:
 * - full mode: a key activated for this app or PC, within its key expiry and its trial or
 *   contract. Every frame, and raw data ([Measurement.raw], [Measurement.packet], [FisicaCore.decode]).
 * - basic mode: everything else. Connection, measurement, settings, weight and foot size work;
 *   a session hands out at most one frame a second, without raw data.
 *
 * [endpoint]: developer portal base URL. With it, an imported key that is not activated yet is
 * activated for this app or PC automatically, and a key is renewed before it expires, in the
 * background (the app needs the INTERNET permission). Without it no network is used: activate
 * offline with [activationCode] and import the file the portal gives. [online] false keeps a
 * configured endpoint unused. [storage]: directory for the license state (for example
 * `context.filesDir`), one file per company and contract; null keeps it in memory only.
 * Network and storage failures never change the license; they only set [lastSyncError].
 *
 * SDK use is not tracked: the stored state is the signed key and the latest time seen, and the
 * server receives only the core's activation request (this app's ID or PC hash) and empty
 * renewal requests.
 */
class FisicaEntitlement @JvmOverloads constructor(
    @Suppress("unused") private val core: FisicaCore,
    endpoint: String? = null,
    private val storage: File? = null,
    private val online: Boolean = true,
    private val clock: () -> Long = { System.currentTimeMillis() / 1000 },
) : AutoCloseable {
    private var handle = Bridge.entitlementCreate()
    private val endpoint = endpoint?.trimEnd('/')
    private var syncing = false
    private val syncLock = Any()  // One exchange with the server at a time.
    private var nextSync = 0L
    private var warned = 0
    private var noticed: List<Any?>? = null
    /** Changes whenever the key changes (import, activation, renewal, release, restore); clients re-apply the license. */
    @Volatile internal var generation = 0; private set
    /** Last storage or network problem, or null. Never a license state. */
    @Volatile var lastSyncError: String? = null; private set
    /** Called with newly raised [FisicaEntitlementWarning] bits. May run on a background thread. */
    @Volatile var onWarning: (Int, EntitlementStatus) -> Unit = { _, _ -> }
    /** Called when the mode, reason, features, warnings or key change. May run on a background thread. */
    @Volatile var onStatus: (EntitlementStatus) -> Unit = {}

    @Synchronized override fun close() { if (handle != 0L) { Bridge.entitlementDestroy(handle); handle = 0 } }
    private fun open(): Long { check(handle != 0L) { "Closed entitlement context" }; return handle }
    private fun now(): Long = clock().also { require(it in 0..(1L shl 53)) { "Expected Unix seconds" } }
    private fun monotonic() = System.nanoTime() / 1_000_000
    private fun text(field: Int): String? = Bridge.entitlementText(handle, field)?.let { String(it, Charsets.UTF_8) }
    /** Serialized use of the core context with the current wall-clock time (handle 0 once closed: basic mode). */
    internal fun <T> locked(block: (Long, Long) -> T): T = synchronized(this) { block(handle, now()) }

    /**
     * Import an SDK key or a key file's content (activation or renewal file from the developer
     * portal). Returns a [FisicaEntitlementOutcome]; a rejected or older key, or one activated for
     * another app or PC, leaves the current key untouched. The state stored for the same company
     * and contract (a key activated or renewed earlier) wins over an older original key file.
     * Online, a new key is activated in the background.
     */
    fun importToken(token: String): Int {
        val outcome = synchronized(this) {
            val bytes = token.toByteArray(Charsets.UTF_8)
            val now = now()
            val result = Bridge.entitlementImport(open(), bytes, now)
            if (result == FisicaEntitlementOutcome.ACCEPTED) {
                nextSync = 0  // a new key may need activation now
                try {
                    stateFile()?.takeIf { it.isFile }?.let {
                        // The key activated or renewed earlier wins over the original key file.
                        Bridge.entitlementRestore(handle, it.readBytes()); Bridge.entitlementImport(handle, bytes, now)
                    }
                } catch (error: Exception) { lastSyncError = "storage: ${error.message}" }
                save(); generation++
            }
            result
        }
        notifyStatus()
        if (outcome == FisicaEntitlementOutcome.ACCEPTED) sync()
        return outcome
    }
    /** Offline setup: a key file from the developer portal works on a device that is never online. */
    fun importFile(file: File): Int = importToken(file.readText(Charsets.UTF_8))

    private fun stateFile(): File? {
        val directory = storage ?: return null
        val company = text(FisicaEntitlementText.COMPANY).orEmpty()
        if (company.isEmpty()) return null
        val contract = text(FisicaEntitlementText.CONTRACT).orEmpty()
        // Keyed by company and contract, the attribution unit; the key inside says which seat.
        // Never an installation or device ID.
        val digest = MessageDigest.getInstance("SHA-256").digest("$company\n$contract".toByteArray(Charsets.UTF_8))
        return File(directory, "entitlement-" + digest.take(8).joinToString("") { "%02x".format(it) } + ".state")
    }
    private fun save() {
        try {
            val file = stateFile() ?: return
            file.parentFile?.mkdirs()
            val temporary = File(file.path + ".tmp")
            temporary.writeBytes(Bridge.entitlementExport(handle))
            if (!temporary.renameTo(file)) { file.delete(); if (!temporary.renameTo(file)) throw IOException("cannot replace ${file.name}") }
        } catch (error: Exception) { lastSyncError = "storage: ${error.message}" }
    }
    private fun deleteState(file: File?) {
        try { if (file != null && file.exists() && !file.delete()) throw IOException("cannot delete ${file.name}") }
        catch (error: Exception) { lastSyncError = "storage: ${error.message}" }
    }

    /** Mode, reason, features, the key and its dates for the current time. */
    @Synchronized fun status(): EntitlementStatus {
        val values = Bridge.entitlementStatus(open(), now())
        val reason = values[1].toInt()
        fun nonEmpty(field: Int) = text(field)?.ifEmpty { null }
        return EntitlementStatus(
            mode = values[0].toInt(), reason = reason, features = values[2].toInt(), contract = values[3].toInt(),
            contractKind = values[4].toInt(), track = values[5].toInt(), bindType = values[6].toInt(), targetType = values[7].toInt(),
            company = nonEmpty(FisicaEntitlementText.COMPANY), contractReference = nonEmpty(FisicaEntitlementText.CONTRACT),
            key = nonEmpty(FisicaEntitlementText.KEY),
            grants = text(FisicaEntitlementText.GRANTS).orEmpty().split(',').filter { it.isNotEmpty() },
            bind = nonEmpty(FisicaEntitlementText.BIND), target = nonEmpty(FisicaEntitlementText.TARGET),
            verifiedAt = values[8], refreshBy = values[9], keyUntil = values[10], fullUntil = values[11],
            contractStart = values[12], contractEnd = values[13], revokedAt = values[14],
            lastImport = values[15].toInt(), keyEnvironment = values[16].toInt(), warnings = values[17].toInt(),
            activationCode = if (reason == FisicaModeReason.NOT_ACTIVATED) text(FisicaEntitlementText.ACTIVATION_CODE) else null,
            lastSyncError = lastSyncError,
        )
    }
    /** True while full mode opens every bit of [feature] ([FisicaFeature]). */
    fun hasFeature(feature: Int): Boolean = synchronized(this) {
        val values = Bridge.entitlementStatus(open(), now())
        feature != 0 && values[0] == FisicaMode.FULL.toLong() && values[2].toInt() and feature == feature
    }
    /**
     * FAC1 code for activating the loaded key in the developer portal, for a device that is never
     * online; null without a key that still needs activation (or when the core cannot read this app or PC).
     */
    @Synchronized fun activationCode(): String? { open(); return text(FisicaEntitlementText.ACTIVATION_CODE) }
    /**
     * Release the key activated for this app or PC: the core forgets it and returns the FRC1
     * release code to enter in the developer portal, which frees the seat. The stored state is
     * deleted. Throws [FisicaException] ([FisicaStatus.INVALID_STATE]) without such a key.
     */
    fun release(): String {
        val code = synchronized(this) {
            open()
            val file = stateFile()
            val code = String(Bridge.entitlementRelease(handle), Charsets.US_ASCII)
            deleteState(file); generation++
            code
        }
        notifyStatus()
        return code
    }

    private fun notifyStatus() {
        val (status, fresh, changed) = synchronized(this) {
            if (handle == 0L) return
            val status = status()
            val fresh = status.warnings and warned.inv(); warned = status.warnings
            val signature = listOf(status.mode, status.reason, status.features, status.warnings, status.key, status.verifiedAt, status.bindType)
            val changed = signature != noticed; noticed = signature
            Triple(status, fresh, changed)
        }
        if (fresh != 0) try { onWarning(fresh, status) } catch (_: Exception) {}
        if (changed) try { onStatus(status) } catch (_: Exception) {}
    }
    /**
     * Re-evaluate the license for the current time (a trial or contract ending, a renewal due) and
     * activate or renew in the background when due. Called from feature paths; it records nothing
     * about the use and never blocks or throws.
     */
    fun checkStatus() {
        try { notifyStatus(); sync() } catch (error: Exception) { lastSyncError = "check: ${error.message}" }
    }

    private class Due(val path: String, val token: String, val body: ByteArray)
    /** What the server should do now: activate, renew, or nothing. */
    private fun due(force: Boolean): Due? = synchronized(this) {
        if (handle == 0L) return null
        val values = Bridge.entitlementStatus(handle, now())
        val token = text(FisicaEntitlementText.TOKEN).orEmpty()
        if (token.isEmpty()) return null
        val renew = Due("/sdk/v1/entitlement/refresh", token, "{}".toByteArray(Charsets.US_ASCII))
        when (values[1].toInt()) {
            FisicaModeReason.NO_KEY, FisicaModeReason.LEGACY_TOKEN, FisicaModeReason.OTHER_TARGET,
            FisicaModeReason.NO_TARGET, FisicaModeReason.REVOKED -> if (force) renew else null
            // The body is the core's request verbatim: this app's ID or PC hash, nothing else.
            FisicaModeReason.NOT_ACTIVATED -> text(FisicaEntitlementText.ACTIVATION_REQUEST)
                ?.let { Due("/sdk/v1/entitlement/activate", token, it.toByteArray(Charsets.UTF_8)) } ?: if (force) renew else null
            FisicaModeReason.KEY_EXPIRED, FisicaModeReason.FULL_ENDED, FisicaModeReason.NOT_STARTED -> renew
            else -> if (force || values[17].toInt() and FisicaEntitlementWarning.RENEW_DUE != 0) renew else null
        }
    }
    private fun post(path: String, token: String, body: ByteArray): ByteArray {
        val connection = URI(endpoint + path).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"; connection.connectTimeout = 5000; connection.readTimeout = 5000
            connection.doOutput = true; connection.useCaches = false
            connection.setRequestProperty("Authorization", "Bearer $token"); connection.setRequestProperty("Content-Type", "application/json")
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            if (code !in 200..299) {
                // A refused activation (key in use elsewhere, released, trial already used) is reported.
                val detail = try { connection.errorStream?.use { String(it.readBytes(), Charsets.UTF_8).take(200) } } catch (_: Exception) { null }
                throw IOException("HTTP $code ${detail.orEmpty()}".trim())
            }
            return connection.inputStream.use { it.readBytes() }
        } finally { connection.disconnect() }
    }
    /**
     * Activate or renew with the server now, on the calling thread (never the main thread): a key
     * not activated yet is activated, a key is renewed when renewal is due or with [force].
     * Returns null or the problem. A failure changes no license state and nothing is thrown.
     */
    @JvmOverloads fun syncNow(force: Boolean = false): String? {
        if (!online || endpoint == null) return null
        return synchronized(syncLock) { syncLocked(force) }
    }
    private fun syncLocked(force: Boolean): String? {
        try {
            val due = due(force) ?: return null
            val response = String(post(due.path, due.token, due.body), Charsets.UTF_8)
            val token = Regex("\"token\"\\s*:\\s*\"([A-Za-z0-9_.-]+)\"").find(response)?.groupValues?.get(1)
                ?: throw IOException("Unexpected response")
            lastSyncError = null
            synchronized(this) {
                if (handle == 0L) return null
                val now = now()
                val accepted = Bridge.entitlementImport(handle, token.toByteArray(Charsets.US_ASCII), now) == FisicaEntitlementOutcome.ACCEPTED
                if (Bridge.entitlementStatus(handle, now)[1].toInt() == FisicaModeReason.REVOKED) {
                    // The server reports this key released or revoked: the device lets go of it.
                    deleteState(stateFile())
                } else if (accepted) save()
                if (accepted) generation++
                nextSync = monotonic() + 3_600_000
            }
        } catch (error: Exception) {
            // A communication failure is not an expiry, a release or a revocation.
            lastSyncError = "network: ${error.message ?: error.javaClass.simpleName}"
        }
        try { notifyStatus() } catch (_: Exception) {}
        return lastSyncError
    }
    /** Background [syncNow], at most hourly after a success unless [force]. Never blocks or throws into the caller. */
    @JvmOverloads fun sync(force: Boolean = false) {
        if (!online || endpoint == null) return
        synchronized(this) {
            val now = monotonic()
            if (handle == 0L || syncing || (!force && now < nextSync)) return
            syncing = true; nextSync = now + 60_000  // until this attempt succeeds
        }
        try {
            Thread({
                try { syncNow(force) } catch (_: Throwable) {} finally { synchronized(this) { syncing = false } }
            }, "fisica-entitlement-sync").apply { isDaemon = true }.start()
        } catch (error: Throwable) { synchronized(this) { syncing = false }; lastSyncError = "network: ${error.message}" }
    }

    /** Opaque state for the host to store: the signed key and the latest time seen. Restoring verifies the key again. */
    @Synchronized fun exportState(): ByteArray = Bridge.entitlementExport(open())
    fun restoreState(state: ByteArray) {
        synchronized(this) { Bridge.entitlementRestore(open(), state); generation++ }
        notifyStatus()
    }
}
