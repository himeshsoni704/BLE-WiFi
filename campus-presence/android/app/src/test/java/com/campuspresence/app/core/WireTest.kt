package com.campuspresence.app.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Bodies must satisfy the backend's strict (extra="forbid") schemas in campus-presence/backend/app/schemas.py. */
class WireTest {
    private val bleKeys = setOf("kind", "observer", "observed_token", "marker_idx", "marker_token", "rssi", "timestamp",
        "duration", "samples", "nonce")
    private val apKeys = setOf("bssid", "ssid", "rssi", "frequency", "connected")
    private val nonceRe = Regex("^[A-Za-z0-9_-]{8,48}$")
    private val tokenRe = Regex("^[0-9a-f]{16}$")

    private fun keys(o: JSONObject) = o.keys().asSequence().toSet()

    private fun marker(rssi: Double = -61.0) = Aggregator.Sighting(Aggregator.Kind.MARKER, 14, "0011223344556677", rssi, 1.79e9, 42.0, 300)
    private fun peer() = Aggregator.Sighting(Aggregator.Kind.PEER, null, "aabbccddeeff0011", -70.5, 1.79e9, 8.0, 40)

    @Test fun nonceMatchesServerPatternAndIsUnique() {
        val seen = HashSet<String>()
        repeat(500) { val n = Wire.nonce(); assertTrue(n, nonceRe.matches(n)); assertTrue(seen.add(n)) }
    }

    @Test fun markerItemShape() {
        val o = Wire.bleItem(marker(), observerToken = "1111111111111111")
        assertEquals("marker", o.getString("kind"))
        assertEquals(14, o.getInt("marker_idx"))
        assertTrue(tokenRe.matches(o.getString("marker_token")))
        assertFalse("observer is only sent on peer items", o.has("observer"))
        assertTrue(bleKeys.containsAll(keys(o)))
        assertTrue(nonceRe.matches(o.getString("nonce")))
    }

    @Test fun peerItemCarriesTheObserverToken() {
        val o = Wire.bleItem(peer(), observerToken = "1111111111111111")
        assertEquals("peer", o.getString("kind"))
        assertEquals("1111111111111111", o.getString("observer"))
        assertEquals("aabbccddeeff0011", o.getString("observed_token"))
        assertFalse(o.has("marker_idx"))
        assertTrue(bleKeys.containsAll(keys(o)))
    }

    @Test fun rssiAndDurationAreClampedIntoTheServersRange() {
        val o = Wire.bleItem(marker(rssi = 12.0).copy(duration = 99_999.0), null)
        assertEquals(0.0, o.getDouble("rssi"), 0.0)
        assertEquals(3600.0, o.getDouble("duration"), 0.0)
        assertEquals(-127.0, Wire.bleItem(marker(rssi = -300.0), null).getDouble("rssi"), 0.0)
    }

    @Test fun wifiObservationKeepsTheStrongest64ApsAndTheServerFields() {
        val aps = (1..80).map { ApRow("aa:bb:cc:00:00:%02x".format(it), "net".repeat(40), -30 - it, 2437, it == 1) }
        val o = Wire.wifiObservation(aps, 1.79e9, "scan", 3.2)
        val wifi = o.getJSONArray("wifi")
        assertEquals(64, wifi.length())
        assertEquals("aa:bb:cc:00:00:01", wifi.getJSONObject(0).getString("bssid"))
        assertTrue(wifi.getJSONObject(0).getBoolean("connected"))
        assertTrue((0 until 64).all { apKeys.containsAll(keys(wifi.getJSONObject(it))) })
        assertEquals(64, wifi.getJSONObject(0).getString("ssid").length)
        assertEquals("scan", o.getString("source"))
        assertEquals(3.2, o.getDouble("scan_age_s"), 0.0)
        assertTrue(nonceRe.matches(o.getString("nonce")))
        assertEquals(setOf("timestamp", "nonce", "source", "wifi", "scan_age_s"), keys(o))
    }

    @Test fun implausibleFrequencyIsOmittedRatherThanRejected() {
        val o = Wire.wifiObservation(listOf(ApRow("aa:bb:cc:00:00:01", "x", -50, 0)), 1.79e9, "cached", null)
        assertFalse(o.getJSONArray("wifi").getJSONObject(0).has("frequency"))
        assertFalse(o.has("scan_age_s"))
    }

    @Test fun surveyBodyHasZoneAndScans() {
        val scan = listOf(ApRow("aa:bb:cc:00:00:01", "x", -50, 2437), ApRow("aa:bb:cc:00:00:02", "y", -70, 5180))
        val o = Wire.survey("204", listOf(scan, scan))
        assertEquals("204", o.getString("zone"))
        assertTrue(o.getBoolean("register_unknown_aps"))
        assertEquals(2, o.getJSONArray("scans").length())
        assertFalse(o.getJSONArray("scans").getJSONObject(0).getJSONArray("wifi").getJSONObject(0).has("connected"))
    }

    @Test fun presenceBody() {
        val o = Wire.presence(1.79e9, "active", "throttled", mapOf("ble_samples" to 12), "204")
        assertEquals("active", o.getString("ble_state"))
        assertEquals("throttled", o.getString("wifi_state"))
        assertEquals(12, o.getJSONObject("counts").getInt("ble_samples"))
        assertEquals("204", o.getJSONObject("zone_estimate").getString("zone"))
        assertFalse(Wire.presence(1.79e9, "off", "off", emptyMap(), null).has("zone_estimate"))
    }

    @Test fun heartbeatBody() {
        val o = Wire.heartbeat(3, listOf(peer()), 1.79e9)
        assertEquals("android", o.getString("node_kind"))
        assertEquals(3, o.getInt("detected_count"))
        val s = o.getJSONArray("sightings").getJSONObject(0)
        assertEquals(setOf("token", "rssi", "duration", "timestamp"), keys(s))
        assertTrue(tokenRe.matches(s.getString("token")))
    }
}
