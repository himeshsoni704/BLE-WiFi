package com.blewifi.presence

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import kotlin.concurrent.thread

/** One screen: configure the server, then run this phone as a Student or as a Scanner. */
class MainActivity : AppCompatActivity() {

    private lateinit var settings: Settings
    private lateinit var status: TextView
    private lateinit var server: EditText
    private lateinit var apiKey: EditText
    private lateinit var secret: EditText
    private lateinit var scannerId: EditText
    private lateinit var zone: EditText
    private val ui = Handler(Looper.getMainLooper())

    private val refresh = object : Runnable {
        override fun run() {
            status.text = "Student: ${if (StudentService.running) "ON" else "off"} (${StudentService.lastStatus})\n" +
                "Scanner: ${if (ScannerService.running) "ON" else "off"} (${ScannerService.lastStatus}), " +
                "${ScannerService.sightings} sightings"
            ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(40, 40, 40, 40)
        }
        fun label(t: String) = TextView(this).apply { text = t; setPadding(0, 24, 0, 0) }.also { root.addView(it) }
        fun field(hint: String, value: String, password: Boolean = false) = EditText(this).apply {
            this.hint = hint
            setText(value)
            setSingleLine()
            if (password) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }.also { root.addView(it) }
        fun button(t: String, onClick: () -> Unit) = Button(this).apply {
            text = t
            setOnClickListener { onClick() }
        }.also { root.addView(it) }

        label("Server URL (emulator host: http://10.0.2.2:8000)")
        server = field("http://192.168.x.x:8000", settings.serverUrl)
        label("API key (blank if the server has none)")
        apiKey = field("X-API-Key", settings.apiKey, password = true)

        label("STUDENT: secret from POST /students (32 hex chars)")
        secret = field("secret", settings.secret, password = true)
        button("Start student broadcast") { startStudent() }
        button("Stop student") { stopService(StudentService::class.java, StudentService.ACTION_STOP) }

        label("SCANNER: id and zone")
        scannerId = field("scanner id, e.g. room-101", settings.scannerId)
        zone = field("zone, e.g. room-101", "")
        button("Register scanner and start") { startScanner() }
        button("Stop scanner") { stopService(ScannerService::class.java, ScannerService.ACTION_STOP) }

        status = TextView(this).apply { setPadding(0, 32, 0, 0) }
        root.addView(status)

        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onResume() { super.onResume(); ui.post(refresh) }
    override fun onPause() { super.onPause(); ui.removeCallbacks(refresh) }

    private fun save() {
        settings.serverUrl = server.text.toString()
        settings.apiKey = apiKey.text.toString()
        settings.secret = secret.text.toString()
        settings.scannerId = scannerId.text.toString()
    }

    private fun neededPermissions(): List<String> {
        val p = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 31) {
            p += listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE,
                Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= 33) p += Manifest.permission.POST_NOTIFICATIONS
        return p
    }

    private fun ensurePermissions(): Boolean {
        val missing = neededPermissions().filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) return true
        ActivityCompat.requestPermissions(this, missing.toTypedArray(), 1)
        Toast.makeText(this, "Grant permissions, then tap the button again", Toast.LENGTH_LONG).show()
        return false
    }

    private fun startStudent() {
        save()
        if (settings.secret.length != 32) { toast("Secret must be 32 hex chars"); return }
        if (!ensurePermissions()) return
        ContextCompat.startForegroundService(this, Intent(this, StudentService::class.java))
    }

    private fun startScanner() {
        save()
        if (settings.scannerId.isEmpty() || zone.text.isNullOrBlank()) { toast("Scanner id and zone required"); return }
        if (!ensurePermissions()) return
        val z = zone.text.toString().trim()
        thread {
            try {
                val id = java.net.URLEncoder.encode(settings.scannerId, "UTF-8")
                val (code, text) = putScanner(id, z)
                ui.post {
                    if (code in 200..299) {
                        ContextCompat.startForegroundService(this, Intent(this, ScannerService::class.java))
                    } else {
                        toast("Register failed: HTTP $code ${text.take(80)}")
                    }
                }
            } catch (e: Exception) {
                ui.post { toast("Register failed: ${e.message}") }
            }
        }
    }

    /** PUT /scanners/{id} - Api only exposes POST/GET, so do the PUT here. */
    private fun putScanner(encodedId: String, zone: String): Pair<Int, String> {
        val conn = java.net.URL(settings.serverUrl + "/scanners/" + encodedId).openConnection() as java.net.HttpURLConnection
        try {
            conn.requestMethod = "PUT"
            conn.connectTimeout = 8000
            conn.readTimeout = 15000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            if (settings.apiKey.isNotEmpty()) conn.setRequestProperty("X-API-Key", settings.apiKey)
            conn.outputStream.use { it.write(JSONObject().put("zone", zone).toString().toByteArray()) }
            val code = conn.responseCode
            val s = if (code in 200..299) conn.inputStream else conn.errorStream
            return code to (s?.bufferedReader()?.use { it.readText() } ?: "")
        } finally {
            conn.disconnect()
        }
    }

    private fun stopService(cls: Class<*>, action: String) {
        startService(Intent(this, cls).setAction(action))
    }

    private fun toast(m: String) = Toast.makeText(this, m, Toast.LENGTH_LONG).show()
}
