package com.campuspresence.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.pm.ServiceInfo
import android.os.Build

object Foreground {
    /** Promote `service` to a foreground service; the notification is what keeps BLE and Wi-Fi running with the screen off. */
    fun start(service: Service, id: Int, channel: String, title: String, text: String) {
        val nm = service.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(channel, "Campus Presence ($channel)", NotificationManager.IMPORTANCE_LOW))
        val n = Notification.Builder(service, channel)
            .setContentTitle(title).setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= 29) {
            service.startForeground(id, n,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            service.startForeground(id, n)
        }
    }
}
