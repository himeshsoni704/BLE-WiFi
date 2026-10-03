package com.campuspresence.app

import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import com.campuspresence.app.core.Aggregator
import com.campuspresence.app.core.ApiException
import com.campuspresence.app.core.ApRow
import com.campuspresence.app.core.CampusApi
import com.campuspresence.app.core.Centroids
import com.campuspresence.app.core.Provision
import com.campuspresence.app.core.Reference
import com.campuspresence.app.core.Session
import com.campuspresence.app.core.Tokens
import com.campuspresence.app.core.Wire
import java.io.IOException

/**
 * Student phone. Logs in, fetches its own token secret, then:
 *  - advertises its rotating token (0x01 | token) so classroom nodes and other phones can see it,
 *  - scans for classroom markers (0x02 | idx | token) and other students, and uploads smoothed observations,
 *  - uploads Wi-Fi scans, and a presence heartbeat with an on-phone room hint.
 * Everything runs on one worker thread, so uploads never overlap.
 */
class StudentService : Service() {

    companion object {
        const val ACTION_STOP = "com.campuspresence.app.STOP_STUDENT"
        private const val CHANNEL = "student"
        private const val TICK_MS = 5_000L
        private const val FLUSH_MS = 10_000L
        private const val WIFI_MS = 30_000L
        private const val WIFI_READ_DELAY_MS = 6_000L
        private const val PRESENCE_MS = 60_000L
        private const val MAX_AGE_S = 100.0                 // the server rejects anything more than 120 s old

        @Volatile var running = false
    }

    private lateinit var settings: Settings
    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler

    private var api: CampusApi? = null
    private var session: Session? = null
    private var provision: Provision? = null
    private var secret = ""
    private var reference: Reference? = null

    private val aggregator = Aggregator()
    private val pending = ArrayList<Aggregator.Sighting>()
    private var advertiser: BleAdvertiser? = null
    private var scanner: BleScanner? = null
    private lateinit var wifi: WifiProbe

    private var bleSamples = 0
    private var wifiUploads = 0
    private var lastWifi: List<ApRow> = emptyList()
    private var wifiState = "active"

