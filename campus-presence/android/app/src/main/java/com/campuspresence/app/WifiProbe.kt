package com.campuspresence.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.SystemClock
import com.campuspresence.app.core.ApRow

/** Reads Wi-Fi scan results. Android throttles scans (about 4 per 2 minutes in the foreground), so results can be cached. */
class WifiProbe(context: Context) {
    private val ctx = context.applicationContext
    private val wifi = ctx.getSystemService(Context.WIFI_SERVICE) as WifiManager

    class Scan(val aps: List<ApRow>, val ageS: Double, val newestMicros: Long)

    /** The server's vocabulary: active | off | denied | unsupported. */
    fun state(): String {
        if (ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return "denied"
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val locationOn = if (Build.VERSION.SDK_INT >= 28) lm.isLocationEnabled      // added in API 28; minSdk is 26
        else lm.isProviderEnabled(LocationManager.GPS_PROVIDER) || lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        if (!locationOn) return "denied"                    // without location services Android returns no scan results
        if (!wifi.isWifiEnabled) return "off"
        return "active"
    }

    /** Ask the system for a fresh scan. False means it was throttled (or refused); the previous results stay readable. */
    @Suppress("DEPRECATION")
    fun request(): Boolean = try { wifi.startScan() } catch (e: SecurityException) { false }

    @SuppressLint("MissingPermission")
    @Suppress("DEPRECATION")
    fun latest(): Scan? {
        if (state() != "active") return null
        val results = try { wifi.scanResults } catch (e: SecurityException) { return null }
        if (results.isNullOrEmpty()) return null
        val connected = wifi.connectionInfo?.bssid?.lowercase()
        val newest = results.maxOf { it.timestamp }                       // microseconds since boot
        val ageS = (SystemClock.elapsedRealtimeNanos() / 1000 - newest) / 1e6
        val aps = results.filter { !it.BSSID.isNullOrEmpty() }.map {
            ApRow(it.BSSID.lowercase(), it.SSID ?: "", it.level, it.frequency, connected != null && it.BSSID.lowercase() == connected)
        }
        if (aps.isEmpty()) return null
        return Scan(aps, ageS.coerceAtLeast(0.0), newest)
    }
}
