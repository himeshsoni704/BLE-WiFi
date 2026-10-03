package com.blewifi.presence

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Student phone: advertises the rotating token over BLE and posts a device report every 30 s
 * (connected BSSID/RSSI, a 4 s accel+gyro window, and recent screen use).
 */
class StudentService : Service() {

    companion object {
        const val TAG = "StudentService"
        const val MANUFACTURER_ID = 0xFFFF
        const val REPORT_PERIOD_MS = 30_000L
        const val SAMPLE_MS = 4_000L
        const val CHANNEL = "student"
        const val ACTION_STOP = "com.blewifi.presence.STOP_STUDENT"

        @Volatile var running = false
        @Volatile var lastStatus = "idle"
    }

    private lateinit var settings: Settings
    private lateinit var api: Api
    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler
    private var advertiser: BluetoothLeAdvertiser? = null
    private var advCallback: AdvertiseCallback? = null
    private var advWindow = Long.MIN_VALUE
    @Volatile private var lastInteractionMs = 0L

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            lastInteractionMs = SystemClock.elapsedRealtime()
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            try { rotateAdvertisement() } catch (e: Exception) { Log.w(TAG, "advertise", e) }
            handler.postDelayed(this, 5_000L)
        }
    }

    private val reportLoop = object : Runnable {
        override fun run() {
            if (!running) return
            try { sendReport() } catch (e: Exception) {
                lastStatus = "report failed: ${e.message}"
                Log.w(TAG, "report", e)
            }
            handler.postDelayed(this, REPORT_PERIOD_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        api = Api(settings)
        thread = HandlerThread("student-svc").also { it.start() }
        handler = Handler(thread.looper)
        val f = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_USER_PRESENT)
        }
        registerReceiver(screenReceiver, f)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (settings.secret.length != 32) {
            lastStatus = "no secret provisioned"
            stopSelf()
            return START_NOT_STICKY
        }
        startForegroundCompat()
        if (!running) {
            running = true
            lastInteractionMs = SystemClock.elapsedRealtime()
            val bm = getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
            advertiser = bm.adapter?.bluetoothLeAdvertiser
            handler.post(tick)
            handler.postDelayed(reportLoop, 3_000L)
            lastStatus = "running"
        }
        return START_STICKY
    }

    private fun startForegroundCompat() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Presence", NotificationManager.IMPORTANCE_LOW))
        }
        val n = Notification.Builder(this, CHANNEL)
            .setContentTitle("Presence active")
            .setContentText("Broadcasting your attendance token")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            startForeground(1, n)
        }
    }

    /** Restart advertising whenever the 30 s token window rolls over. */
    @SuppressLint("MissingPermission")
    private fun rotateAdvertisement() {
        val adv = advertiser ?: run { lastStatus = "BLE advertising unavailable"; return }
        val window = Token.window(System.currentTimeMillis() / 1000)
        if (window == advWindow) return
        advCallback?.let { adv.stopAdvertising(it) }
        val payload = Token.forWindowBytes(settings.secret, window)
        val data = AdvertiseData.Builder()
            .setIncludeDeviceName(false)
            .addManufacturerData(MANUFACTURER_ID, payload)
            .build()
        val s = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(false)
            .build()
        val cb = object : AdvertiseCallback() {
            override fun onStartFailure(errorCode: Int) { lastStatus = "advertise failed: $errorCode" }
        }
        adv.startAdvertising(s, data, cb)
        advCallback = cb
        advWindow = window
    }

    @Suppress("DEPRECATION")
    private fun wifiInfo(): JSONObject? {
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val info = wm.connectionInfo ?: return null
        val bssid = info.bssid ?: return null
        if (bssid == "02:00:00:00:00:00") return null     // location permission missing / not connected
        return JSONObject().put("bssid", bssid.lowercase()).put("rssi", info.rssi.coerceIn(-127, 0))
    }

    /** Collect ~4 s of accel + gyro, then post it. Runs on the worker thread. */
    private fun sendReport() {
        val sm = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val acc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val gyr = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        val a = ArrayList<FloatArray>()
        val g = ArrayList<FloatArray>()
        val l = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                synchronized(a) {
                    if (e.sensor.type == Sensor.TYPE_ACCELEROMETER) a.add(e.values.copyOf(3))
                    else g.add(e.values.copyOf(3))
                }
            }
            override fun onAccuracyChanged(s: Sensor?, accuracy: Int) {}
        }
        var elapsed = SAMPLE_MS / 1000.0
        if (acc != null && gyr != null) {
            val t0 = SystemClock.elapsedRealtime()
            sm.registerListener(l, acc, 20_000, handler)   // ~50 Hz
            sm.registerListener(l, gyr, 20_000, handler)
            Thread.sleep(SAMPLE_MS)
            sm.unregisterListener(l)
            elapsed = (SystemClock.elapsedRealtime() - t0) / 1000.0
        }

        val body = JSONObject()
            .put("token", Token.now(settings.secret))
            .put("ts", System.currentTimeMillis() / 1000.0)
            .put("interacting", isInteracting())
        wifiInfo()?.let { body.put("wifi", it) }

        synchronized(a) {
            val n = minOf(a.size, g.size)
            if (n >= 20) {
                val fs = (n / elapsed).coerceIn(10.0, 200.0)
                val aj = JSONArray(); val gj = JSONArray()
                for (i in 0 until n) {
                    aj.put(JSONArray(a[i].map { it.toDouble() }))
                    gj.put(JSONArray(g[i].map { it.toDouble() }))
                }
                body.put("window", JSONObject().put("fs", fs).put("accel", aj).put("gyro", gj))
            }
        }
        val (code, text) = api.post("/device-report", body)
        lastStatus = if (code in 200..299) "last report ok" else "report HTTP $code: ${text.take(80)}"
    }

    private fun isInteracting(): Boolean {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isInteractive || SystemClock.elapsedRealtime() - lastInteractionMs < 60_000L
    }

    @SuppressLint("MissingPermission")
    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        advCallback?.let { runCatching { advertiser?.stopAdvertising(it) } }
        runCatching { unregisterReceiver(screenReceiver) }
        thread.quitSafely()
        lastStatus = "stopped"
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
