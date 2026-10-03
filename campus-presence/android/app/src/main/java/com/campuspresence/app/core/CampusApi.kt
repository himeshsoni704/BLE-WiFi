package com.campuspresence.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI

class ApiException(val code: Int, message: String) : Exception(message)

data class Room(val markerIdx: Int, val room: String, val name: String)

data class Provision(
    val studentKey: String,
    val displayName: String,
    val tokenSecret: String,
    val windowS: Int,
    val rooms: List<Room>,
    /** upper-case BSSID -> ap_id, for the APs the server knows. */
    val apCatalogue: Map<String, String>,
)

data class NodeIdentity(val room: String, val name: String, val markerIdx: Int, val markerSecret: String, val windowS: Int)

data class ClassroomRow(val id: String, val name: String, val hasMarker: Boolean)

/** Blocking client for the Proof-of-Presence API. Call from a worker thread. */
class CampusApi(baseUrl: String, private val timeoutMs: Int = 15_000) {
    val base: String = baseUrl.trim().trimEnd('/')

    @Volatile var bearer: String? = null
    @Volatile var nodeKey: String? = null

    private fun send(method: String, path: String, body: JSONObject?, user: Boolean, node: Boolean): String {
        val conn = URI(base + path).toURL().openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.setRequestProperty("Accept", "application/json")
            if (user) bearer?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
            if (node) nodeKey?.let { conn.setRequestProperty("X-Node-Key", it) }
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code !in 200..299) throw ApiException(code, detail(text))
            return text
        } finally {
            conn.disconnect()
        }
    }

    private fun detail(text: String): String = try {
        JSONObject(text).opt("detail")?.toString()?.take(240) ?: text.take(240)
    } catch (e: Exception) {
        text.take(240)
    }

    /** No auth. Also tells us whether the phone's clock is close enough to the server's (it must be within 2 min). */
    fun health(): JSONObject = JSONObject(send("GET", "/health", null, user = false, node = false))

    fun login(username: String, password: String): JSONObject {
        val r = JSONObject(send("POST", "/auth/login", JSONObject().put("username", username).put("password", password),
            user = false, node = false))
        bearer = r.getString("access_token")
        return r
    }

    fun provision(): Provision {
        val r = JSONObject(send("GET", "/auth/provision", null, user = true, node = false))
        val rooms = r.getJSONArray("classrooms").let { a ->
            List(a.length()) { val c = a.getJSONObject(it); Room(c.getInt("marker_idx"), c.getString("room"), c.getString("name")) }
        }
        val aps = HashMap<String, String>()
        r.getJSONArray("ap_catalogue").let { a ->
            for (i in 0 until a.length()) {
                val ap = a.getJSONObject(i)
                if (!ap.isNull("bssid")) aps[ap.getString("bssid").uppercase()] = ap.getString("ap_id")
            }
        }
        return Provision(r.getString("student_key"), r.getString("display_name"), r.getString("token_secret"),
            r.getInt("token_window_s"), rooms, aps)
    }

    fun classrooms(): List<ClassroomRow> {
        val a = JSONArray(send("GET", "/classrooms", null, user = true, node = false))
        return List(a.length()) { val c = a.getJSONObject(it); ClassroomRow(c.getString("id"), c.getString("name"), !c.isNull("marker")) }
    }

    fun bleObservation(body: JSONObject): JSONObject = JSONObject(send("POST", "/ble-observation", body, true, false))

    fun wifiObservation(body: JSONObject): JSONObject = JSONObject(send("POST", "/wifi-observation", body, true, false))

    fun presence(body: JSONObject): JSONObject = JSONObject(send("POST", "/presence", body, true, false))

    fun wifiReference(): Reference = Centroids.parse(JSONObject(send("GET", "/wifi/reference", null, true, false)))

    /** Staff only. */
    fun wifiSurvey(body: JSONObject): JSONObject = JSONObject(send("POST", "/wifi/survey", body, true, false))

    /** Staff only. */
    fun wifiRetrain(): JSONObject = JSONObject(send("POST", "/wifi/retrain", null, true, false))

    fun nodeProvision(): NodeIdentity {
        val r = JSONObject(send("GET", "/nodes/provision", null, user = false, node = true))
        return NodeIdentity(r.getString("room"), r.getString("name"), r.getInt("marker_idx"), r.getString("marker_secret"),
            r.getInt("token_window_s"))
    }

    fun nodeHeartbeat(body: JSONObject): JSONObject = JSONObject(send("POST", "/nodes/heartbeat", body, user = false, node = true))
}

/** Remembers the credentials so a 12-hour token expiring mid-lesson just logs in again. */
class Session(val api: CampusApi, private val username: String, private val password: String) {
    fun login(): JSONObject = api.login(username, password)

    fun <T> authed(block: () -> T): T {
        if (api.bearer == null) login()
        return try {
            block()
        } catch (e: ApiException) {
            if (e.code != 401) throw e
            login()
            block()
        }
    }
}
