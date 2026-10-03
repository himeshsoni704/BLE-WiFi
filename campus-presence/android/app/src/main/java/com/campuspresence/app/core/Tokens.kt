package com.campuspresence.app.core

import java.nio.ByteBuffer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object Hex {
    fun encode(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }

    fun decode(hex: String): ByteArray {
        require(hex.length % 2 == 0) { "odd-length hex" }
        return ByteArray(hex.length / 2) { i -> hex.substring(2 * i, 2 * i + 2).toInt(16).toByte() }
    }
}

/** What one BLE advertisement from this system carries. */
sealed class Advert {
    abstract val token: String

    data class Student(override val token: String) : Advert()
    data class Marker(val idx: Int, override val token: String) : Advert()
}

/**
 * Rotating BLE tokens and their wire format. Must match campus-presence/backend/app/tokens.py byte for byte:
 *
 *   student presence : 0x01 | token[8]
 *   classroom marker : 0x02 | marker_idx[2, big-endian] | token[8]
 *   student token    = HMAC-SHA256(secret, "cp-student/v1|" + int64_be(window))[:8]
 *   marker token     = HMAC-SHA256(secret, "cp-marker/v1|" + uint16_be(idx) + int64_be(window))[:8]
 *   window           = floor(unix_seconds / 30)
 *
 * Carried as manufacturer-specific data under company id 0xFFFF ("reserved for testing").
 */
object Tokens {
    const val COMPANY_ID = 0xFFFF
    const val WINDOW_S = 30L
    const val TOKEN_BYTES = 8
    const val TYPE_STUDENT = 0x01
    const val TYPE_MARKER = 0x02
    private val STUDENT_PREFIX = "cp-student/v1|".toByteArray(Charsets.US_ASCII)
    private val MARKER_PREFIX = "cp-marker/v1|".toByteArray(Charsets.US_ASCII)

    fun window(unixSeconds: Double, windowS: Long = WINDOW_S): Long = Math.floor(unixSeconds / windowS).toLong()

    private fun int64(window: Long): ByteArray = ByteBuffer.allocate(8).putLong(window).array()

    private fun hmac(secretHex: String, message: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(Hex.decode(secretHex), "HmacSHA256"))
        return mac.doFinal(message).copyOfRange(0, TOKEN_BYTES)
    }

    fun student(secretHex: String, window: Long): ByteArray = hmac(secretHex, STUDENT_PREFIX + int64(window))

    fun marker(secretHex: String, markerIdx: Int, window: Long): ByteArray {
        require(markerIdx in 1..65535) { "marker index out of range" }
        val idx = byteArrayOf((markerIdx shr 8).toByte(), markerIdx.toByte())
        return hmac(secretHex, MARKER_PREFIX + idx + int64(window))
    }

    fun studentHex(secretHex: String, window: Long): String = Hex.encode(student(secretHex, window))

    fun studentPayload(token: ByteArray): ByteArray {
        require(token.size == TOKEN_BYTES)
        return byteArrayOf(TYPE_STUDENT.toByte()) + token
    }

    fun markerPayload(markerIdx: Int, token: ByteArray): ByteArray {
        require(markerIdx in 1..65535 && token.size == TOKEN_BYTES)
        return byteArrayOf(TYPE_MARKER.toByte(), (markerIdx shr 8).toByte(), markerIdx.toByte()) + token
    }

    /** Parse manufacturer data (the bytes after the 2-byte company id). Null if it isn't ours. */
    fun decode(data: ByteArray): Advert? = when {
        data.size == 1 + TOKEN_BYTES && (data[0].toInt() and 0xFF) == TYPE_STUDENT ->
            Advert.Student(Hex.encode(data.copyOfRange(1, data.size)))
        data.size == 3 + TOKEN_BYTES && (data[0].toInt() and 0xFF) == TYPE_MARKER ->
            Advert.Marker(((data[1].toInt() and 0xFF) shl 8) or (data[2].toInt() and 0xFF),
                Hex.encode(data.copyOfRange(3, data.size)))
        else -> null
    }
}
