package com.campuspresence.app.core

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * Exercises the HTTP client against a local stub (headers, bodies, error mapping, token refresh). The stub is plain
 * java.net sockets: Android's unit-test classpath has no com.sun.net.httpserver.
 */
class CampusApiTest {
    private class Request(val method: String, val path: String, val headers: Map<String, String>, val body: String)

    private lateinit var server: ServerSocket
    private val seenAuth = ArrayList<String?>()
    private val seenNodeKey = ArrayList<String?>()
    private var lastBody = ""
    private val logins = AtomicInteger()

    private fun readLine(input: InputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val c = input.read()
            if (c < 0) return if (sb.isEmpty()) null else sb.toString()
            if (c == '\n'.code) return sb.toString().trimEnd('\r')
            sb.append(c.toChar())
        }
    }

    private fun serve(c: Socket, handle: (Request) -> Pair<Int, String>) {
        c.use {
            val input = it.getInputStream()
            val first = readLine(input) ?: return
            val (method, target) = first.split(" ").let { p -> p[0] to p[1] }
            val headers = HashMap<String, String>()
            while (true) {
                val line = readLine(input) ?: break
                if (line.isEmpty()) break
                val i = line.indexOf(':')
                headers[line.substring(0, i).trim().lowercase()] = line.substring(i + 1).trim()
            }
            val n = headers["content-length"]?.toInt() ?: 0
            val body = ByteArray(n).also { buf ->
                var off = 0
                while (off < n) { val r = input.read(buf, off, n - off); if (r < 0) break; off += r }
            }.toString(Charsets.UTF_8)
            val (code, text) = handle(Request(method, target.substringBefore('?'), headers, body))
            val payload = text.toByteArray(Charsets.UTF_8)
            val head = "HTTP/1.1 $code X\r\nContent-Type: application/json\r\nContent-Length: ${payload.size}\r\nConnection: close\r\n\r\n"
            it.getOutputStream().apply { write(head.toByteArray()); write(payload); flush() }
        }
    }

    private fun route(r: Request): Pair<Int, String> {
        seenAuth.add(r.headers["authorization"])
        seenNodeKey.add(r.headers["x-node-key"])
        lastBody = r.body
        return when (r.path) {
            "/health" -> 200 to """{"ok":true,"time":1790000000.5}"""
            "/auth/login" -> {
                val n = logins.incrementAndGet()
                if (JSONObject(r.body).getString("password") == "pw") 200 to """{"access_token":"tok$n","role":"student"}"""
                else 401 to """{"detail":"invalid credentials"}"""
            }
            "/auth/provision" ->
                if (r.headers["authorization"] == "Bearer tok2") 200 to PROVISION else 401 to """{"detail":"token expired"}"""
            "/ble-observation" -> 422 to """{"detail":[{"loc":["body","observations"],"msg":"too short"}]}"""
            "/nodes/provision" ->
                200 to """{"room":"204","name":"Room 204","marker_idx":14,"marker_secret":"00112233445566778899aabbccddeeff","token_window_s":30}"""
            else -> 404 to """{"detail":"Not Found"}"""
        }
    }

    @Before fun start() {
        server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) {
            while (!server.isClosed) {
                try { serve(server.accept()) { route(it) } } catch (e: IOException) { /* closed or client went away */ }
            }
        }
    }

    @After fun stop() = server.close()

    private fun url() = "http://127.0.0.1:${server.localPort}/"      // trailing slash must be tolerated

    @Test fun healthNeedsNoAuth() {
        val api = CampusApi(url())
        assertEquals(1790000000.5, api.health().getDouble("time"), 0.0)
        assertNull(seenAuth.last())
    }

    @Test fun loginStoresTheBearerAndFailureCarriesTheServerDetail() {
        val api = CampusApi(url())
        api.login("HIMESH", "pw")
        assertEquals("tok1", api.bearer)
        try { api.login("HIMESH", "bad"); fail() } catch (e: ApiException) {
            assertEquals(401, e.code); assertEquals("invalid credentials", e.message)
        }
    }

    @Test fun sessionLogsInAgainWhenTheTokenIsRejected() {
        val api = CampusApi(url())
        val session = Session(api, "HIMESH", "pw")
        session.login()                                        // tok1, which the stub then treats as expired
        val p = session.authed { api.provision() }             // 401 -> login -> tok2 -> ok
        assertEquals("tok2", api.bearer)
        assertEquals(2, logins.get())
        assertEquals("000102030405060708090a0b0c0d0e0f", p.tokenSecret)
        assertEquals(Room(14, "204", "Room 204"), p.rooms.single())
        assertEquals(mapOf("AA:BB:CC:00:00:01" to "LIVE_01"), p.apCatalogue)   // unregistered AP (null bssid) skipped
    }

    @Test fun otherErrorsAreNotRetriedAndValidationDetailIsReadable() {
        val api = CampusApi(url()).also { it.bearer = "tok2" }
        try { api.bleObservation(JSONObject().put("observations", org.json.JSONArray())); fail() } catch (e: ApiException) {
            assertEquals(422, e.code); assertTrue(e.message!!, e.message!!.contains("too short"))
        }
        assertEquals("""{"observations":[]}""", lastBody)
    }

    @Test fun nodeCallsSendTheNodeKeyNotABearer() {
        val api = CampusApi(url()).also { it.nodeKey = "node-204-demo"; it.bearer = "ignored" }
        val n = api.nodeProvision()
        assertEquals("node-204-demo", seenNodeKey.last())
        assertNull(seenAuth.last())
        assertEquals(14, n.markerIdx)
        assertEquals("204", n.room)
    }

    @Test fun serverDownSurfacesAsAnIOException() {
        val api = CampusApi("http://127.0.0.1:1", timeoutMs = 800)
        try { api.health(); fail() } catch (e: java.io.IOException) { /* expected */ }
    }

    companion object {
        private const val PROVISION = """{"student_key":"abc","display_name":"Himesh","token_secret":"000102030405060708090a0b0c0d0e0f",
          "token_window_s":30,"company_id":65535,
          "classrooms":[{"marker_idx":14,"room":"204","name":"Room 204","marker_id":"ROOM_204_BEACON"}],
          "ap_catalogue":[{"ap_id":"LIVE_01","bssid":"AA:BB:CC:00:00:01","frequency_mhz":2437},{"ap_id":"AP_02","bssid":null,"frequency_mhz":5180}]}"""
    }
}
