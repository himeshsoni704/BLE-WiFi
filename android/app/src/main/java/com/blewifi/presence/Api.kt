package com.blewifi.presence

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Minimal blocking client for the backend. Call from a background thread. */
class Api(private val settings: Settings) {

    fun post(path: String, body: JSONObject): Pair<Int, String> = send("POST", path, body)

    fun get(path: String): Pair<Int, String> = send("GET", path, null)

    private fun send(method: String, path: String, body: JSONObject?): Pair<Int, String> {
        val conn = URL(settings.serverUrl + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8000
            conn.readTimeout = 15000
            conn.setRequestProperty("Content-Type", "application/json")
            if (settings.apiKey.isNotEmpty()) conn.setRequestProperty("X-API-Key", settings.apiKey)
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            return code to text
        } finally {
            conn.disconnect()
        }
    }
}
