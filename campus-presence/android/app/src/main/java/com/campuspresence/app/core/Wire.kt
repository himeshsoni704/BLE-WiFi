package com.campuspresence.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom

/** One access point as heard in a Wi-Fi scan. */
data class ApRow(val bssid: String, val ssid: String, val rssi: Int, val frequency: Int, val connected: Boolean = false)

/**
 * Request bodies, shaped to the backend's strict schemas (campus-presence/backend/app/schemas.py rejects unknown
 * fields and out-of-range values), so every limit enforced there is enforced here first.
 */
object Wire {
    const val MAX_BLE_BATCH = 200
    const val MAX_APS = 64
    const val MAX_SURVEY_SCANS = 200
    const val MAX_SIGHTINGS = 200

    private val rng = SecureRandom()

    /** 20 hex chars: satisfies the server's ^[A-Za-z0-9_-]{8,48}$ and is unique per upload (replay protection). */
    fun nonce(): String = Hex.encode(ByteArray(10).also { rng.nextBytes(it) })

    private fun rssi(v: Double): Double = v.coerceIn(-127.0, 0.0)

    /** `observerToken` proves which phone is reporting; the server checks it against the logged-in student. */
    fun bleItem(s: Aggregator.Sighting, observerToken: String?, nonce: String = nonce()): JSONObject {
        val o = JSONObject()
        when (s.kind) {
            Aggregator.Kind.MARKER -> o.put("kind", "marker").put("marker_idx", s.markerIdx).put("marker_token", s.token)
            Aggregator.Kind.PEER -> {
                o.put("kind", "peer").put("observed_token", s.token)
                if (observerToken != null) o.put("observer", observerToken)
            }
        }
        return o.put("rssi", rssi(s.rssi)).put("timestamp", s.timestamp).put("duration", s.duration.coerceIn(0.0, 3600.0))
            .put("samples", s.samples).put("nonce", nonce)
    }

    fun bleBatch(items: List<JSONObject>): JSONObject = JSONObject().put("observations", JSONArray(items))

    private fun apJson(a: ApRow, withConnected: Boolean): JSONObject {
        val o = JSONObject().put("bssid", a.bssid.take(24)).put("ssid", a.ssid.take(64)).put("rssi", rssi(a.rssi.toDouble()))
        if (a.frequency in 2000..7200) o.put("frequency", a.frequency)
        if (withConnected) o.put("connected", a.connected)
        return o
    }

    private fun strongest(aps: List<ApRow>): List<ApRow> = aps.sortedByDescending { it.rssi }.take(MAX_APS)

    /** `source` is "scan" for results from a scan we just triggered, "cached" for whatever the system already had. */
    fun wifiObservation(aps: List<ApRow>, ts: Double, source: String, scanAgeS: Double?, nonce: String = nonce()): JSONObject {
        require(aps.isNotEmpty()) { "no access points" }
        val o = JSONObject().put("timestamp", ts).put("nonce", nonce).put("source", source)
            .put("wifi", JSONArray(strongest(aps).map { apJson(it, true) }))
        if (scanAgeS != null) o.put("scan_age_s", scanAgeS.coerceIn(0.0, 86400.0))
        return o
    }

    fun survey(zone: String, scans: List<List<ApRow>>): JSONObject {
        require(scans.size in 1..MAX_SURVEY_SCANS) { "1..$MAX_SURVEY_SCANS scans per request" }
        return JSONObject().put("zone", zone).put("register_unknown_aps", true)
            .put("scans", JSONArray(scans.map { JSONObject().put("wifi", JSONArray(strongest(it).map { a -> apJson(a, false) })) }))
    }

    /** States use the server's vocabulary: ble active|off|denied|unsupported, wifi active|off|denied|throttled|unsupported. */
    fun presence(ts: Double, bleState: String, wifiState: String, counts: Map<String, Int>, zone: String?,
                 nonce: String = nonce()): JSONObject {
        val o = JSONObject().put("timestamp", ts).put("nonce", nonce).put("ble_state", bleState).put("wifi_state", wifiState)
            .put("counts", JSONObject(counts))
        if (zone != null) o.put("zone_estimate", JSONObject().put("zone", zone).put("method", "nearest_centroid"))
        return o
    }

    fun heartbeat(detected: Int, sightings: List<Aggregator.Sighting>, ts: Double): JSONObject = JSONObject()
        .put("detected_count", detected).put("node_kind", "android").put("timestamp", ts)
        .put("sightings", JSONArray(sightings.sortedByDescending { it.rssi }.take(MAX_SIGHTINGS).map {
            JSONObject().put("token", it.token).put("rssi", rssi(it.rssi)).put("duration", it.duration.coerceIn(0.0, 3600.0))
                .put("timestamp", it.timestamp)
        }))
}
