package com.campuspresence.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SurveyBufferTest {
    private fun scan(rssi: Int) = listOf(ApRow("aa:bb:cc:00:00:01", "net", rssi, 2437))

    @Test fun scansAreLabelledWithTheRoomCollectionStartedIn() {
        val b = SurveyBuffer()
        assertNull(b.begin("204"))
        b.add(scan(-50), 1); b.add(scan(-51), 2)
        val p = b.pending()!!
        assertEquals("204", p.room)
        assertEquals(2, p.scans.size)
    }

    @Test fun anotherRoomCannotStartWhileScansArePending() {
        val b = SurveyBuffer()
        b.begin("204"); b.add(scan(-50), 1)
        val refused = b.begin("205")
        assertNotNull(refused)
        assertTrue(refused!!, refused.contains("204") && refused.contains("205"))
        assertEquals("204", b.room)                          // still labelled with the original room
        assertNull(b.begin("204"))                           // resuming the same room is fine
    }

    @Test fun anotherRoomMayStartOnceTheScansAreUploadedOrDiscarded() {
        val b = SurveyBuffer()
        b.begin("204"); b.add(scan(-50), 1)
        b.uploaded(b.pending()!!)
        assertNull(b.begin("205"))
        b.add(scan(-60), 2)
        b.discard()
        assertEquals(0, b.size)
        assertNull(b.begin("206"))
    }

    @Test fun theSameSystemScanIsNotCountedTwice() {
        val b = SurveyBuffer()
        b.begin("204")
        assertTrue(b.add(scan(-50), 100))
        assertFalse(b.add(scan(-50), 100))                   // cached results re-read
        assertTrue(b.add(scan(-50), 101))
        assertEquals(2, b.size)
    }

    @Test fun nothingIsCollectedBeforeARoomIsChosen() {
        val b = SurveyBuffer()
        assertFalse(b.add(scan(-50), 1))
        assertNull(b.pending())
    }

    @Test fun uploadDropsOnlyWhatWasSent() {
        val b = SurveyBuffer()
        b.begin("204"); b.add(scan(-50), 1)
        val sent = b.pending()!!
        b.add(scan(-70), 2)                                  // arrives while the upload is in flight
        b.uploaded(sent)
        assertEquals(1, b.size)
        assertEquals(-70, b.pending()!!.scans.single().single().rssi)
    }
}
