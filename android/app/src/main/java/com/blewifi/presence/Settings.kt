package com.blewifi.presence

import android.content.Context

/** App configuration persisted in SharedPreferences. */
class Settings(context: Context) {
    private val p = context.applicationContext.getSharedPreferences("blewifi", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = p.getString("server", "http://10.0.2.2:8000") ?: ""
        set(v) = p.edit().putString("server", v.trim().trimEnd('/')).apply()

    var apiKey: String
        get() = p.getString("apiKey", "") ?: ""
        set(v) = p.edit().putString("apiKey", v.trim()).apply()

    /** 32 hex chars, provisioned once from POST /students. */
    var secret: String
        get() = p.getString("secret", "") ?: ""
        set(v) = p.edit().putString("secret", v.trim().lowercase()).apply()

    var scannerId: String
        get() = p.getString("scannerId", "") ?: ""
        set(v) = p.edit().putString("scannerId", v.trim()).apply()
}
