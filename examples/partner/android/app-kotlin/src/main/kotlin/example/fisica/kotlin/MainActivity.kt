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
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.carenco.fisica.*
import java.util.concurrent.Executor

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
    }
    private lateinit var status: TextView
    private lateinit var licenseText: TextView
    private lateinit var core: FisicaCore
    private lateinit var license: FisicaEntitlement
    private lateinit var devices: LinearLayout
    private lateinit var ble: BleTransport
    private val main = Handler(Looper.getMainLooper())
    private var client: FisicaClient? = null
    private val seen = HashSet<String>()
    private val permissions get() = if (Build.VERSION.SDK_INT >= 31)
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        status = TextView(this).apply { text = "Grant Bluetooth access, scan, then choose a Scale" }
        licenseText = TextView(this)
        devices = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        layout.addView(licenseText); layout.addView(status)
        layout.addView(Button(this).apply { text = "Scan BLE"; setOnClickListener {
            if (permissions.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) requestPermissions(permissions, 1)
            else scan()
        } })
        layout.addView(devices); setContentView(layout)
        core = FisicaCore()
        // Basic mode without a key: measurement, weight and foot size; one frame a second, no raw data.
        license = FisicaEntitlement(core, PORTAL, filesDir)
        license.onStatus = { runOnUiThread { showLicense() } }
        if (SDK_KEY.isNotEmpty()) license.importToken(SDK_KEY)
        showLicense()
        ble = BleTransport(this, getSystemService(BluetoothManager::class.java)?.adapter)
        ble.onError = { status.text = it.message }
        ble.onDevice = { result ->
            val device = result.device
            if (device.name?.contains(FisicaBleProfile.NAME_CONTAINS) == true && seen.add(device.address)) {
                devices.addView(Button(this).apply { text = "${device.name}: ${device.address}"; setOnClickListener { measure(device) } })
            }
        }
    }
    private fun showLicense() {
        val current = license.status()
        licenseText.text = "License: $current" + (current.activationCode?.let { "\nActivation code: $it" } ?: "")
    }
    private fun scan() {
        seen.clear(); devices.removeAllViews(); ble.scan(); status.text = "Scanning for 5 seconds"
        main.postDelayed({ ble.stopScan() }, 5000)
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 && grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) scan()
        else status.text = "Permission denied; allow Bluetooth access in app settings"
    }
    private fun measure(device: BluetoothDevice) {
        if (client != null) return
        ble.stopScan(); ble.select(device)
        val current = FisicaClient(core, ble, license, Executor { runOnUiThread(it) })
        client = current
        fun failed(error: Throwable) { status.text = error.message; current.close { _, _ -> client = null } }
        current.onError = { failed(it) }
        current.connect(FisicaLink.BLE) { _, error ->
            if (error != null) { failed(error); return@connect }
            current.start { _, startError ->
                if (startError != null) { failed(startError); return@start }
                status.text = "Measuring for 5 seconds"
                main.postDelayed({ current.stop { _, stopError ->
                    try {
                        if (stopError != null) throw stopError
                        val report = current.report()
                        status.text = report?.let { "Weight: ${it.weight} kg; frames: ${it.frameCount} (${license.status().modeName} mode)" }
                            ?: "No stable result"
                    } catch (failure: Throwable) { status.text = failure.message }
                    finally { current.close { _, _ -> client = null } }
                } }, 5000)
            }
        }
    }
    override fun onDestroy() {
        main.removeCallbacksAndMessages(null); ble.stopScan(); client?.close { _, _ -> }
        license.close()
        super.onDestroy()
    }
}
