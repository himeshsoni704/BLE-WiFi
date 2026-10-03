package com.campuspresence.app

import android.content.Context

/** Everything the app remembers. Stored in app-private SharedPreferences (backups are disabled in the manifest). */
class Settings(context: Context) {
    private val p = context.applicationContext.getSharedPreferences("campus", Context.MODE_PRIVATE)

    private fun str(key: String, default: String) = p.getString(key, default) ?: default

    var serverUrl: String
        get() = str("server", "http://10.0.2.2:8000")
        set(v) = p.edit().putString("server", v.trim().trimEnd('/')).apply()

    var username: String
        get() = str("username", "")
        set(v) = p.edit().putString("username", v.trim()).apply()

    var password: String
        get() = str("password", "")
        set(v) = p.edit().putString("password", v).apply()

    var nodeKey: String
        get() = str("nodeKey", "")
        set(v) = p.edit().putString("nodeKey", v.trim()).apply()

    var role: String
        get() = str("role", ROLE_STUDENT)
        set(v) = p.edit().putString("role", v).apply()

    var zone: String
        get() = str("zone", "")
        set(v) = p.edit().putString("zone", v).apply()

    companion object {
        const val ROLE_STUDENT = "student"
        const val ROLE_NODE = "node"
        const val ROLE_SURVEY = "survey"
    }
}
