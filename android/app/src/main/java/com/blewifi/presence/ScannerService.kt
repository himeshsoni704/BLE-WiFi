package com.blewifi.presence

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothManager
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Scanner phone: listens for student tokens (manufacturer data, 8 bytes) and posts them in
 * batches to /scan/batch. The scanner must be registered first: PUT /scanners/{id} {zone}.
 */
class ScannerService : Service() {

    companion object {
        const val TAG = "ScannerService"
        const val FLUSH_MS = 5_000L
        const val CHANNEL = "scanner"
        const val ACTION_STOP = "com.blewifi.presence.STOP_SCANNER"

        @Volatile var running = false
        @Volatile var lastStatus = "idle"
        @Volatile var sightings = 0
    }

    private lateinit var settings: Settings
    private lateinit var api: Api
    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler
    private var scanner: BluetoothLeScanner? = null
    private val pending = ArrayList<JSONObject>()

    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val bytes = result.scanRecord?.getManufacturerSpecificData(StudentService.MANUFACTURER_ID) ?: return
            if (bytes.size != Token.TOKEN_BYTES) return
            val item = JSONObject()
                .put("token", Token.bytesToHex(bytes))
                .put("rssi", result.rssi.coerceIn(-127, 20))
                .put("ts", System.currentTimeMillis() / 1000.0)
            synchronized(pending) { pending.add(item) }
            sightings++
        }

        override fun onScanFailed(errorCode: Int) { lastStatus = "scan failed: $errorCode" }
    }

    private val flushLoop = object : Runnable {
        override fun run() {
            if (!running) return
            flush()
            handler.postDelayed(this, FLUSH_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        api = Api(settings)
        thread = HandlerThread("scanner-svc").also { it.start() }
        handler = Handler(thread.looper)
    }

    @SuppressLint("MissingPermission")
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (settings.scannerId.isEmpty()) {
            lastStatus = "no scanner id"
            stopSelf()
            return START_NOT_STICKY
        }
        startForegroundCompat()
        if (!running) {
            val bm = getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
            scanner = bm.adapter?.bluetoothLeScanner
            val sc = scanner
            if (sc == null) {
                lastStatus = "Bluetooth is off"
                stopSelf()
                return START_NOT_STICKY
            }
            val filter = ScanFilter.Builder().setManufacturerData(StudentService.MANUFACTURER_ID, ByteArray(0)).build()
            val s = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
            sc.startScan(listOf(filter), s, callback)
            running = true
            lastStatus = "scanning"
            handler.postDelayed(flushLoop, FLUSH_MS)
        }
        return START_STICKY
    }

    private fun startForegroundCompat() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Scanner", NotificationManager.IMPORTANCE_LOW))
        }
        val n = Notification.Builder(this, CHANNEL)
            .setContentTitle("Scanner active")
            .setContentText("Collecting attendance tokens")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(2, n,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            startForeground(2, n)
        }
    }

    private fun flush() {
        val batch: List<JSONObject>
        synchronized(pending) {
            if (pending.isEmpty()) return
            batch = ArrayList(pending)
            pending.clear()
        }
        // Android reports the same advertiser many times a second; keep the strongest per token per second
        val best = HashMap<String, JSONObject>()
        for (s in batch) {
            val key = s.getString("token") + "|" + s.getDouble("ts").toLong()
            val cur = best[key]
            if (cur == null || s.getInt("rssi") > cur.getInt("rssi")) best[key] = s
        }
        val arr = JSONArray(best.values.toList())
        try {
            val body = JSONObject().put("scanner_id", settings.scannerId).put("scans", arr)
            val (code, text) = api.post("/scan/batch", body)
            lastStatus = if (code in 200..299) "sent ${arr.length()} scans" else "HTTP $code: ${text.take(80)}"
            if (code !in 200..299) requeue(best.values)
        } catch (e: Exception) {
            lastStatus = "send failed: ${e.message}"
            Log.w(TAG, "flush", e)
            requeue(best.values)
        }
    }

    private fun requeue(items: Collection<JSONObject>) {
        synchronized(pending) {
            if (pending.size < 5_000) pending.addAll(0, items)
        }
    }

    @SuppressLint("MissingPermission")
    override fun onDestroy() {
        running = false
        runCatching { scanner?.stopScan(callback) }
        handler.removeCallbacksAndMessages(null)
        thread.quitSafely()
        lastStatus = "stopped"
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
