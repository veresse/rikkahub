package me.rerere.rikkahub.ui.components.ui

import android.content.Intent
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import me.rerere.rikkahub.BuildConfig
import me.rerere.rikkahub.data.datastore.SettingsStore
import me.rerere.rikkahub.ui.activity.AppUpdateActivity
import me.rerere.rikkahub.utils.*
import org.koin.compose.koinInject

@Composable
fun AppUpdatePrompt() {
    if (BuildConfig.IS_PLAY_STORE) return
    val checker = koinInject<UpdateChecker>()
    val store = koinInject<SettingsStore>()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val context = LocalContext.current
    var update by remember { mutableStateOf<UpdateInfo?>(null) }
    var dismissed by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(lifecycle, checker) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                try {
                    val settings = store.awaitLoaded()
                    if (shouldCheckForUpdate(false, settings.init,
                            settings.displaySetting.updateCheckDisabledUntilEpochMillis, System.currentTimeMillis())) {
                        val info = checker.fetchUpdate()
                        update = info.takeIf { Version(it.version) > Version(BuildConfig.VERSION_NAME) }
                    } else update = null
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) { /* Retry while foregrounded; background checks run independently. */ }
                delay(60 * 60 * 1000L)
            }
        }
    }
    val info = update
    if (info != null && info.version != dismissed) {
        AlertDialog(
            onDismissRequest = { dismissed = info.version },
            title = { Text("发现新版本 ${info.version}") },
            text = { Text("可在应用内下载更新，下载完成后确认安装。聊天记录和设置会保留。") },
            confirmButton = { TextButton(onClick = {
                dismissed = info.version
                context.startActivity(Intent(context, AppUpdateActivity::class.java))
            }) { Text("立即更新") } },
            dismissButton = { TextButton(onClick = { dismissed = info.version }) { Text("稍后") } },
        )
    }
}