    private fun now() = System.currentTimeMillis() / 1000.0

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        wifi = WifiProbe(this)
        thread = HandlerThread("campus-student").also { it.start() }
        handler = Handler(thread.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            Foreground.start(this, 11, CHANNEL, "Campus Presence", "Broadcasting your attendance token")
        } catch (e: SecurityException) {                  // a permission the foreground-service type needs is missing
            Status.student = "cannot start: ${Status.describe(e)}"
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            Status.student = "starting"
            handler.post { setup() }
        }
        return START_STICKY
    }

    private fun fail(msg: String) {
        Status.student = msg
        Status.log("student: $msg")
        stopSelf()
    }

    private fun setup() {
        val a = CampusApi(settings.serverUrl)
        val s = Session(a, settings.username, settings.password)
        try {
            s.login()
            val p = a.provision()
            api = a; session = s; provision = p; secret = p.tokenSecret
            Status.log("signed in as ${p.displayName}; ${p.rooms.size} rooms, ${p.apCatalogue.size} known access points")
        } catch (e: Exception) {
            fail("sign-in failed: ${Status.describe(e)}")
            return
        }
        advertiser = BleAdvertiser(this) { w -> Tokens.studentPayload(Tokens.student(secret, w)) }
        scanner = BleScanner(this) { advert, rssi, ts ->
            if (aggregator.add(advert, rssi, ts)) synchronized(this) { bleSamples++ }
        }
        scanner?.start()?.let { Status.log("BLE scan: $it") }
        Status.student = "running"
        handler.post(tick)
        handler.postDelayed(flushLoop, FLUSH_MS)
        handler.postDelayed(wifiLoop, 2_000L)
        handler.postDelayed(presenceLoop, PRESENCE_MS)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            try { advertiser?.tick(now()) } catch (e: Exception) { Status.log("advertise: ${Status.describe(e)}") }
            handler.postDelayed(this, TICK_MS)
        }
    }

    private val flushLoop = object : Runnable {
        override fun run() {
            if (!running) return
            try { flush() } catch (e: Exception) { Status.log("BLE upload failed: ${Status.describe(e)}") }
            handler.postDelayed(this, FLUSH_MS)
        }
    }

    private val wifiLoop = object : Runnable {
        override fun run() {
            if (!running) return
            val requested = wifi.request()
            handler.postDelayed({ if (running) readWifi(requested) }, WIFI_READ_DELAY_MS)
            handler.postDelayed(this, WIFI_MS)
        }
    }

    private val presenceLoop = object : Runnable {
        override fun run() {
            if (!running) return
            try { heartbeat() } catch (e: Exception) { Status.log("presence upload failed: ${Status.describe(e)}") }
            handler.postDelayed(this, PRESENCE_MS)
        }
    }

    private fun flush() {
        val s = session ?: return
        val a = api ?: return
        val t = now()
        pending.addAll(aggregator.drain())
        pending.removeAll { t - it.timestamp > MAX_AGE_S }
        if (pending.isEmpty()) return
        val batch = pending.take(Wire.MAX_BLE_BATCH)
        val items = batch.map { Wire.bleItem(it, Tokens.studentHex(secret, Tokens.window(it.timestamp))) }
        val reply = try {
            s.authed { a.bleObservation(Wire.bleBatch(items)) }
        } catch (e: ApiException) {
            pending.removeAll(batch.toSet())              // the server understood and refused: retrying would just repeat it
            throw e
        } catch (e: IOException) {
            throw e                                       // network trouble: keep the observations and try again
        }
        pending.removeAll(batch.toSet())
        val rejected = HashMap<String, Int>()
        reply.optJSONArray("results")?.let { arr ->
            for (i in 0 until arr.length()) {
                val r = arr.getJSONObject(i)
                if (r.optString("status") == "rejected") rejected.merge(r.optString("reason", "?"), 1, Int::plus)
            }
        }
        reply.optString("state").takeIf { it.isNotEmpty() && it != "null" }?.let { Status.serverState = it }
        val markers = batch.count { it.kind == Aggregator.Kind.MARKER }
        Status.log("BLE: sent ${batch.size} (${markers} marker, ${batch.size - markers} peer), accepted ${reply.optInt("accepted")}" +
            (if (rejected.isEmpty()) "" else ", rejected $rejected") + "; state ${Status.serverState}")
    }

    private fun readWifi(requested: Boolean) {
        val s = session ?: return
        val a = api ?: return
        wifiState = wifi.state()
        val scan = wifi.latest()
        if (scan == null) {
            Status.log("Wi-Fi: no scan results (state: $wifiState)")
            return
        }
        lastWifi = scan.aps
        if (scan.ageS > MAX_AGE_S) {
            wifiState = "throttled"
            Status.log("Wi-Fi: newest scan is ${scan.ageS.toInt()} s old (Android throttles scans); not uploading")
            return
        }
        val source = if (requested && scan.ageS < 20) "scan" else "cached"
        try {
            val r = s.authed { a.wifiObservation(Wire.wifiObservation(scan.aps, now(), source, scan.ageS)) }
            if (r.optString("status") == "accepted") {
                wifiUploads++
                r.optString("state").takeIf { it.isNotEmpty() && it != "null" }?.let { Status.serverState = it }
                val warn = r.optString("warning").takeIf { it.isNotEmpty() && it != "null" }
                Status.log("Wi-Fi: ${scan.aps.size} APs, server says zone ${r.opt("predicted_zone")} (${r.opt("confidence")})" +
                    (warn?.let { "; $it" } ?: ""))
            } else {
                val why = r.optString("reason")
                Status.log("Wi-Fi rejected: $why" + if (why == "no_known_aps") " (ask faculty to survey the rooms first)" else "")
            }
        } catch (e: Exception) {
            Status.log("Wi-Fi upload failed: ${Status.describe(e)}")
        }
    }

    private fun heartbeat() {
        val s = session ?: return
        val a = api ?: return
        // Refresh the room hint inputs: the survey may have added access points and room fingerprints since the last beat.
        provision = s.authed { a.provision() }
        reference = try { s.authed { a.wifiReference() } } catch (e: Exception) { reference }
        val hint = reference?.let { Centroids.estimate(it, provision!!.apCatalogue, lastWifi) }
        val bleState = when {
            advertiser?.active == true && scanner?.error == null -> "active"
            advertiser?.error == "Bluetooth is off" -> "off"
            else -> "unsupported"
        }
        val counts = mapOf("ble_samples" to synchronized(this) { bleSamples }, "wifi_scans_uploaded" to wifiUploads)
        s.authed { a.presence(Wire.presence(now(), bleState, wifiState, counts, hint?.zone)) }
        Status.log("heartbeat: BLE $bleState, Wi-Fi $wifiState, on-phone room hint ${hint?.zone ?: "none"}")
        advertiser?.error?.let { Status.log("BLE advertise: $it") }
        scanner?.error?.let { Status.log("BLE scan: $it") }
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        advertiser?.stop()
        scanner?.stop()
        thread.quitSafely()
        Status.student = "stopped"
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
