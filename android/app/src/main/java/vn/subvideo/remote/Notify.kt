package vn.subvideo.remote

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object Notify {
    const val JOBS = "jobs"
    const val FILES = "files"
    private var nextId = 100

    fun channels(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(JOBS, "Việc trên máy tính", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(FILES, "Tải file", NotificationManager.IMPORTANCE_LOW))
    }

    fun allowed(ctx: Context): Boolean = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun job(ctx: Context, title: String, text: String) {
        if (!allowed(ctx)) return
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(ctx, JOBS)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(nextId++, n)
        } catch (e: SecurityException) {
            // người dùng tắt quyền thông báo
        }
    }

    fun progress(ctx: Context, id: Int, title: String, pct: Int, done: Boolean, open: Uri? = null, mime: String = "*/*") {
        if (!allowed(ctx)) return
        val b = NotificationCompat.Builder(ctx, FILES)
            .setSmallIcon(if (done) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_sys_download)
            .setContentTitle(title)
            .setOnlyAlertOnce(true)
        if (done) {
            b.setContentText("Đã tải xong – bấm để mở").setAutoCancel(true)
            if (open != null) {
                val i = Intent(Intent.ACTION_VIEW).setDataAndType(open, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                b.setContentIntent(PendingIntent.getActivity(ctx, id, i, PendingIntent.FLAG_IMMUTABLE))
            }
        } else {
            b.setContentText("$pct %").setProgress(100, pct, pct <= 0).setOngoing(true)
        }
        try {
            NotificationManagerCompat.from(ctx).notify(id, b.build())
        } catch (e: SecurityException) {
        }
    }

    fun newId(): Int = nextId++

    /** Hỏi máy tính mỗi 15 phút (ít nhất Android cho phép) khi app không mở: việc nào xong / lỗi thì báo. */
    fun schedule(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<PollWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("poll", ExistingPeriodicWorkPolicy.KEEP, req)
    }
}

class PollWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        Store.init(applicationContext)
        if (!Store.configured || Store.token.isEmpty()) return Result.success()
        return try {
            val base = Pin.pickBase()
            val since = Store.lastEvent
            val c = Pin.open("$base/api/events?since=$since")
            if (c.responseCode != 200) {
                c.disconnect()
                return Result.success()
            }
            val body = c.inputStream.bufferedReader().use { it.readText() }
            c.disconnect()
            val j = JSONObject(body)
            val evs = j.optJSONArray("events")
            if (since > 0 && evs != null) {
                for (i in 0 until minOf(evs.length(), 10)) {
                    val e = evs.getJSONObject(i)
                    val ok = e.optString("status") == "done"
                    val text = if (ok) e.optString("result_msg").ifEmpty { "Xong" } else e.optString("error").ifEmpty { "Lỗi" }
                    Notify.job(applicationContext, (if (ok) "✓ " else "⚠ ") + e.optString("title"), text.take(300))
                }
            }
            Store.lastEvent = maxOf(since, j.optDouble("now", since))
            Result.success()
        } catch (e: Exception) {
            Result.success()   // mất mạng / máy tính tắt: lần sau hỏi lại
        }
    }
}
