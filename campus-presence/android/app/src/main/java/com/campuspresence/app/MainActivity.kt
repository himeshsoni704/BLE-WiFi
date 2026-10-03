package com.campuspresence.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import com.campuspresence.app.core.CampusApi
import com.campuspresence.app.core.Session
import kotlin.concurrent.thread

/** One screen: point it at the server, pick what this phone is (student, classroom node, or surveyor), start. */
class MainActivity : Activity() {

    private lateinit var settings: Settings
    private lateinit var server: EditText
    private lateinit var username: EditText
    private lateinit var password: EditText
    private lateinit var nodeKey: EditText
    private lateinit var zone: Spinner
    private lateinit var credentials: View
    private lateinit var nodeBox: View
    private lateinit var surveyBox: View
    private lateinit var startStop: Button
    private lateinit var info: TextView
    private lateinit var status: TextView
    private lateinit var surveyor: Surveyor
    private var roomIds: List<String> = emptyList()
    private var pendingAfterPermission: (() -> Unit)? = null
    private val ui = Handler(Looper.getMainLooper())

    private val refresh = object : Runnable {
        override fun run() {
            status.text = "Student: ${Status.student}\nNode: ${Status.node}\nSurvey: ${Status.survey}\n" +
                "Server says this phone is: ${Status.serverState}\n\n${Status.tail()}"
            startStop.text = when (settings.role) {
                Settings.ROLE_STUDENT -> if (StudentService.running) "Stop student" else "Start student"
                Settings.ROLE_NODE -> if (NodeService.running) "Stop classroom node" else "Start classroom node"
                else -> if (surveyor.running) "Stop collecting" else "Start collecting scans"
            }
            ui.postDelayed(this, 1000)
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        surveyor = Surveyor(this) { }

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(16), dp(16), dp(16)) }
        fun text(t: String, bold: Boolean = false) = TextView(this).apply {
            text = t; setPadding(0, dp(10), 0, 0); if (bold) setTypeface(typeface, Typeface.BOLD)
        }
        fun field(hint: String, value: String, secret: Boolean = false) = EditText(this).apply {
            this.hint = hint; setText(value); setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or (if (secret) InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_TEXT_VARIATION_URI)
        }
        fun button(t: String, onClick: () -> Unit) = Button(this).apply { text = t; setOnClickListener { onClick() } }

        root.addView(text("Campus Presence", bold = true).apply { textSize = 20f })
        root.addView(text("Server (the PC's LAN address, e.g. http://192.168.1.20:8000)"))
        server = field("http://192.168.x.x:8000", settings.serverUrl).also { root.addView(it) }
        root.addView(button("Check server") { checkServer() })
        info = text("").also { root.addView(it) }

        root.addView(text("This phone is a…", bold = true))
        val group = RadioGroup(this).apply { orientation = RadioGroup.VERTICAL }
        val roles = listOf(Settings.ROLE_STUDENT to "Student", Settings.ROLE_NODE to "Classroom node (stands in for the smart board)",
            Settings.ROLE_SURVEY to "Wi-Fi surveyor (faculty)")
        for ((id, label) in roles) {
            group.addView(RadioButton(this).apply { text = label; tag = id; setId(View.generateViewId()); isChecked = settings.role == id })
        }
        group.setOnCheckedChangeListener { g, checked ->
            settings.role = g.findViewById<RadioButton>(checked).tag as String
            showRole()
        }
        root.addView(group)

