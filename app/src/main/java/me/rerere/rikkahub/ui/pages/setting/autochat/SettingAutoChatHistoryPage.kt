package me.rerere.rikkahub.ui.pages.setting.autochat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import me.rerere.hugeicons.HugeIcons
import me.rerere.hugeicons.stroke.ArrowRight01
import me.rerere.hugeicons.stroke.Delete02
import me.rerere.hugeicons.stroke.Time02
import me.rerere.hugeicons.stroke.UserCircle
import me.rerere.rikkahub.R
import me.rerere.rikkahub.Screen
import me.rerere.rikkahub.data.datastore.getAssistantById
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import me.rerere.rikkahub.ui.components.nav.BackButton
import me.rerere.rikkahub.ui.components.ui.CardGroup
import me.rerere.rikkahub.ui.components.ui.UIAvatar
import me.rerere.rikkahub.ui.context.LocalNavController
import me.rerere.rikkahub.ui.context.LocalSettings
import me.rerere.rikkahub.utils.plus
import org.koin.androidx.compose.koinViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun SettingAutoChatHistoryPage(
    vm: AutoChatViewModel = koinViewModel()
) {
    val records by vm.recentRecords.collectAsStateWithLifecycle()
    val navController = LocalNavController.current
    val settings = LocalSettings.current
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    var showClearConfirmDialog by remember { mutableStateOf(false) }

    val dateFormat = remember { SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()) }

    Scaffold(
        modifier = Modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(stringResource(R.string.auto_chat_history)) },
                navigationIcon = { BackButton() },
                actions = {
                    if (records.isNotEmpty()) {
                        IconButton(onClick = { showClearConfirmDialog = true }) {
                            Icon(HugeIcons.Delete02, contentDescription = stringResource(R.string.auto_chat_history_clear))
                        }
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        }
    ) { innerPadding ->
        if (records.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        imageVector = HugeIcons.Time02,
                        contentDescription = null,
                        modifier = Modifier.size(48.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
                    )
                    Text(
                        text = stringResource(R.string.auto_chat_history_empty),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        } else {
            LazyColumn(
                contentPadding = innerPadding + PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize()
            ) {
                items(records, key = { it.id.toString() }) { record ->
                    val assistant = remember(record.assistantId, settings) {
                        settings.getAssistantById(record.assistantId)
                    }

                    CardGroup {
                        item(
                            onClick = {
                                navController.navigate(Screen.Chat(record.conversationId.toString()))
                            },
                            leadingContent = {
                                if (assistant != null) {
                                    UIAvatar(name = assistant.name, value = assistant.avatar, modifier = Modifier.size(36.dp))
                                } else {
                                    Icon(HugeIcons.UserCircle, contentDescription = null, modifier = Modifier.size(36.dp))
                                }
                            },
                            headlineContent = {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                                ) {
                                    Text(
                                        text = assistant?.name ?: stringResource(R.string.assistant_page_default_assistant),
                                        style = MaterialTheme.typography.titleMedium
                                    )
                                    StatusBadge(status = record.status)
                                }
                            },
                            supportingContent = {
                                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    val typeText = when (record.occurrenceType) {
                                        AutoChatType.FIXED -> "定时问候"
                                        AutoChatType.RANDOM -> "随机对话"
                                        AutoChatType.MANUAL_TEST -> "立即测试"
                                    }
                                    val planTimeText = dateFormat.format(Date(record.scheduledTime))
                                    Text("类型: $typeText | 计划时间: $planTimeText", style = MaterialTheme.typography.bodySmall)

                                    record.actualTime?.let {
                                        Text("实际执行: ${dateFormat.format(Date(it))}", style = MaterialTheme.typography.bodySmall)
                                    }

                                    record.errorMessage?.let { err ->
                                        Text(
                                            "原因: $err",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.error
                                        )
                                    }

                                    record.contextSummary?.let { summary ->
                                        Text(
                                            summary,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }
                                }
                            },
                            trailingContent = {
                                Icon(HugeIcons.ArrowRight01, contentDescription = null, modifier = Modifier.size(16.dp))
                            }
                        )
                    }
                }
            }
        }
    }

    if (showClearConfirmDialog) {
        AlertDialog(
            onDismissRequest = { showClearConfirmDialog = false },
            title = { Text(stringResource(R.string.auto_chat_history_clear)) },
            text = { Text(stringResource(R.string.auto_chat_history_clear_confirm)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        showClearConfirmDialog = false
                        vm.clearHistory()
                    }
                ) {
                    Text(stringResource(R.string.delete), color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirmDialog = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }
}

@Composable
private fun StatusBadge(status: AutoChatStatus) {
    val (labelRes, containerColor, contentColor) = when (status) {
        AutoChatStatus.SUCCEEDED -> Triple(
            R.string.auto_chat_status_succeeded,
            MaterialTheme.colorScheme.primaryContainer,
            MaterialTheme.colorScheme.onPrimaryContainer
        )
        AutoChatStatus.FAILED -> Triple(
            R.string.auto_chat_status_failed,
            MaterialTheme.colorScheme.errorContainer,
            MaterialTheme.colorScheme.onErrorContainer
        )
        AutoChatStatus.SKIPPED -> Triple(
            R.string.auto_chat_status_skipped,
            MaterialTheme.colorScheme.surfaceVariant,
            MaterialTheme.colorScheme.onSurfaceVariant
        )
        AutoChatStatus.MISSED -> Triple(
            R.string.auto_chat_status_missed,
            MaterialTheme.colorScheme.tertiaryContainer,
            MaterialTheme.colorScheme.onTertiaryContainer
        )
        AutoChatStatus.RUNNING -> Triple(
            R.string.auto_chat_status_running,
            MaterialTheme.colorScheme.secondaryContainer,
            MaterialTheme.colorScheme.onSecondaryContainer
        )
        AutoChatStatus.SCHEDULED -> Triple(
            R.string.auto_chat_status_scheduled,
            MaterialTheme.colorScheme.surfaceContainerHigh,
            MaterialTheme.colorScheme.onSurfaceVariant
        )
        AutoChatStatus.CANCELLED -> Triple(
            R.string.auto_chat_status_cancelled,
            MaterialTheme.colorScheme.surfaceVariant,
            MaterialTheme.colorScheme.outline
        )
        AutoChatStatus.INTERRUPTED -> Triple(
            R.string.auto_chat_status_interrupted,
            MaterialTheme.colorScheme.errorContainer,
            MaterialTheme.colorScheme.onErrorContainer
        )
    }

    Surface(
        color = containerColor,
        contentColor = contentColor,
        shape = MaterialTheme.shapes.extraSmall,
    ) {
        Text(
            text = stringResource(labelRes),
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
        )
    }
}
