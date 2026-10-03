package com.campuspresence.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import com.campuspresence.app.core.Advert
import com.campuspresence.app.core.Tokens

/** Scans for this system's manufacturer data (company id 0xFFFF) and hands each decoded advert to `onAdvert`. */
class BleScanner(context: Context, private val onAdvert: (Advert, Int, Double) -> Unit) {
    private val adapter: BluetoothAdapter? =
        (context.applicationContext.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private var scanning = false

    @Volatile var error: String? = null
        private set

    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = handle(result)

        override fun onBatchScanResults(results: MutableList<ScanResult>) = results.forEach { handle(it) }

        override fun onScanFailed(errorCode: Int) {
            scanning = false
            error = "scan failed: " + when (errorCode) {
                SCAN_FAILED_ALREADY_STARTED -> "already started"
                SCAN_FAILED_APPLICATION_REGISTRATION_FAILED -> "registration failed"
                SCAN_FAILED_FEATURE_UNSUPPORTED -> "unsupported"
                SCAN_FAILED_INTERNAL_ERROR -> "internal error"
                else -> "code $errorCode"
            }
        }
    }

    private fun handle(result: ScanResult) {
        val bytes = result.scanRecord?.getManufacturerSpecificData(Tokens.COMPANY_ID) ?: return
        val advert = Tokens.decode(bytes) ?: return
        onAdvert(advert, result.rssi, System.currentTimeMillis() / 1000.0)
    }

    /** Returns null when scanning started, otherwise why it could not. */
    @SuppressLint("MissingPermission")
    fun start(): String? {
        if (scanning) return null
        val a = adapter ?: return "no Bluetooth adapter"
        if (!a.isEnabled) return "Bluetooth is off"
        val scanner = a.bluetoothLeScanner ?: return "BLE scanning is not available"
        // Manufacturer id with an empty pattern matches any payload under 0xFFFF; Tokens.decode() does the real filtering.
        // A filter is also what lets Android keep scanning with the screen off.
        val filter = ScanFilter.Builder().setManufacturerData(Tokens.COMPANY_ID, ByteArray(0)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        error = null
        scanner.startScan(listOf(filter), settings, callback)
        scanning = true
        return null
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        if (!scanning) return
        runCatching { adapter?.bluetoothLeScanner?.stopScan(callback) }
        scanning = false
    }
}
