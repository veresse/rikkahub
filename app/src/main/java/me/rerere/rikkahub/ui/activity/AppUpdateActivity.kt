package me.rerere.rikkahub.ui.activity

import android.Manifest
import android.app.DownloadManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.core.net.toUri
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import me.rerere.rikkahub.BuildConfig
import me.rerere.rikkahub.ui.theme.RikkahubTheme
import me.rerere.rikkahub.utils.*
import org.koin.android.ext.android.inject

class AppUpdateActivity : ComponentActivity() {
    private val checker by inject<UpdateChecker>()
    private val download by lazy { UpdateDownloadManager(this) }
    private var info by mutableStateOf<UpdateInfo?>(null)
    private var message by mutableStateOf("正在检查更新…")
    private var ready by mutableStateOf(false)
    private var busy by mutableStateOf(false)
    private var awaitingPermission = false
    private var autoInstall = false
    private var pendingDownload = false
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        pendingDownload = true
        if (info != null) startDownload()
    }
    private val installPermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        awaitingPermission = false
        if (packageManager.canRequestPackageInstalls()) install()
        else message = "尚未允许安装，请点击安装重试授权。"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingDownload = savedInstanceState?.getBoolean("pending_download") ?: false
        autoInstall = savedInstanceState?.getBoolean("auto_install") ?: false
        if (BuildConfig.IS_PLAY_STORE) { finish(); return }
        setContent {
            RikkahubTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Column(Modifier.safeDrawingPadding().padding(24.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                        Text("应用更新", style = MaterialTheme.typography.headlineMedium)
                        info?.let { Text("新版本 ${it.version}", style = MaterialTheme.typography.titleLarge) }
                        Text(message)
                        if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                        Button(enabled = !busy && (ready || info != null), onClick = {
                            if (ready) install()
                            else if (Build.VERSION.SDK_INT >= 33 && !NotificationUtil.hasNotificationPermission(this@AppUpdateActivity)) {
                                pendingDownload = true
                                notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
                            } else startDownload()
                        }) { Text(if (ready) "安装更新" else "下载更新") }
                        TextButton(onClick = { finish() }) { Text("稍后处理") }
                    }
                }
            }
        }
        lifecycleScope.launch {
            try {
                info = checker.fetchUpdate().takeIf { Version(it.version) > Version(BuildConfig.VERSION_NAME) }
                if (info == null && download.status()?.state == DownloadManager.STATUS_SUCCESSFUL) {
                    download.clear()
                    ready = false
                }
                message = if (info == null) "当前已是最新版本" else "点击下载，完成后由系统确认覆盖安装。"
                if (pendingDownload && info != null) startDownload()
            } catch (e: CancellationException) { throw e
            } catch (e: Exception) { message = "检查失败：${e.message}。请关闭此页面后重试。" }
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.RESUMED) {
                while (true) {
                    when (val status = download.status()) {
                        null -> Unit
                        else -> when (status.state) {
                            DownloadManager.STATUS_PENDING, DownloadManager.STATUS_RUNNING, DownloadManager.STATUS_PAUSED -> {
                                busy = true
                                message = if (status.state == DownloadManager.STATUS_PAUSED) "下载已暂停，等待网络恢复…" else "正在下载 ${status.percent}%"
                            }
                            DownloadManager.STATUS_SUCCESSFUL -> {
                                busy = false
                                if (!ready && !awaitingPermission) message = "下载完成，点击安装更新。"
                                ready = true
                                if (autoInstall) { autoInstall = false; install() }
                            }
                            DownloadManager.STATUS_FAILED -> {
                                busy = false; ready = false; autoInstall = false
                                message = "下载失败，请检查网络后重试。"
                            }
                        }
                    }
                    delay(1000)
                }
            }
        }
    }

    private fun startDownload() {
        val release = info ?: return
        pendingDownload = false
        val asset = selectUpdateDownload(release.downloads, Build.SUPPORTED_ABIS.toList())
        if (asset == null) { message = "未找到适用于此设备的更新包"; return }
        try {
            download.start(asset, release.version)
            busy = true; autoInstall = true
        } catch (e: Exception) { busy = false; message = "下载失败：${e.message}" }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("pending_download", pendingDownload)
        outState.putBoolean("auto_install", autoInstall)
        super.onSaveInstanceState(outState)
    }

    private fun install() {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { download.validate() }
                if (!packageManager.canRequestPackageInstalls()) {
                    awaitingPermission = true
                    installPermission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, "package:$packageName".toUri()))
                    return@launch
                }
                val uri = FileProvider.getUriForFile(this@AppUpdateActivity, "$packageName.fileprovider", download.file)
                startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
            } catch (e: CancellationException) { throw e
            } catch (e: Exception) {
                download.clear()
                ready = false; autoInstall = false
                message = "无法安装：${e.message}。可重新下载。"
            }
        }
    }
}
