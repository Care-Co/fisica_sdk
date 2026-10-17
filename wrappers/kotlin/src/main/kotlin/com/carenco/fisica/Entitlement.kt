package com.carenco.fisica

import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

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
    val message: String? get() = FisicaLicenseMessages.REASONS[reason]
    /** Messages for the [warnings] bits. */
    val warningMessages: List<String> get() = FisicaLicenseMessages.WARNINGS.filter { warnings and it.key != 0 }.map { it.value }
    override fun toString() = "$modeName mode" + (reasonName?.let { " ($it)" } ?: "")

    companion object {
        private val REASONS = listOf(null, "no_key", "legacy_token", "not_activated", "no_target", "other_target", "revoked",
            "clock_rollback", "key_expired", "not_started", "full_ended")
        // Messages are shared by every wrapper: FisicaLicenseMessages (generated from wrappers/license-messages.json).
    }
}

/** What to show while pairing: an owner or admin enters [userCode] at [verificationUri] ([verificationUriComplete] holds the code). */
class PairingCode(val userCode: String, val verificationUri: String, val verificationUriComplete: String, val expiresIn: Int)

/** How pairing ended. APPROVED: the key is loaded and stored. */
enum class PairingResult { APPROVED, DENIED, EXPIRED, CANCELLED }

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
 * offline with [activationCode] and import the file the portal gives, or [pair] without a key.
 * [online] false keeps a configured endpoint unused. [storage]: directory for the license state
 * (for example `context.filesDir`), one file per seat; null keeps it in memory only. The key stored
 * there for this app or PC (paired or imported earlier) is loaded at once.
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

    init { if (loadStored()) { notifyStatus(); sync() } }

    /**
     * The key stored for this app or PC, if any. The core names a state file from the company, the
     * contract and the target, so a file it names again after restoring it belongs here. With
     * several, a full-mode key wins, then the most recently verified one.
     */
    private fun loadStored(): Boolean {
        val files = storage?.listFiles { file -> file.name.startsWith("entitlement-") && file.name.endsWith(".state") } ?: return false
        var best: Triple<Boolean, Long, ByteArray>? = null
        for (file in files.sortedBy { it.name }) {
            val probe = Bridge.entitlementCreate()
            try {
                val state = file.readBytes()
                Bridge.entitlementRestore(probe, state)
                if (Bridge.entitlementText(probe, FisicaEntitlementText.STATE_NAME)?.let { String(it, Charsets.UTF_8) } != file.name) continue
                val values = Bridge.entitlementStatus(probe, now())
                val full = values[0] == FisicaMode.FULL.toLong()
                val current = best
                if (current == null || (full && !current.first) || (full == current.first && values[8] > current.second)) {
                    best = Triple(full, values[8], state)
                }
            } catch (_: Exception) {
            } finally { Bridge.entitlementDestroy(probe) }
        }
        val chosen = best ?: return false
        return synchronized(this) { runCatching { Bridge.entitlementRestore(handle, chosen.third); generation++ }.isSuccess }
    }

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
        // One file per seat on this device, named by the core from the company, the contract and this
        // app's or PC's target, so every wrapper on a PC finds the same file. Never a usage record.
        val name = text(FisicaEntitlementText.STATE_NAME).orEmpty()
        return if (name.isEmpty()) null else File(directory, name)
    }
    private fun save() {
        try {
            val file = stateFile() ?: return
            file.parentFile?.mkdirs()
            // A temporary file of its own: other processes and wrappers on this PC may save the same seat.
            val temporary = File.createTempFile(file.name + ".", ".tmp", file.parentFile)
            try {
                temporary.writeBytes(Bridge.entitlementExport(handle))
                if (!temporary.renameTo(file)) { file.delete(); if (!temporary.renameTo(file)) throw IOException("cannot replace ${file.name}") }
            } finally { if (temporary.exists()) temporary.delete() }
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
     * deleted. Online, the server is also told in the background, which frees the seat at once
     * ([releaseNow] waits for that). Throws [FisicaException] ([FisicaStatus.INVALID_STATE]) without such a key.
     */
    fun release(): String {
        val (code, token) = releaseHere()
        if (token != null) Thread({ tellReleased(token) }, "fisica-entitlement-release").apply { isDaemon = true }.start()
        return code
    }
    /**
     * [release], then online tell the server with the key itself and wait (call it on a background
     * thread). Without `second == true`, enter the code at the key in the developer portal.
     */
    fun releaseNow(): Pair<String, Boolean> {
        val (code, token) = releaseHere()
        return code to (token != null && tellReleased(token))
    }
    private fun releaseHere(): Pair<String, String?> {
        val (code, token) = synchronized(this) {
            open()
            val file = stateFile()
            val token = text(FisicaEntitlementText.TOKEN).orEmpty()
            val code = String(Bridge.entitlementRelease(handle), Charsets.US_ASCII)
            deleteState(file); generation++
            code to token
        }
        notifyStatus()
        return code to if (online && endpoint != null && token.isNotEmpty()) token else null
    }
    private fun tellReleased(token: String): Boolean = try {
        post("/sdk/v1/entitlement/release", token, "{}".toByteArray(Charsets.US_ASCII)); true
    } catch (error: Exception) { lastSyncError = "network: ${error.message ?: error.javaClass.simpleName}"; false }

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
    /** What the server should do now, as the core decides it: activate, renew, or nothing. */
    private fun due(force: Boolean): Due? = synchronized(this) {
        if (handle == 0L) return null
        val token = text(FisicaEntitlementText.TOKEN).orEmpty()
        if (token.isEmpty()) return null
        val renew = Due("/sdk/v1/entitlement/refresh", token, "{}".toByteArray(Charsets.US_ASCII))
        when (Bridge.entitlementNextExchange(handle, now())) {
            // The body is the core's request verbatim: this app's ID or PC hash, nothing else.
            FisicaExchange.ACTIVATE -> text(FisicaEntitlementText.ACTIVATION_REQUEST)
                ?.let { Due("/sdk/v1/entitlement/activate", token, it.toByteArray(Charsets.UTF_8)) } ?: if (force) renew else null
            FisicaExchange.REFRESH -> renew
            else -> if (force) renew else null
        }
    }
    private fun send(path: String, token: String?, body: ByteArray): Pair<Int, String> {
        val connection = URI(endpoint + path).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"; connection.connectTimeout = 5000; connection.readTimeout = 5000
            connection.doOutput = true; connection.useCaches = false
            if (token != null) connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            return code to (stream?.use { String(it.readBytes(), Charsets.UTF_8) } ?: "")
        } finally { connection.disconnect() }
    }
    private fun post(path: String, token: String, body: ByteArray): ByteArray {
        val (code, text) = send(path, token, body)
        // A refused activation (key in use elsewhere, released, trial already used) is reported.
        if (code !in 200..299) throw IOException("HTTP $code ${text.take(200)}".trim())
        return text.toByteArray(Charsets.UTF_8)
    }
    private fun jsonString(text: String, name: String) = Json.string(text, name)
    private fun jsonNumber(text: String, name: String) = Json.number(text, name)

    /**
     * Get a key without one: the developer portal approves a code shown here (device authorization).
     *
     * Only this app's ID or this PC's hash is sent. [onCode] receives the code to show; an owner or
     * admin of the organization enters it at [PairingCode.verificationUri] and approves it with a new
     * or unused key. The key, already activated for this app or PC, then arrives, is loaded and
     * stored, and later instances load it by themselves. Blocks until the code is approved, denied or
     * expires, or [isCancelled] returns true: call it on a background thread. Throws
     * IllegalStateException without an endpoint or a readable app ID or PC, and IOException when
     * pairing cannot start.
     */
    @JvmOverloads fun pair(onCode: (PairingCode) -> Unit, isCancelled: () -> Boolean = { false }): PairingResult {
        checkNotNull(endpoint) { "Pairing needs the developer portal endpoint" }
        val request = synchronized(this) { open(); text(FisicaEntitlementText.ACTIVATION_REQUEST) }
            ?: throw IllegalStateException("The SDK cannot read this app's ID or this PC's identity, so a key cannot be activated here.")
        val (status, started) = send("/sdk/v1/pairing", null, request.toByteArray(Charsets.UTF_8))
        if (status != 200) throw IOException("HTTP $status ${started.take(200)}".trim())
        fun field(name: String) = jsonString(started, name) ?: throw IOException("Unexpected response")
        val deviceCode = field("device_code")
        val code = PairingCode(field("user_code"), field("verification_uri"), field("verification_uri_complete"),
            jsonNumber(started, "expires_in")?.toInt() ?: throw IOException("Unexpected response"))
        onCode(code)
        val body = "{\"device_code\":\"$deviceCode\"}".toByteArray(Charsets.UTF_8)
        var interval = jsonNumber(started, "interval") ?: 5.0
        // A key approved just before the code expires can still be received for a short while.
        val deadline = monotonic() + (code.expiresIn + 30) * 1000L
        while (true) {
            var wait = minOf((interval * 1000).toLong(), deadline - monotonic()).coerceAtLeast(0)
            while (wait > 0) {
                if (isCancelled()) return PairingResult.CANCELLED
                val step = minOf(wait, 200L); Thread.sleep(step); wait -= step
            }
            if (isCancelled()) return PairingResult.CANCELLED
            try {
                val (answerStatus, answer) = send("/sdk/v1/pairing/token", null, body)
                if (answerStatus == 200) {
                    val outcome = importToken(jsonString(answer, "token") ?: throw IOException("Unexpected response"))
                    check(outcome == FisicaEntitlementOutcome.ACCEPTED) { "The paired key was not accepted here ($outcome)" }
                    return PairingResult.APPROVED
                }
                when (jsonString(answer, "error")) {
                    "access_denied" -> return PairingResult.DENIED
                    "expired_token" -> return PairingResult.EXPIRED
                    "slow_down" -> interval += 5
                }
            } catch (error: IOException) { lastSyncError = "network: ${error.message}" }  // keep polling until the code expires
            if (monotonic() >= deadline) return PairingResult.EXPIRED
        }
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
            val (status, response) = send(due.path, due.token, due.body)
            if (status !in 200..299) {
                // A refused activation (key in use elsewhere, released, trial already used) does not change
                // within a minute: ask again in an hour. Other failures retry after a minute.
                if (status in 400..499 && status != 429) synchronized(this) { nextSync = monotonic() + 3_600_000 }
                throw IOException("HTTP $status ${response.take(200)}".trim())
            }
            val token = Json.string(response, "token") ?: throw IOException("Unexpected response")
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

/** The server's answers: a JSON object with string, number and literal members (escapes included). */
internal object Json {
    fun string(text: String, name: String): String? = parse(text)[name] as? String
    fun number(text: String, name: String): Double? = parse(text)[name] as? Double

    fun parse(text: String): Map<String, Any?> {
        val reader = Reader(text)
        val value = reader.value()
        reader.end()
        @Suppress("UNCHECKED_CAST")
        return value as? Map<String, Any?> ?: throw IOException("Unexpected response")
    }

    private class Reader(val text: String) {
        var at = 0
        fun fail(): Nothing = throw IOException("Unexpected response")
        fun space() { while (at < text.length && text[at] in " \t\r\n") at++ }
        fun end() { space(); if (at != text.length) fail() }
        fun expect(c: Char) { space(); if (text.getOrNull(at) != c) fail(); at++ }
        fun value(): Any? {
            space()
            return when (text.getOrNull(at) ?: fail()) {
                '{' -> {
                    at++; val members = LinkedHashMap<String, Any?>()
                    space(); if (text.getOrNull(at) == '}') { at++; return members }
                    while (true) {
                        space(); val name = string(); expect(':'); members[name] = value()
                        space(); if (text.getOrNull(at) == ',') { at++; continue }
                        expect('}'); return members
                    }
                }
                '[' -> {
                    at++; val items = ArrayList<Any?>()
                    space(); if (text.getOrNull(at) == ']') { at++; return items }
                    while (true) {
                        items.add(value())
                        space(); if (text.getOrNull(at) == ',') { at++; continue }
                        expect(']'); return items
                    }
                }
                '"' -> string()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else -> {
                    val start = at
                    while (at < text.length && (text[at].isDigit() || text[at] in "+-.eE")) at++
                    text.substring(start, at).toDoubleOrNull() ?: fail()
                }
            }
        }
        fun literal(word: String, value: Any?): Any? { if (!text.startsWith(word, at)) fail(); at += word.length; return value }
        fun string(): String {
            if (text.getOrNull(at) != '"') fail()
            at++
            val out = StringBuilder()
            while (true) {
                val c = text.getOrNull(at++) ?: fail()
                when (c) {
                    '"' -> return out.toString()
                    '\\' -> when (val escaped = text.getOrNull(at++) ?: fail()) {
                        '"', '\\', '/' -> out.append(escaped)
                        'b' -> out.append('\b'); 'f' -> out.append('\u000C'); 'n' -> out.append('\n')
                        'r' -> out.append('\r'); 't' -> out.append('\t')
                        'u' -> { if (at + 4 > text.length) fail(); out.append(text.substring(at, at + 4).toIntOrNull(16)?.toChar() ?: fail()); at += 4 }
                        else -> fail()
                    }
                    else -> out.append(c)
                }
            }
        }
    }
}
