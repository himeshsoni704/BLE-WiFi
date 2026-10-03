package com.campuspresence.app

import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import com.campuspresence.app.core.Advert
import com.campuspresence.app.core.Aggregator
import com.campuspresence.app.core.CampusApi
import com.campuspresence.app.core.NodeIdentity
import com.campuspresence.app.core.Tokens
import com.campuspresence.app.core.Wire
import java.io.IOException

/**
 * Classroom node: a phone that stands in for the smart board. It fetches its room identity with the room's node key,
 * advertises that room's rotating marker (0x02 | idx | token) so student phones can prove they are in the room, and
 * reports the student tokens it hears. Put it where the board would be, powered and awake.
 */
class NodeService : Service() {

    companion object {
        const val ACTION_STOP = "com.campuspresence.app.STOP_NODE"
        private const val CHANNEL = "node"
        private const val TICK_MS = 5_000L
        private const val HEARTBEAT_MS = 10_000L
        private const val MAX_AGE_S = 100.0

        @Volatile var running = false
    }

    private lateinit var settings: Settings
    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler

    private var api: CampusApi? = null
    private var identity: NodeIdentity? = null
    private val aggregator = Aggregator()
    private val pending = ArrayList<Aggregator.Sighting>()
    private var advertiser: BleAdvertiser? = null
    private var scanner: BleScanner? = null

    private fun now() = System.currentTimeMillis() / 1000.0

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        thread = HandlerThread("campus-node").also { it.start() }
        handler = Handler(thread.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            Foreground.start(this, 12, CHANNEL, "Campus Presence node", "Acting as the classroom marker")
        } catch (e: SecurityException) {
            Status.node = "cannot start: ${Status.describe(e)}"
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            Status.node = "starting"
            handler.post { setup() }
        }
        return START_STICKY
    }

    private fun fail(msg: String) {
        Status.node = msg
        Status.log("node: $msg")
        stopSelf()
    }

    private fun setup() {
        val a = CampusApi(settings.serverUrl).also { it.nodeKey = settings.nodeKey }
        val id = try {
            a.nodeProvision()
        } catch (e: Exception) {
            fail("provisioning failed: ${Status.describe(e)}")
            return
        }
        api = a; identity = id
        Status.log("node for ${id.name} (marker ${id.markerIdx}) provisioned")
        advertiser = BleAdvertiser(this) { w ->
            Tokens.markerPayload(id.markerIdx, Tokens.marker(id.markerSecret, id.markerIdx, w))
        }
        scanner = BleScanner(this) { advert, rssi, ts ->
            if (advert is Advert.Student) aggregator.add(advert, rssi, ts)      // other markers are not our business
        }
        scanner?.start()?.let { Status.log("BLE scan: $it") }
        Status.node = "running as ${id.name}"
        handler.post(tick)
        handler.postDelayed(heartbeatLoop, HEARTBEAT_MS)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            try { advertiser?.tick(now()) } catch (e: Exception) { Status.log("advertise: ${Status.describe(e)}") }
            handler.postDelayed(this, TICK_MS)
        }
    }

    private val heartbeatLoop = object : Runnable {
        override fun run() {
            if (!running) return
            try { heartbeat() } catch (e: Exception) { Status.log("heartbeat failed: ${Status.describe(e)}") }
            handler.postDelayed(this, HEARTBEAT_MS)
        }
    }

    private fun heartbeat() {
        val a = api ?: return
        val t = now()
        pending.addAll(aggregator.drain())
        pending.removeAll { t - it.timestamp > MAX_AGE_S }
        val batch = pending.take(Wire.MAX_SIGHTINGS)
        val reply = try {
            a.nodeHeartbeat(Wire.heartbeat(aggregator.recentStudents(t), batch, t))
        } catch (e: IOException) {
            throw e                                       // keep the sightings, try again next beat
        } catch (e: Exception) {
            pending.removeAll(batch.toSet())
            throw e
        }
        pending.removeAll(batch.toSet())
        advertiser?.error?.let { Status.log("BLE advertise: $it") }
        scanner?.error?.let { Status.log("BLE scan: $it") }
        Status.log("heartbeat: heard ${aggregator.recentStudents(t)} students, sent ${batch.size} sightings, " +
            "accepted ${reply.optInt("sightings_accepted")}, rejected ${reply.optInt("sightings_rejected")}")
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        advertiser?.stop()
        scanner?.stop()
        thread.quitSafely()
        Status.node = "stopped"
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
