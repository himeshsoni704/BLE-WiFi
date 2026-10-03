package com.campuspresence.app

import com.campuspresence.app.core.ApiException
import java.text.SimpleDateFormat
import java.util.ArrayDeque
import java.util.Date
import java.util.Locale

/** What the services tell the screen. Plain in-process state: the services and the activity share one process. */
object Status {
    @Volatile var student = "idle"
    @Volatile var node = "idle"
    @Volatile var survey = "idle"
    /** Attendance state the server reported in its last reply (e.g. PRESENT, REVIEW_REQUIRED). */
    @Volatile var serverState = "-"

    private val lines = ArrayDeque<String>()

    @Synchronized
    fun log(msg: String) {
        lines.addLast(SimpleDateFormat("HH:mm:ss", Locale.US).format(Date()) + "  " + msg)
        while (lines.size > 80) lines.removeFirst()
    }

    @Synchronized
    fun tail(n: Int = 30): String = lines.toList().takeLast(n).joinToString("\n")

    fun describe(e: Throwable): String = when (e) {
        is ApiException -> "HTTP ${e.code}: ${e.message}"
        else -> e.javaClass.simpleName + (e.message?.let { ": $it" } ?: "")
    }
}
