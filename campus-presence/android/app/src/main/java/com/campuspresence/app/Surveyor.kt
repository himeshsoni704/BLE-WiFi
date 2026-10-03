package com.campuspresence.app

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.campuspresence.app.core.ApRow
import com.campuspresence.app.core.CampusApi
import com.campuspresence.app.core.Session
import com.campuspresence.app.core.SurveyBuffer
import com.campuspresence.app.core.Wire

/**
 * Staff tool: stand in a room, collect Wi-Fi scans labelled with that room, upload them as training data, then
 * retrain. The backend cannot place a real phone from Wi-Fi until at least two rooms have been surveyed
 * (8+ scans each). Runs while the screen is on; Android hands out only a few fresh scans per minute, so this takes
 * a few minutes per room (switch off "Wi-Fi scan throttling" in Developer options to speed it up).
 */
class Surveyor(context: Context, private val onChange: () -> Unit) {
    companion object {
        private const val REQUEST_MS = 30_000L
        private const val POLL_MS = 4_000L
    }

    private val probe = WifiProbe(context)
    private val ui = Handler(Looper.getMainLooper())
    private val buffer = SurveyBuffer()

    @Volatile var running = false
        private set

    val collected: Int get() = buffer.size

    val room: String? get() = buffer.room

    private val requestLoop = object : Runnable {
        override fun run() {
            if (!running) return
            probe.request()
            ui.postDelayed(this, REQUEST_MS)
        }
    }

    private val pollLoop = object : Runnable {
        override fun run() {
            if (!running) return
            poll()
            ui.postDelayed(this, POLL_MS)
        }
    }

    private fun poll() {
        val s = probe.latest() ?: return
        // every distinct system scan is one sample; a re-read of the same cached scan is not
        if (s.ageS > 45 || !buffer.add(s.aps, s.newestMicros)) return
        Status.survey = "collecting for room $room: $collected scans (last one heard ${s.aps.size} access points)"
        onChange()
    }

    /** Null when collecting started, otherwise why it did not (pending scans belong to a different room). */
    fun start(roomId: String): String? {
        if (running) return null
        buffer.begin(roomId)?.let { return it }
        running = true
        Status.survey = "collecting for room $roomId: $collected scans"
        ui.post(requestLoop)
        ui.post(pollLoop)
        return null
    }

    fun stop() {
        running = false
        ui.removeCallbacksAndMessages(null)
        Status.survey = "stopped with $collected scans for room $room waiting to upload"
    }

    fun discard() {
        buffer.discard()
        Status.survey = "discarded the pending scans"
    }

    /** Blocking: call from a worker thread. Uploads under the room the scans were collected in. */
    fun upload(api: CampusApi, session: Session): String {
        val p = buffer.pending() ?: return "nothing to upload yet"
        var stored = 0
        var perZone: Any? = null
        for (chunk in p.scans.chunked(Wire.MAX_SURVEY_SCANS)) {
            val r = session.authed { api.wifiSurvey(Wire.survey(p.room, chunk)) }
            stored += r.optInt("stored")
            perZone = r.opt("samples_per_zone")
        }
        buffer.uploaded(p)
        return "uploaded ${p.scans.size} scans for room ${p.room} ($stored stored); server now has $perZone"
    }

    fun retrain(api: CampusApi, session: Session): String {
        val r = session.authed { api.wifiRetrain() }
        return if (r.optString("status") == "trained") {
            "trained a ${r.optString("model")} model on ${r.optInt("n_scans")} scans of ${r.opt("zones")}; " +
                "cross-validated accuracy ${r.optDouble("cv_accuracy")} (optimistic: same walk)"
        } else {
            "not trained yet: ${r.optString("need")}; have ${r.opt("samples_per_zone")}"
        }
    }
}
