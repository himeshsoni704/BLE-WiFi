package com.campuspresence.app.core

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicInteger

/** Exercises the HTTP client against a local stub (headers, bodies, error mapping, token refresh). */
class CampusApiTest {
    private lateinit var server: HttpServer
    private val seenAuth = ArrayList<String?>()
    private val seenNodeKey = ArrayList<String?>()
    private var lastBody = ""
    private val logins = AtomicInteger()

    private fun reply(ex: HttpExchange, code: Int, body: String) {
        val b = body.toByteArray()
        ex.responseHeaders.add("Content-Type", "application/json")
        ex.sendResponseHeaders(code, b.size.toLong())
        ex.responseBody.use { it.write(b) }
    }

    @Before fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { ex ->
            seenAuth.add(ex.requestHeaders.getFirst("Authorization"))
            seenNodeKey.add(ex.requestHeaders.getFirst("X-Node-Key"))
            lastBody = ex.requestBody.readBytes().toString(Charsets.UTF_8)
            when (ex.requestURI.path) {
                "/health" -> reply(ex, 200, """{"ok":true,"time":1790000000.5}""")
                "/auth/login" -> {
                    val n = logins.incrementAndGet()
                    if (JSONObject(lastBody).getString("password") == "pw") reply(ex, 200, """{"access_token":"tok$n","role":"student"}""")
                    else reply(ex, 401, """{"detail":"invalid credentials"}""")
                }
                "/auth/provision" -> {
                    if (ex.requestHeaders.getFirst("Authorization") == "Bearer tok2") reply(ex, 200, PROVISION)
                    else reply(ex, 401, """{"detail":"token expired"}""")
                }
                "/ble-observation" -> reply(ex, 422, """{"detail":[{"loc":["body","observations"],"msg":"too short"}]}""")
                "/nodes/provision" -> reply(ex, 200, """{"room":"204","name":"Room 204","marker_idx":14,"marker_secret":"00112233445566778899aabbccddeeff","token_window_s":30}""")
                else -> reply(ex, 404, """{"detail":"Not Found"}""")
            }
        }
        server.start()
    }

    @After fun stop() = server.stop(0)

    private fun url() = "http://127.0.0.1:${server.address.port}/"      // trailing slash must be tolerated

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