        credentials = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(text("Username and password (student, or faculty for the surveyor)"))
            username = field("HIMESH", settings.username).also { addView(it) }
            password = field("password", settings.password, secret = true).also { addView(it) }
        }.also { root.addView(it) }
        nodeBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(text("Room node key (demo: node-204-demo)"))
            nodeKey = field("node-204-demo", settings.nodeKey).also { addView(it) }
        }.also { root.addView(it) }
        surveyBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(text("Room you are standing in (scans are labelled with the room picked when you press Start)"))
            zone = Spinner(this@MainActivity).also { addView(it) }
            addView(button("Load rooms from server") { loadRooms() })
            addView(button("Upload collected scans") { surveyUpload() })
            addView(button("Discard collected scans") { surveyor.discard() })
            addView(button("Retrain Wi-Fi model") { surveyRetrain() })
        }.also { root.addView(it) }

        startStop = button("Start") { onStartStop() }.also { root.addView(it) }
        status = text("").apply { typeface = Typeface.MONOSPACE; textSize = 12f }.also { root.addView(it) }

        setContentView(ScrollView(this).apply { addView(root) })
        showRole()
    }

    override fun onResume() { super.onResume(); ui.post(refresh) }

    override fun onPause() { super.onPause(); ui.removeCallbacks(refresh) }

    override fun onDestroy() { surveyor.stop(); super.onDestroy() }

    private fun showRole() {
        val r = settings.role
        credentials.visibility = if (r == Settings.ROLE_NODE) View.GONE else View.VISIBLE
        nodeBox.visibility = if (r == Settings.ROLE_NODE) View.VISIBLE else View.GONE
        surveyBox.visibility = if (r == Settings.ROLE_SURVEY) View.VISIBLE else View.GONE
    }

    private fun save() {
        settings.serverUrl = server.text.toString()
        settings.username = username.text.toString()
        settings.password = password.text.toString()
        settings.nodeKey = nodeKey.text.toString()
        roomIds.getOrNull(zone.selectedItemPosition)?.let { settings.zone = it }
    }

    private fun toast(m: String) = Toast.makeText(this, m, Toast.LENGTH_LONG).show()

    private fun onUi(block: () -> Unit) = ui.post(block)

    // ---- permissions ----------------------------------------------------------------------------------------------

    private fun neededPermissions(): List<String> {
        val p = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)          // Wi-Fi scan results need it on every version
        if (Build.VERSION.SDK_INT >= 31) {
            p += listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE, Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= 33) p += Manifest.permission.POST_NOTIFICATIONS
        return p
    }

    /** Runs `then` now if everything is granted, otherwise asks first and runs it once the user has answered. */
    private fun withPermissions(then: () -> Unit) {
        val missing = neededPermissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) { then(); return }
        pendingAfterPermission = then
        requestPermissions(missing.toTypedArray(), 1)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val then = pendingAfterPermission
        pendingAfterPermission = null
        if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) then?.invoke()
        else toast("Bluetooth, location and notification permissions are all needed")
    }

    // ---- actions --------------------------------------------------------------------------------------------------

    private fun checkServer() {
        save()
        info.text = "checking…"
        thread {
            val text = try {
                val h = CampusApi(settings.serverUrl).health()
                val skew = h.getDouble("time") - System.currentTimeMillis() / 1000.0
                val models = h.optJSONObject("models")
                "Server OK. Phone clock is ${"%.0f".format(Math.abs(skew))} s ${if (skew >= 0) "behind" else "ahead of"} the server" +
                    (if (Math.abs(skew) > 60) " - TOO FAR OFF: the server rejects readings more than 120 s from its own clock" else "") +
                    ".\nLive Wi-Fi model: ${if (models?.optBoolean("wifi_live") == true) "trained" else "not trained (survey rooms first)"}"
            } catch (e: Exception) {
                "Cannot reach the server: ${Status.describe(e)}"
            }
            onUi { info.text = text }
        }
    }

    private fun onStartStop() {
        save()
        when (settings.role) {
            Settings.ROLE_STUDENT -> {
                if (StudentService.running) { startService(Intent(this, StudentService::class.java).setAction(StudentService.ACTION_STOP)); return }
                if (settings.username.isEmpty() || settings.password.isEmpty()) { toast("Enter your username and password"); return }
                withPermissions { startForegroundService(Intent(this, StudentService::class.java)) }
            }
            Settings.ROLE_NODE -> {
                if (NodeService.running) { startService(Intent(this, NodeService::class.java).setAction(NodeService.ACTION_STOP)); return }
                if (settings.nodeKey.isEmpty()) { toast("Enter the room's node key"); return }
                withPermissions { startForegroundService(Intent(this, NodeService::class.java)) }
            }
            else -> {
                if (surveyor.running) {
                    surveyor.stop()
                    window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    return
                }
                val room = roomIds.getOrNull(zone.selectedItemPosition)
                if (room == null) { toast("Load rooms and pick the room you are standing in first"); return }
                withPermissions {
                    val refused = surveyor.start(room)
                    if (refused != null) { toast(refused); return@withPermissions }
                    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)       // scans only happen while it is awake
                }
            }
        }
    }

    private fun staffSession(): Pair<CampusApi, Session> {
        val api = CampusApi(settings.serverUrl)
        return api to Session(api, settings.username, settings.password)
    }

    private fun loadRooms() {
        save()
        thread {
            try {
                val (api, s) = staffSession()
                val rows = s.authed { api.classrooms() }
                roomIds = rows.map { it.id }
                val labels = rows.map { it.name }
                onUi {
                    zone.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
                    roomIds.indexOf(settings.zone).takeIf { it >= 0 }?.let { zone.setSelection(it) }
                    toast("${rows.size} rooms loaded")
                }
            } catch (e: Exception) {
                onUi { toast("Could not load rooms: ${Status.describe(e)}") }
            }
        }
    }

    private fun surveyUpload() {
        save()
        thread {
            val msg = try {
                val (api, s) = staffSession()
                surveyor.upload(api, s)
            } catch (e: Exception) {
                "upload failed: ${Status.describe(e)}"
            }
            Status.log("survey: $msg")
            Status.survey = msg
        }
    }

    private fun surveyRetrain() {
        save()
        thread {
            val msg = try {
                val (api, s) = staffSession()
                surveyor.retrain(api, s)
            } catch (e: Exception) {
                "retrain failed: ${Status.describe(e)}"
            }
            Status.log("survey: $msg")
            Status.survey = msg
        }
    }
}
