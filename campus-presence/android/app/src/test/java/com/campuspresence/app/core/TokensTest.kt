package com.campuspresence.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The literal vectors are the ones in campus-presence/backend/tests/test_tokens.py: both sides must agree. */
class TokensTest {
    private val s = "000102030405060708090a0b0c0d0e0f"

    @Test fun studentTokensMatchTheBackend() {
        assertEquals("8bdd878956cddb95", Tokens.studentHex(s, 0))
        assertEquals("16df7955866e124e", Tokens.studentHex(s, 1))
        assertEquals("bc65de6d870ec36a", Tokens.studentHex(s, 59_000_000))
        assertEquals("f73a220ac6e2c585", Tokens.studentHex(s, -1))
    }

    @Test fun markerTokensMatchTheBackend() {
        assertEquals("074eac309307cde8", Hex.encode(Tokens.marker(s, 1, 0)))
        assertEquals("bb99608351456553", Hex.encode(Tokens.marker(s, 204, 59_000_000)))
        assertEquals("2663935f3f961da9", Hex.encode(Tokens.marker(s, 65535, -1)))
    }

    @Test fun markerIndexIsBoundsChecked() {
        for (bad in intArrayOf(0, -1, 65536)) {
            try { Tokens.marker(s, bad, 0); throw AssertionError("accepted $bad") } catch (_: IllegalArgumentException) {}
        }
    }

    @Test fun windowRollsEvery30SecondsAndFloorsNegatives() {
        assertEquals(0L, Tokens.window(0.0))
        assertEquals(0L, Tokens.window(29.999))
        assertEquals(1L, Tokens.window(30.0))
        assertEquals(-1L, Tokens.window(-0.5))
        assertEquals(59_000_000L, Tokens.window(59_000_000.0 * 30 + 12.5))
    }

    @Test fun studentPayloadLayout() {
        val tok = Tokens.student(s, 7)
        val raw = Tokens.studentPayload(tok)
        assertEquals(9, raw.size)
        assertEquals(0x01, raw[0].toInt())
        assertEquals(Advert.Student(Hex.encode(tok)), Tokens.decode(raw))
    }

    @Test fun markerPayloadLayoutIsBigEndian() {
        val tok = Tokens.marker(s, 0x0123, 7)
        val raw = Tokens.markerPayload(0x0123, tok)
        assertEquals(11, raw.size)
        assertEquals(0x02, raw[0].toInt())
        assertEquals(0x01, raw[1].toInt())
        assertEquals(0x23, raw[2].toInt())
        assertEquals(Advert.Marker(0x0123, Hex.encode(tok)), Tokens.decode(raw))
        assertEquals(Advert.Marker(65535, Hex.encode(tok)), Tokens.decode(Tokens.markerPayload(65535, tok)))
    }

    @Test fun foreignManufacturerDataIsIgnored() {
        assertNull(Tokens.decode(byteArrayOf()))
        assertNull(Tokens.decode(byteArrayOf(0x09) + ByteArray(8)))
        assertNull(Tokens.decode(byteArrayOf(0x01, 0x02)))
        assertNull(Tokens.decode(byteArrayOf(0x01) + ByteArray(9)))      // student type, marker-length body
        assertNull(Tokens.decode(byteArrayOf(0x02) + ByteArray(8)))      // marker type, student-length body
    }

    @Test fun payloadsFitInALegacyBleAdvertisement() {
        // flags(3) + manufacturer AD structure (length + type + company id(2) + payload) must be <= 31 bytes
        assertTrue(3 + 2 + 2 + Tokens.markerPayload(1, ByteArray(8)).size <= 31)
    }
}
