package com.carenco.fisica

import android.bluetooth.*
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.os.Handler
import android.os.Looper
import java.util.UUID

/**
 * OS IO, not protocol policy. Host must grant permissions and select a Scale.
 * Use it as the [FisicaTransport] of a [FisicaClient], or wire the callbacks yourself.
 * Callbacks run on the main thread.
 */
@Suppress("DEPRECATION", "MissingPermission")
class BleTransport(private val context: Context, private val adapter: BluetoothAdapter?) : FisicaTransport, AutoCloseable {
    var onDevice: (ScanResult)->Unit = {}
    override var onReady: ()->Unit = {}
    override var onBytes: (ByteArray)->Unit = {}
    override var onLost: ()->Unit = {}
    override var onSendFailure: (Throwable)->Unit = {}
    override var onError: (Throwable)->Unit = {}
    /** ATT MTU agreed with the Scale; 23 until negotiation succeeds. */
    val mtu get() = outgoing.mtu
    private val main = Handler(Looper.getMainLooper())
    private var gatt: BluetoothGatt? = null
    private var write: BluetoothGattCharacteristic? = null
    private var device: BluetoothDevice? = null
    private var writeID: UUID? = null; private var notifyID: UUID? = null
    private var busy = false; private var ready = false; private var scanning = false; private var discovering = false
    private val outgoing = BleWriteQueue()
    private val ccc = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    private fun fail(message: String) { onError(FisicaException(1001,message)) }
    private val scan = object: ScanCallback() {
        override fun onScanResult(type: Int, result: ScanResult) { main.post { onDevice(result) } }
        override fun onScanFailed(code: Int) { main.post { scanning=false; fail("BLE scan error $code") } }
    }
    // Null when Bluetooth is absent or switched off; SecurityException when a permission is missing.
    private fun scanner() = try { adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner } catch (error: SecurityException) { null }
    /** Reports an error instead of throwing when Bluetooth is off, unsupported or not permitted. */
    fun scan() { main.post {
        val scanner=scanner()
        if (scanner==null) { fail("Bluetooth is off, unavailable or not permitted"); return@post }
        try { scanner.startScan(scan); scanning=true } catch (error: SecurityException) { fail("Bluetooth scan permission is missing") }
    } }
    fun stopScan() { main.post { quietStopScan() } }
    private fun quietStopScan() {
        if (!scanning) return
        scanning=false
        try { scanner()?.stopScan(scan) } catch (error: RuntimeException) { /* Adapter was switched off meanwhile. */ }
    }
    /** Choose the Scale and its GATT profile for a later [open]. */
    @JvmOverloads fun select(device: BluetoothDevice, profile: FisicaBleProfile = FisicaBleProfile.SCALE) { main.post {
        this.device=device; writeID=UUID.fromString(profile.write); notifyID=UUID.fromString(profile.notify)
    } }
    /** Connect to the selected Scale; [onReady] follows once notifications are enabled. */
    override fun open() { main.post {
        val target=device
        if (target==null || writeID==null || notifyID==null) { fail("Select a Scale and profile before opening"); onLost(); return@post }
        quietStopScan(); reset(); closeGatt()
        try { gatt=target.connectGatt(context,false,callback,BluetoothDevice.TRANSPORT_LE) }
        catch (error: SecurityException) { fail("Bluetooth connect permission is missing"); onLost() }
    } }
    fun connect(device: BluetoothDevice, writeUUID: UUID, notifyUUID: UUID) {
        select(device,FisicaBleProfile(writeUUID.toString(),notifyUUID.toString())); open()
    }
    override fun send(bytes: ByteArray) { main.post {
        if (!ready) { onSendFailure(FisicaException(1001,"BLE is not ready")); return@post }
        outgoing.enqueue(bytes); pump()
    } }
    private fun pump() {
        val g=gatt; val c=write
        if (busy || g==null || c==null) return
        val chunk=outgoing.next() ?: return
        c.writeType=if(c.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT else BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE; c.value=chunk; busy=true
        val accepted=try { g.writeCharacteristic(c) } catch (error: SecurityException) { false }
        if (!accepted) { busy=false; outgoing.clear(); onSendFailure(FisicaException(1001,"BLE write failed")) }
    }
    /** State of one connection attempt; a reconnect must not inherit the previous MTU or queue. */
    private fun reset() { ready=false; busy=false; discovering=false; outgoing.reset(); write=null }
    private fun closeGatt() {
        val g=gatt ?: return
        gatt=null
        try { g.disconnect(); g.close() } catch (error: RuntimeException) { /* Permission revoked or adapter off: nothing left to release. */ }
    }
    override fun close() { main.post { quietStopScan(); reset(); closeGatt() } }
    private fun discover(g: BluetoothGatt) {
        if (discovering) return  // A later MTU update must not restart discovery.
        discovering=true
        if (!g.discoverServices()) { fail("Service discovery failed"); abandon(g) }
    }
    /** A connection that cannot become ready is closed and reported as lost instead of waiting forever. */
    private fun abandon(g: BluetoothGatt) { if (g===gatt) { reset(); closeGatt(); onLost() } }
    private val callback = object: BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt,status: Int,state: Int) { main.post {
            if (g!==gatt) return@post
            if (status==BluetoothGatt.GATT_SUCCESS && state==BluetoothProfile.STATE_CONNECTED) {
                // Negotiate before discovery; a refused request leaves the default MTU in use.
                if (!g.requestMtu(BleWriteQueue.REQUESTED_MTU)) discover(g)
            } else { if(status!=0) fail("BLE connection error $status"); reset(); closeGatt(); onLost() }
        } }
        override fun onMtuChanged(g: BluetoothGatt,size: Int,status: Int) { main.post {
            if (g!==gatt) return@post
            outgoing.negotiated(size,status==BluetoothGatt.GATT_SUCCESS)
            discover(g)
        } }
        override fun onServicesDiscovered(g: BluetoothGatt,status: Int) { main.post {
            if (g!==gatt) return@post
            if(status!=BluetoothGatt.GATT_SUCCESS) { fail("Service discovery error $status"); abandon(g); return@post }
            val characteristics=g.services.flatMap { it.characteristics }
            write=characteristics.firstOrNull { it.uuid==writeID }
            val notification=characteristics.firstOrNull { it.uuid==notifyID }
            if(write==null || notification==null) { fail("Scale characteristic UUIDs not found"); abandon(g); return@post }
            val descriptor=notification.getDescriptor(ccc)
            if(descriptor==null || !g.setCharacteristicNotification(notification,true)) { fail("Notifications unavailable"); abandon(g); return@post }
            descriptor.value=if(notification.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE else BluetoothGattDescriptor.ENABLE_INDICATION_VALUE
            if(!g.writeDescriptor(descriptor)) { fail("Enable notifications failed"); abandon(g) }
        } }
        override fun onDescriptorWrite(g: BluetoothGatt,d: BluetoothGattDescriptor,status: Int) { main.post {
            if (g!==gatt) return@post
            if(status==BluetoothGatt.GATT_SUCCESS && d.uuid==ccc && !ready) { ready=true; onReady() }
            else if(status!=0) { fail("Notification setup error $status"); abandon(g) }
        } }
        override fun onCharacteristicChanged(g: BluetoothGatt,c: BluetoothGattCharacteristic) {
            val data=c.value?.clone()?:return; if(c.uuid==notifyID) main.post { onBytes(data) }
        }
        override fun onCharacteristicChanged(g: BluetoothGatt,c: BluetoothGattCharacteristic,value: ByteArray) {
            val data=value.clone(); if(c.uuid==notifyID) main.post { onBytes(data) }
        }
        override fun onCharacteristicWrite(g: BluetoothGatt,c: BluetoothGattCharacteristic,status: Int) { main.post {
            if (g!==gatt) return@post
            busy=false; if(status!=0) { outgoing.clear(); onSendFailure(FisicaException(1001,"BLE write error $status")) } else pump()
        } }
    }
}
