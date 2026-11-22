package me.rerere.rikkahub.utils

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Environment
import android.os.Build
import androidx.core.net.toUri
import me.rerere.rikkahub.BuildConfig
import me.rerere.rikkahub.service.postUpdateNotification
import java.io.File

internal class UpdateDownloadManager(private val context: Context) {
    private val prefs = context.getSharedPreferences("app_update", Context.MODE_PRIVATE)
    private val manager = context.getSystemService(DownloadManager::class.java)
    val id: Long get() = prefs.getLong("download_id", -1)
    val file: File get() = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "app-update.apk")
    data class Status(val state: Int, val percent: Int)

    fun status(): Status? {
        if (id == -1L) return null
        manager.query(DownloadManager.Query().setFilterById(id))?.use { cursor ->
            if (!cursor.moveToFirst()) return null
            val total = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
            val done = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
            return Status(cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)),
                if (total > 0) (done * 100 / total).toInt() else 0)
        }
        return null
    }

    fun start(download: UpdateDownload, version: String) {
        check(!BuildConfig.IS_PLAY_STORE)
        val uri = download.url.toUri()
        require(uri.scheme == "https" && uri.host == "github.com" &&
            uri.path?.startsWith("/veresse/rikkahub/releases/download/") == true) { "更新地址无效" }
        val old = status()
        if (old != null && old.state != DownloadManager.STATUS_FAILED &&
            prefs.getString("download_version", null) == version) return
        if (id != -1L) manager.remove(id)
        file.delete()
        val request = DownloadManager.Request(uri).setTitle("RikkaHub $version")
            .setDescription("正在下载更新，完成后点击安装")
            .setMimeType("application/vnd.android.package-archive")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, "app-update.apk")
        prefs.edit().putLong("download_id", manager.enqueue(request)).putString("download_version", version).commit()
    }

    fun validate() {
        check(status()?.state == DownloadManager.STATUS_SUCCESSFUL && file.isFile) { "更新尚未下载完成" }
        val pm = context.packageManager
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val archive = pm.getPackageArchiveInfo(file.absolutePath, flags)
            ?: error("更新包无法读取，请重新下载")
        val installed = pm.getPackageInfo(context.packageName, flags)
        check(archive.packageName == context.packageName) { "更新包的应用标识不匹配" }
        val newer = if (Build.VERSION.SDK_INT >= 28) archive.longVersionCode > installed.longVersionCode
            else archive.versionCode > installed.versionCode
        check(newer) { "此更新已安装或版本过旧" }
        check(archive.versionName == prefs.getString("download_version", null)) { "更新包版本不匹配" }
        val trustedSignatures = if (Build.VERSION.SDK_INT >= 28) installed.signingInfo?.apkContentsSigners else installed.signatures
        val archiveSignatures = if (Build.VERSION.SDK_INT >= 28) archive.signingInfo?.apkContentsSigners else archive.signatures
        val trusted = trustedSignatures.orEmpty().map { it.toCharsString() }.toSet()
        val signers = archiveSignatures.orEmpty().map { it.toCharsString() }
        check(signers.isNotEmpty() && signers.all { it in trusted }) { "更新签名不匹配，无法覆盖安装" }
    }

    fun clear() {
        if (id != -1L) manager.remove(id)
        prefs.edit().remove("download_id").remove("download_version").commit()
    }
}

class UpdateDownloadReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (BuildConfig.IS_PLAY_STORE || intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
        val download = UpdateDownloadManager(context)
        if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -2) != download.id) return
        if (download.status()?.state == DownloadManager.STATUS_SUCCESSFUL) {
            postUpdateNotification(context, "更新下载完成", "点击确认安装 RikkaHub 更新")
        } else postUpdateNotification(context, "更新下载失败", "点击重新下载更新")
    }
}
