package me.rerere.rikkahub.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CancellationException
import me.rerere.rikkahub.BuildConfig
import me.rerere.rikkahub.data.datastore.SettingsStore
import me.rerere.rikkahub.ui.activity.AppUpdateActivity
import me.rerere.rikkahub.utils.NotificationUtil
import me.rerere.rikkahub.utils.UpdateChecker
import me.rerere.rikkahub.utils.Version
import me.rerere.rikkahub.utils.shouldCheckForUpdate
import java.util.concurrent.TimeUnit

class AppUpdateWorker(
    context: Context,
    parameters: WorkerParameters,
    private val checker: UpdateChecker,
    private val settingsStore: SettingsStore,
) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        if (BuildConfig.IS_PLAY_STORE) return Result.success()
        return try {
            val settings = settingsStore.awaitLoaded()
            if (!shouldCheckForUpdate(false, settings.init,
                    settings.displaySetting.updateCheckDisabledUntilEpochMillis, System.currentTimeMillis())) {
                return Result.success()
            }
            val info = checker.fetchUpdate()
            val prefs = applicationContext.getSharedPreferences("app_update", Context.MODE_PRIVATE)
            if (Version(info.version) > Version(BuildConfig.VERSION_NAME) &&
                prefs.getString("notified_version", null) != info.version) {
                if (postUpdateNotification(applicationContext, "发现新版本 ${info.version}", "点击下载并安装更新")) {
                    prefs.edit().putString("notified_version", info.version).apply()
                }
            }
            Result.success()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.retry()
        }
    }

    companion object {
        fun schedule(context: Context) {
            if (BuildConfig.IS_PLAY_STORE) return
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                "app-release-check", ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<AppUpdateWorker>(6, TimeUnit.HOURS)
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .build()
            )
        }
    }
}

internal fun postUpdateNotification(context: Context, title: String, message: String): Boolean {
    if (BuildConfig.IS_PLAY_STORE) return false
    context.getSystemService(NotificationManager::class.java).createNotificationChannel(
        NotificationChannel("app_updates", "应用更新", NotificationManager.IMPORTANCE_DEFAULT)
    )
    return NotificationUtil.notify(context, "app_updates", 8101) {
        this.title = title
        content = message
        autoCancel = true
        contentIntent = PendingIntent.getActivity(context, 8101,
            Intent(context, AppUpdateActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
}
