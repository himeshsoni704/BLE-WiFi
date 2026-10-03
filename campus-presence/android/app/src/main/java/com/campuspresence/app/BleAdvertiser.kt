package com.campuspresence.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.content.Context
import com.campuspresence.app.core.Tokens

/**
 * Advertises one rotating payload as manufacturer data (company id 0xFFFF). The 30 s token window rolls over, so
 * advertising is restarted with a fresh payload each window. Call tick() every few seconds.
 */
class BleAdvertiser(context: Context, private val payloadForWindow: (Long) -> ByteArray) {
    private val adapter: BluetoothAdapter? =
        (context.applicationContext.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private var callback: AdvertiseCallback? = null
    private var window = Long.MIN_VALUE

    @Volatile var active = false
        private set
    @Volatile var error: String? = null
        private set

    @SuppressLint("MissingPermission")
    fun tick(nowSeconds: Double) {
        val a = adapter ?: run { error = "no Bluetooth adapter"; return }
        if (!a.isEnabled) { active = false; error = "Bluetooth is off"; return }
        val adv = a.bluetoothLeAdvertiser ?: run { active = false; error = "BLE advertising is not supported here"; return }
        val w = Tokens.window(nowSeconds)
        if (w == window && active) return
        callback?.let { runCatching { adv.stopAdvertising(it) } }
        val data = AdvertiseData.Builder().setIncludeDeviceName(false)
            .addManufacturerData(Tokens.COMPANY_ID, payloadForWindow(w)).build()
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(false).setTimeout(0).build()
        val cb = object : AdvertiseCallback() {
            override fun onStartSuccess(settingsInEffect: AdvertiseSettings) { active = true; error = null }
            override fun onStartFailure(errorCode: Int) {
                active = false
                window = Long.MIN_VALUE                  // try again on the next tick
                error = "advertise failed: " + when (errorCode) {
                    ADVERTISE_FAILED_DATA_TOO_LARGE -> "data too large"
                    ADVERTISE_FAILED_TOO_MANY_ADVERTISERS -> "too many advertisers"
                    ADVERTISE_FAILED_ALREADY_STARTED -> "already started"
                    ADVERTISE_FAILED_INTERNAL_ERROR -> "internal error"
                    ADVERTISE_FAILED_FEATURE_UNSUPPORTED -> "feature unsupported"
                    else -> "code $errorCode"
                }
            }
        }
        window = w
        callback = cb
        adv.startAdvertising(settings, data, cb)
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        callback?.let { cb -> runCatching { adapter?.bluetoothLeAdvertiser?.stopAdvertising(cb) } }
        callback = null
        active = false
        window = Long.MIN_VALUE
    }
}
