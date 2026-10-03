package com.campuspresence.app.core

/**
 * Survey scans waiting to be uploaded, and the room they were collected in. The backend trains its live Wi-Fi model
 * from these labels, so a scan filed under the wrong room silently corrupts it: the label is fixed when collection
 * starts and a different room cannot be started while scans for another are pending.
 */
class SurveyBuffer {
    private val scans = ArrayList<List<ApRow>>()
    private var lastStamp = Long.MIN_VALUE

    /** The room the pending scans belong to; null before the first survey. */
    @get:Synchronized var room: String? = null
        private set

    val size: Int @Synchronized get() = scans.size

    /** Null when collecting for `roomId` may begin, otherwise the reason it may not. */
    @Synchronized
    fun begin(roomId: String): String? {
        if (scans.isNotEmpty() && room != roomId) {
            return "${scans.size} scans for room $room are still waiting: upload or discard them before surveying room $roomId"
        }
        room = roomId
        return null
    }

    /** `stamp` identifies the system scan (its newest timestamp), so re-reading a cached scan is not a second sample. */
    @Synchronized
    fun add(aps: List<ApRow>, stamp: Long): Boolean {
        if (room == null || stamp == lastStamp || aps.isEmpty()) return false
        lastStamp = stamp
        scans.add(aps)
        return true
    }

    class Pending(val room: String, val scans: List<List<ApRow>>)

    @Synchronized
    fun pending(): Pending? = room?.takeIf { scans.isNotEmpty() }?.let { Pending(it, ArrayList(scans)) }

    /** Drop exactly what was uploaded; anything collected while the upload ran stays pending. */
    @Synchronized
    fun uploaded(p: Pending) {
        scans.removeAll(p.scans.toSet())
    }

    @Synchronized
    fun discard() {
        scans.clear()
    }
}
