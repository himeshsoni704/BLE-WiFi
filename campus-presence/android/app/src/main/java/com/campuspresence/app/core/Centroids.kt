package com.campuspresence.app.core

import org.json.JSONObject

/** The server's per-room mean Wi-Fi fingerprints (GET /wifi/reference), for an on-phone location hint. */
class Reference(val apIds: List<String>, val centroids: Map<String, DoubleArray>, val missing: Double)

data class Estimate(val zone: String, val distance: Double, val matchedAps: Int)

object Centroids {
    fun parse(json: JSONObject): Reference {
        val ids = json.getJSONArray("ap_ids").let { a -> List(a.length()) { a.getString(it) } }
        val cs = json.getJSONObject("centroids")
        val centroids = HashMap<String, DoubleArray>()
        for (zone in cs.keys()) {
            val arr = cs.getJSONArray(zone)
            centroids[zone] = DoubleArray(arr.length()) { arr.getDouble(it) }
        }
        return Reference(ids, centroids, json.optDouble("missing_value", -100.0))
    }

    /**
     * Nearest room centroid by Euclidean distance over the reference APs ("not heard" counts as the missing value).
     * Null when no reference AP was heard at all, or nothing has been surveyed. It is a hint with no probability.
     * `catalogue` maps upper-case BSSID -> ap_id.
     */
    fun estimate(ref: Reference, catalogue: Map<String, String>, aps: List<ApRow>): Estimate? {
        if (ref.centroids.isEmpty() || ref.apIds.isEmpty()) return null
        val heard = HashMap<String, Double>()
        for (a in aps) {
            val id = catalogue[a.bssid.uppercase()] ?: continue
            heard[id] = maxOf(heard[id] ?: -127.0, a.rssi.toDouble())
        }
        val matched = ref.apIds.count { it in heard }
        if (matched == 0) return null
        val observed = DoubleArray(ref.apIds.size) { heard[ref.apIds[it]] ?: ref.missing }
        var best: Estimate? = null
        for ((zone, c) in ref.centroids) {
            if (c.size != observed.size) continue
            var d = 0.0
            for (i in observed.indices) d += (observed[i] - c[i]) * (observed[i] - c[i])
            val dist = Math.sqrt(d)
            if (best == null || dist < best.distance) best = Estimate(zone, dist, matched)
        }
        return best
    }
}
