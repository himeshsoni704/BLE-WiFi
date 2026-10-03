package com.blewifi.presence

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Rotating BLE token. Must match backend/app/tokens.py:
 * hex(HMAC-SHA256(secret_bytes, "ble-wifi/v1|" || int64_be(window))[:8]),
 * window = floor(unix_s / 30).
 */
object Token {
    const val WINDOW_S = 30L
    const val TOKEN_BYTES = 8
    private val PREFIX = "ble-wifi/v1|".toByteArray(Charsets.US_ASCII)

    fun window(unixSeconds: Long): Long = Math.floorDiv(unixSeconds, WINDOW_S)

    fun hexToBytes(hex: String): ByteArray {
        require(hex.length % 2 == 0) { "odd-length hex" }
        return ByteArray(hex.length / 2) { i -> hex.substring(2 * i, 2 * i + 2).toInt(16).toByte() }
    }

    fun bytesToHex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }

    fun forWindowBytes(secretHex: String, window: Long): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(hexToBytes(secretHex), "HmacSHA256"))
        val msg = ByteArray(PREFIX.size + 8)
        System.arraycopy(PREFIX, 0, msg, 0, PREFIX.size)
        for (i in 0 until 8) msg[PREFIX.size + i] = (window ushr (8 * (7 - i))).toByte()
        return mac.doFinal(msg).copyOfRange(0, TOKEN_BYTES)
    }

    fun forWindow(secretHex: String, window: Long): String = bytesToHex(forWindowBytes(secretHex, window))

    fun now(secretHex: String, unixSeconds: Long = System.currentTimeMillis() / 1000): String =
        forWindow(secretHex, window(unixSeconds))
}
