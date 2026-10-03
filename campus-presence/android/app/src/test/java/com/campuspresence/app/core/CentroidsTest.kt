package com.campuspresence.app.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class CentroidsTest {
    private val ref = Centroids.parse(JSONObject("""{"ap_ids":["LIVE_01","LIVE_02","LIVE_03"],
        "centroids":{"204":[-45.0,-72.0,-80.0],"205":[-72.0,-46.0,-78.0]},"samples":{"204":14,"205":14},"missing_value":-100.0}"""))
    private val catalogue = mapOf("AA:BB:CC:00:00:01" to "LIVE_01", "AA:BB:CC:00:00:02" to "LIVE_02", "AA:BB:CC:00:00:03" to "LIVE_03")

    private fun ap(n: Int, rssi: Int) = ApRow("aa:bb:cc:00:00:0$n", "net", rssi, 2437)   // phones report lower case

    @Test fun picksTheNearestRoom() {
        assertEquals("204", Centroids.estimate(ref, catalogue, listOf(ap(1, -44), ap(2, -73), ap(3, -79)))!!.zone)
        assertEquals("205", Centroids.estimate(ref, catalogue, listOf(ap(1, -71), ap(2, -47), ap(3, -77)))!!.zone)
    }

    @Test fun anApNotHeardCountsAsTheMissingValue() {
        val e = Centroids.estimate(ref, catalogue, listOf(ap(1, -44)))
        assertNotNull(e)
        assertEquals(1, e!!.matchedAps)
    }

    @Test fun noKnownApOrNoSurveyGivesNoEstimate() {
        assertNull(Centroids.estimate(ref, catalogue, listOf(ApRow("de:ad:be:ef:00:01", "other", -40, 2437))))
        assertNull(Centroids.estimate(Reference(emptyList(), emptyMap(), -100.0), catalogue, listOf(ap(1, -44))))
    }
}
