package com.campuspresence.app.core

/**
 * Collapses the many raw sightings BLE produces (the same advertiser several times a second) into one
 * smoothed observation per token per flush: median RSSI, how long it was in range, how many samples.
 * RSSI is a noisy proximity hint, never a distance.
 */
class Aggregator(private val maxSeries: Int = 1024) {

    enum class Kind { PEER, MARKER }

    data class Sighting(
        val kind: Kind,
        val markerIdx: Int?,
        val token: String,
        val rssi: Double,
        /** Unix seconds of the last sample. */
        val timestamp: Double,
        /** Seconds between the first and last sample of this flush window. */
        val duration: Double,
        val samples: Int,
    )

    private class Series(val kind: Kind, val idx: Int?, val token: String, var first: Double, var last: Double) {
        val rssi = ArrayList<Int>()
    }

    private val series = LinkedHashMap<String, Series>()
    private val lastSeenStudent = HashMap<String, Double>()

    /** False if the sample was dropped (invalid RSSI, or the table is full). */
    @Synchronized
    fun add(advert: Advert, rssi: Int, tsSeconds: Double): Boolean {
        if (rssi > 0 || rssi < -127) return false            // 127 means "unavailable"
        val key = when (advert) {
            is Advert.Marker -> "m:${advert.idx}:${advert.token}"
            is Advert.Student -> "p:${advert.token}"
        }
        var s = series[key]
        if (s == null) {
            if (series.size >= maxSeries) return false
            s = Series(
                if (advert is Advert.Marker) Kind.MARKER else Kind.PEER,
                (advert as? Advert.Marker)?.idx, advert.token, tsSeconds, tsSeconds,
            )
            series[key] = s
        }
        s.rssi.add(rssi)
        if (tsSeconds < s.first) s.first = tsSeconds
        if (tsSeconds > s.last) s.last = tsSeconds
        if (advert is Advert.Student) {
            lastSeenStudent[advert.token] = tsSeconds
            if (lastSeenStudent.size > 4 * maxSeries) lastSeenStudent.entries.removeAll { tsSeconds - it.value > 120 }
        }
        return true
    }

    /** Aggregated observations since the previous drain; the table is cleared. */
    @Synchronized
    fun drain(): List<Sighting> {
        val out = series.values.map { s ->
            Sighting(s.kind, s.idx, s.token, round1(median(s.rssi)), s.last, s.last - s.first, s.rssi.size)
        }
        series.clear()
        return out
    }

    /** Distinct student tokens heard in the last `withinS` seconds (tokens rotate, so this is a head count). */
    @Synchronized
    fun recentStudents(now: Double, withinS: Double = Tokens.WINDOW_S.toDouble()): Int =
        lastSeenStudent.values.count { now - it <= withinS }

    private fun median(v: List<Int>): Double {
        val s = v.sorted()
        val n = s.size
        return if (n % 2 == 1) s[n / 2].toDouble() else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }

    private fun round1(x: Double): Double = Math.round(x * 10.0) / 10.0
}
