package com.campuspresence.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AggregatorTest {
    private val peer = Advert.Student("aabbccddeeff0011")
    private val marker = Advert.Marker(14, "0011223344556677")

    @Test fun collapsesRepeatedSightingsIntoOneObservation() {
        val a = Aggregator()
        for ((i, r) in listOf(-60, -70, -62, -64, -90).withIndex()) assertTrue(a.add(peer, r, 1000.0 + i))
        val out = a.drain().single()
        assertEquals(Aggregator.Kind.PEER, out.kind)
        assertEquals(-64.0, out.rssi, 0.0)               // median, so the -90 outlier is harmless
        assertEquals(5, out.samples)
        assertEquals(4.0, out.duration, 0.0)
        assertEquals(1004.0, out.timestamp, 0.0)
    }

    @Test fun evenSampleCountAveragesTheMiddlePair() {
        val a = Aggregator()
        a.add(marker, -60, 10.0); a.add(marker, -61, 11.0)
        assertEquals(-60.5, a.drain().single().rssi, 0.0)
    }

    @Test fun drainClearsSoNothingIsSentTwice() {
        val a = Aggregator()
        a.add(marker, -55, 1.0)
        assertEquals(1, a.drain().size)
        assertTrue(a.drain().isEmpty())
    }

    @Test fun markersAreKeyedByIndexAndToken() {
        val a = Aggregator()
        a.add(marker, -55, 1.0)
        a.add(Advert.Marker(15, marker.token), -55, 1.0)
        a.add(Advert.Marker(14, "ffffffffffffffff"), -55, 1.0)
        val out = a.drain()
        assertEquals(3, out.size)
        assertEquals(setOf(14, 15), out.map { it.markerIdx }.toSet())
        assertTrue(out.all { it.kind == Aggregator.Kind.MARKER })
    }

    @Test fun invalidRssiIsDropped() {
        val a = Aggregator()
        assertFalse(a.add(peer, 127, 1.0))               // "unavailable"
        assertFalse(a.add(peer, 5, 1.0))
        assertFalse(a.add(peer, -128, 1.0))
        assertTrue(a.add(peer, -127, 1.0))
        assertTrue(a.add(peer, 0, 2.0))
        assertEquals(2, a.drain().single().samples)
    }

    @Test fun tableIsBounded() {
        val a = Aggregator(maxSeries = 2)
        assertTrue(a.add(Advert.Student("0000000000000001"), -50, 1.0))
        assertTrue(a.add(Advert.Student("0000000000000002"), -50, 1.0))
        assertFalse(a.add(Advert.Student("0000000000000003"), -50, 1.0))
        assertTrue(a.add(Advert.Student("0000000000000001"), -50, 2.0))      // existing series still accepted
    }

    @Test fun recentStudentsCountsDistinctTokensInTheWindowAndIgnoresMarkers() {
        val a = Aggregator()
        a.add(Advert.Student("0000000000000001"), -50, 100.0)
        a.add(Advert.Student("0000000000000001"), -50, 101.0)
        a.add(Advert.Student("0000000000000002"), -50, 90.0)
        a.add(marker, -50, 100.0)
        assertEquals(2, a.recentStudents(105.0, 30.0))
        assertEquals(1, a.recentStudents(125.0, 30.0))
        a.drain()
        assertEquals(1, a.recentStudents(125.0, 30.0))   // a head count survives the upload
    }
}
