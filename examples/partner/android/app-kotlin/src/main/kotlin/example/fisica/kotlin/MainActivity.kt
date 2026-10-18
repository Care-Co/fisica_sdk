package example.fisica.kotlin

import android.Manifest
import android.app.Activity
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import com.carenco.fisica.*
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.Executor

/**
 * The same steps as every partner sample: SDK and core version, the license, pick a Scale (or the
 * synthetic demo), Connect (reads the battery), Measure (5 s), the report, Disconnect.
 */
class MainActivity : Activity() {
    companion object {
        /**
         * Paste an SDK key from the developer portal for full mode; empty runs in basic mode. The key is
         * bound to this app's package name, one seat for every install, so it ships inside the app
         * (pairing with a code is for PCs).
         */
        const val SDK_KEY = ""
        /** Activates the key for this app and renews it (needs the INTERNET permission). */
        const val PORTAL = FisicaEntitlement.DEFAULT_ENDPOINT
        const val NO_RESULT = "No stable result; check the load on the Scale and the received frames"
    }
    /** The Scale to connect: one found over BLE, or the synthetic demo Scale (no hardware). */
    private sealed class Choice {
        class Ble(val device: BluetoothDevice) : Choice()
        class Demo(val transport: MockTransport) : Choice()
    }
    private lateinit var licenseText: TextView
    private lateinit var status: TextView
    private lateinit var scanButton: Button
    private lateinit var demoButton: Button
    private lateinit var devices: RadioGroup
    private lateinit var connectButton: Button
    private lateinit var measureButton: Button
    private lateinit var result: TextView
    private lateinit var disconnectButton: Button
    private lateinit var core: FisicaCore
    private lateinit var license: FisicaEntitlement
    private lateinit var ble: BleTransport
    private val main = Handler(Looper.getMainLooper())
    private val found = HashMap<Int, BluetoothDevice>()
    private var choice: Choice? = null
    private var client: FisicaClient? = null
    private var connected = false
    private var busy = false
    private val permissions get() = if (Build.VERSION.SDK_INT >= 31)
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        fun button(label: String, action: () -> Unit) = Button(this).apply { text = label; setOnClickListener { action() } }
        licenseText = TextView(this)
        status = TextView(this).apply { text = "Scan for Scales, or try the demo" }
        scanButton = button("Scan for Scales") {
            if (permissions.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) requestPermissions(permissions, 1)
            else scan()
        }
        demoButton = button("Demo without hardware") { chooseDemo() }
        devices = RadioGroup(this).apply {
            setOnCheckedChangeListener { _, id -> found[id]?.let { choice = Choice.Ble(it); status.text = "${it.name} selected; press Connect"; refresh() } }
        }
        connectButton = button("Connect") { connect() }
        measureButton = button("Measure (5 s)") { measure() }
        result = TextView(this)
        disconnectButton = button("Disconnect") { release("Disconnected") }
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 32, 32, 32) }
        for (view in listOf<View>(licenseText, status, scanButton, demoButton, devices, connectButton, measureButton, result, disconnectButton)) {
            layout.addView(view)
        }
        setContentView(ScrollView(this).apply { addView(layout) })
        showReport(null); refresh()
        try {
            core = FisicaCore()
            // Basic mode without a key: measurement, weight and foot size; one frame a second, no raw data.
            license = FisicaEntitlement(core, PORTAL)  // the key is kept in the app's files by default
            license.onStatus = { runOnUiThread { showLicense() } }
            if (SDK_KEY.isNotEmpty()) license.importToken(SDK_KEY)
            showLicense()
        } catch (error: Throwable) {
            status.text = "Error: ${error.message}"; scanButton.isEnabled = false; demoButton.isEnabled = false
            return
        }
        ble = BleTransport(this, getSystemService(BluetoothManager::class.java)?.adapter)
        ble.onError = { status.text = "Error: ${it.message}" }
        ble.onDevice = { scan ->
            val device = scan.device
            if (device.name?.contains(FisicaBleProfile.NAME_CONTAINS) == true && found.values.none { it.address == device.address }) {
                val option = RadioButton(this).apply { id = View.generateViewId(); text = "${device.name}: ${device.address}" }
                found[option.id] = device
                devices.addView(option)
            }
        }
        refresh()
    }
    private fun showLicense() {
        val current = license.status()
        // "full mode", or "basic mode (no_key)" with the SDK's explanation of the reason.
        licenseText.text = listOfNotNull(
            "Fisica SDK ${FisicaRelease.SDK_VERSION} (core ${core.version})",
            "License: ${current.modeName} mode" + (current.reasonName?.let { " ($it)" } ?: ""),
            current.message,
            current.activationCode?.let { "Activation code: $it" },
        ).joinToString("\n")
    }
    private fun refresh() {
        scanButton.isEnabled = client == null; demoButton.isEnabled = client == null
        for (i in 0 until devices.childCount) devices.getChildAt(i).isEnabled = client == null
        connectButton.isEnabled = choice != null && client == null && !busy
        measureButton.isEnabled = connected && !busy
        disconnectButton.isEnabled = client != null && !(busy && connected)
    }
    private fun scan() {
        devices.clearCheck(); found.clear(); devices.removeAllViews()
        if (choice is Choice.Ble) choice = null
        ble.scan(); status.text = "Scanning for 5 seconds"; refresh()
        main.postDelayed({
            ble.stopScan()
            if (client == null) status.text = if (found.isEmpty()) "No Scale found; check that it is powered on" else "Choose a Scale"
        }, 5000)
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 && grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) scan()
        else status.text = "Permission denied; allow Bluetooth access in app settings"
    }
    /** Copy scale-mock.json from the sample folder to src/main/assets to try the app without a Scale. */
    private fun chooseDemo() {
        val transport = try { demoTransport(JSONObject(assets.open("scale-mock.json").bufferedReader().use { it.readText() })) }
            catch (error: IOException) { status.text = "Copy scale-mock.json to src/main/assets for the demo"; return }
            catch (error: JSONException) { status.text = "Error: ${error.message}"; return }
        devices.clearCheck(); choice = Choice.Demo(transport)
        status.text = "Demo without hardware selected; press Connect"; refresh()
    }
    /** The recorded synthetic replies of scale-mock.json; no measurement or protocol logic. */
    private fun demoTransport(script: JSONObject) = MockTransport().apply {
        fun bytes(hex: String) = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        val replies = script.getJSONObject("replies")
        for (name in replies.keys()) {
            val chunks = mutableListOf(bytes(replies.getString(name)))
            if (name == "START_CONTINUOUS_MEASUREMENT") repeat(script.getInt("frame_count")) { chunks.add(bytes(script.getString("measurement_hex"))) }
            reply(core.command(name), *chunks.toTypedArray())
        }
    }
    private fun connect() {
        val chosen = choice ?: return
        if (client != null || busy) return
        // MockTransport declares no link, so connect is always given one.
        val (transport, link) = when (chosen) {
            is Choice.Ble -> { ble.stopScan(); ble.select(chosen.device); ble to FisicaLink.BLE }
            is Choice.Demo -> chosen.transport to FisicaLink.USB
        }
        val current = FisicaClient(core, transport, license, Executor { runOnUiThread(it) })
        client = current; busy = true; showReport(null); status.text = "Connecting"; refresh()
        current.onError = { fail(it, current) }
        current.onLinkLost = { fail(IllegalStateException("The connection to the Scale was lost"), current) }
        current.connect(link) { _, error ->
            if (client !== current) return@connect
            if (error != null) { fail(error, current); return@connect }
            // Measurement packets carry no battery field: read it now; the report shows this reading.
            current.readBattery { _, _ ->
                if (client !== current) return@readBattery
                connected = true; busy = false
                status.text = "Connected (firmware ${current.firmwareVersion ?: "unknown"})"; refresh()
            }
        }
    }
    private fun measure() {
        val current = client ?: return
        if (!connected || busy) return
        busy = true; showReport(null); status.text = "Measuring for 5 seconds"; refresh()
        current.start { _, error ->
            if (client !== current) return@start
            if (error != null) { fail(error, current); return@start }
            main.postDelayed({
                if (client === current) current.stop { _, stopError ->
                    if (client !== current) return@stop
                    try {
                        if (stopError != null) throw stopError
                        // Weight and foot size come from every packet, in basic and full mode alike.
                        val report = current.report()
                        showReport(report)
                        status.text = if (report == null) NO_RESULT else "Measurement done"
                        busy = false; refresh()
                    } catch (failure: Throwable) { fail(failure, current) }
                }
            }, 5000)
        }
    }
    private fun showReport(report: SessionReport?) {
        fun cm(value: Double) = "%.1f cm".format(value)
        result.text = if (report == null) "Weight: -\nLeft foot: -\nRight foot: -\nFrames: -\nBattery: -" else listOf(
            "Weight: %.1f kg".format(report.weight),
            "Left foot: length ${cm(report.leftLength)}, width ${cm(report.leftWidth)}",
            "Right foot: length ${cm(report.rightLength)}, width ${cm(report.rightWidth)}",
            "Frames: ${report.frameCount}",
            "Battery: " + (report.battery?.let { "$it%" } ?: "not read"),
        ).joinToString("\n")
    }
    /** One clear line, and the client is always closed. */
    private fun fail(error: Throwable, failed: FisicaClient) {
        if (client === failed) release("Error: ${error.message ?: error}")
    }
    /** Close the client (the Scale leaves measurement mode); resources are freed even without an answer. */
    private fun release(message: String) {
        val current = client ?: return
        client = null; connected = false; busy = true; status.text = message; refresh()
        current.close { _, _ -> busy = false; refresh() }
    }
    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        if (::ble.isInitialized) ble.stopScan()
        client?.close { _, _ -> }
        if (::license.isInitialized) license.close()
        super.onDestroy()
    }
}
