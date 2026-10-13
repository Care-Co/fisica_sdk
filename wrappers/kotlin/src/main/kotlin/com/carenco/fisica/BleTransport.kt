package com.carenco.fisica

import android.bluetooth.*
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.os.Handler
import android.os.Looper
import java.util.UUID
import java.util.ArrayDeque

/** OS IO, not protocol policy. Host must grant permissions and select a Scale. */
@Suppress("DEPRECATION", "MissingPermission")
class BleTransport(private val context: Context, private val adapter: BluetoothAdapter) : AutoCloseable {
    var onDevice: (ScanResult)->Unit = {}
    var onReady: ()->Unit = {}
    var onBytes: (ByteArray)->Unit = {}
    var onLost: ()->Unit = {}
    var onError: (Throwable)->Unit = {}
    private val main = Handler(Looper.getMainLooper())
    private var gatt: BluetoothGatt? = null
    private var write: BluetoothGattCharacteristic? = null
    private var writeID: UUID? = null; private var notifyID: UUID? = null
    private var mtu = 23; private var busy = false; private var ready = false
    private val outgoing = ArrayDeque<ByteArray>()
    private val ccc = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    private fun fail(message: String) { onError(FisicaException(1001,message)) }
    private val scan = object: ScanCallback() {
        override fun onScanResult(type: Int, result: ScanResult) { main.post { onDevice(result) } }
        override fun onScanFailed(code: Int) { main.post { fail("BLE scan error $code") } }
    }
    fun scan() { adapter.bluetoothLeScanner.startScan(scan) }
    fun stopScan() { adapter.bluetoothLeScanner.stopScan(scan) }
    fun connect(device: BluetoothDevice, writeUUID: UUID, notifyUUID: UUID) {
        main.post { stopScan(); gatt?.close(); writeID=writeUUID; notifyID=notifyUUID; ready=false
            gatt=device.connectGatt(context,false,callback,BluetoothDevice.TRANSPORT_LE) }
    }
    fun send(bytes: ByteArray) { main.post {
        if (!ready) { fail("BLE is not ready"); return@post }
        bytes.asList().chunked(mtu-3).forEach { outgoing.add(it.toByteArray()) }; pump()
    } }
    private fun pump() {
        val g=gatt; val c=write
        if (busy || outgoing.isEmpty() || g==null || c==null) return
        c.writeType=if(c.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT else BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE; c.value=outgoing.removeFirst(); busy=true
        if (!g.writeCharacteristic(c)) { busy=false; outgoing.clear(); fail("BLE write failed") }
    }
    override fun close() { main.post { stopScan(); ready=false; outgoing.clear(); gatt?.disconnect(); gatt?.close(); gatt=null; write=null; busy=false } }
    private val callback = object: BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt,status: Int,state: Int) { main.post {
            if (status==BluetoothGatt.GATT_SUCCESS && state==BluetoothProfile.STATE_CONNECTED) {
                if (!g.discoverServices()) fail("Service discovery failed")
            } else { ready=false; outgoing.clear(); busy=false; if(status!=0) fail("BLE connection error $status"); onLost() }
        } }
        override fun onServicesDiscovered(g: BluetoothGatt,status: Int) { main.post {
            if(status!=BluetoothGatt.GATT_SUCCESS) { fail("Service discovery error $status"); return@post }
            val characteristics=g.services.flatMap { it.characteristics }
            write=characteristics.firstOrNull { it.uuid==writeID }
            val notification=characteristics.firstOrNull { it.uuid==notifyID }
            if(write==null || notification==null) { fail("Scale characteristic UUIDs not found"); return@post }
            val descriptor=notification.getDescriptor(ccc)
            if(descriptor==null || !g.setCharacteristicNotification(notification,true)) { fail("Notifications unavailable"); return@post }
            descriptor.value=if(notification.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE else BluetoothGattDescriptor.ENABLE_INDICATION_VALUE
            if(!g.writeDescriptor(descriptor)) fail("Enable notifications failed")
        } }
        override fun onDescriptorWrite(g: BluetoothGatt,d: BluetoothGattDescriptor,status: Int) { main.post {
            if(status==BluetoothGatt.GATT_SUCCESS && d.uuid==ccc && !ready) { ready=true; onReady() }
            else if(status!=0) fail("Notification setup error $status")
        } }
        override fun onCharacteristicChanged(g: BluetoothGatt,c: BluetoothGattCharacteristic) {
            val data=c.value?.clone()?:return; if(c.uuid==notifyID) main.post { onBytes(data) }
        }
        override fun onCharacteristicChanged(g: BluetoothGatt,c: BluetoothGattCharacteristic,value: ByteArray) {
            val data=value.clone(); if(c.uuid==notifyID) main.post { onBytes(data) }
        }
        override fun onCharacteristicWrite(g: BluetoothGatt,c: BluetoothGattCharacteristic,status: Int) { main.post {
            busy=false; if(status!=0) { outgoing.clear(); fail("BLE write error $status") } else pump()
        } }
        override fun onMtuChanged(g: BluetoothGatt,size: Int,status: Int) { main.post { if(status==0) mtu=size.coerceAtLeast(23) } }
    }
}
