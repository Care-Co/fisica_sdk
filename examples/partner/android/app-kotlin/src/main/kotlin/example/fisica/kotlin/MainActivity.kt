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
    private lateinit var status: TextView
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
        devices = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        layout.addView(status)
        layout.addView(Button(this).apply { text = "Scan BLE"; setOnClickListener {
            if (permissions.any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }) requestPermissions(permissions, 1)
            else scan()
        } })
        layout.addView(devices); setContentView(layout)
        ble = BleTransport(this, getSystemService(BluetoothManager::class.java)?.adapter)
        ble.onError = { status.text = it.message }
        ble.onDevice = { result ->
            val device = result.device
            if (device.name?.contains(FisicaBleProfile.NAME_CONTAINS) == true && seen.add(device.address)) {
                devices.addView(Button(this).apply { text = "${device.name}: ${device.address}"; setOnClickListener { measure(device) } })
            }
        }
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
        val current = FisicaClient(FisicaCore(), ble, callbacks = Executor { runOnUiThread(it) })
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
                        status.text = report?.let { "Weight: ${it.weight} kg; frames: ${it.frameCount}" } ?: "No stable result"
                    } catch (failure: Throwable) { status.text = failure.message }
                    finally { current.close { _, _ -> client = null } }
                } }, 5000)
            }
        }
    }
    override fun onDestroy() {
        main.removeCallbacksAndMessages(null); ble.stopScan(); client?.close { _, _ -> }
        super.onDestroy()
    }
}
