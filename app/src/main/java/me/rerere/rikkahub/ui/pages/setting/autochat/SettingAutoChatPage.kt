package me.rerere.rikkahub.ui.pages.setting.autochat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import me.rerere.hugeicons.HugeIcons
import me.rerere.hugeicons.stroke.Add01
import me.rerere.hugeicons.stroke.Message02
import me.rerere.hugeicons.stroke.Time02
import me.rerere.hugeicons.stroke.UserCircle
import me.rerere.rikkahub.R
import me.rerere.rikkahub.Screen
import me.rerere.rikkahub.data.datastore.getAssistantById
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.ui.components.nav.BackButton
import me.rerere.rikkahub.ui.components.ui.CardGroup
import me.rerere.rikkahub.ui.components.ui.UIAvatar
import me.rerere.rikkahub.ui.context.LocalNavController
import me.rerere.rikkahub.ui.context.LocalSettings
import me.rerere.rikkahub.utils.plus
import org.koin.androidx.compose.koinViewModel

@Composable
fun SettingAutoChatPage(
    vm: AutoChatViewModel = koinViewModel()
) {
    val rules by vm.rules.collectAsStateWithLifecycle()
    val navController = LocalNavController.current
    val settings = LocalSettings.current
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()

    Scaffold(
        modifier = Modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(stringResource(R.string.auto_chat_title)) },
                navigationIcon = { BackButton() },
                actions = {
                    IconButton(onClick = { navController.navigate(Screen.SettingAutoChatHistory) }) {
                        Icon(HugeIcons.Time02, contentDescription = stringResource(R.string.auto_chat_history))
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        },
        floatingActionButton = {
            if (rules.isNotEmpty()) {
                FloatingActionButton(
                    onClick = { navController.navigate(Screen.SettingAutoChatDetail("")) }
                ) {
                    Icon(HugeIcons.Add01, contentDescription = stringResource(R.string.auto_chat_btn_create))
                }
            }
        }
    ) { innerPadding ->
        if (rules.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Icon(
                        imageVector = HugeIcons.Message02,
                        contentDescription = null,
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Text(
                        text = stringResource(R.string.auto_chat_empty_title),
                        style = MaterialTheme.typography.titleLarge,
                    )
                    Text(
                        text = stringResource(R.string.auto_chat_empty_desc),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.padding(top = 8.dp)
                    ) {
                        Button(
                            onClick = { navController.navigate(Screen.SettingAutoChatDetail("default")) }
                        ) {
                            Text(stringResource(R.string.auto_chat_btn_create_default))
                        }
                        OutlinedButton(
                            onClick = { navController.navigate(Screen.SettingAutoChatDetail("")) }
                        ) {
                            Text(stringResource(R.string.auto_chat_btn_create))
                        }
                    }
                }
            }
        } else {
            LazyColumn(
                contentPadding = innerPadding + PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
                modifier = Modifier.fillMaxSize()
            ) {
                item {
                    Text(
                        text = stringResource(R.string.auto_chat_desc),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp)
                    )
                }

                item {
                    CardGroup(
                        title = { Text(stringResource(R.string.auto_chat_rules)) }
                    ) {
                        rules.forEach { rule ->
                            val assistant = settings.getAssistantById(rule.assistantId)
                            item(
                                onClick = {
                                    navController.navigate(Screen.SettingAutoChatDetail(rule.id.toString()))
                                },
                                leadingContent = {
                                    if (assistant != null) {
                                        UIAvatar(
                                            name = assistant.name,
                                            value = assistant.avatar,
                                            modifier = Modifier.size(36.dp)
                                        )
                                    } else {
                                        Icon(HugeIcons.UserCircle, contentDescription = null, modifier = Modifier.size(36.dp))
                                    }
                                },
                                headlineContent = {
                                    Text(
                                        text = rule.name.ifBlank { assistant?.name ?: stringResource(R.string.auto_chat_rule_name) },
                                        style = MaterialTheme.typography.titleMedium,
                                    )
                                },
                                supportingContent = {
                                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                        val fixedSummary = rule.fixedTimes.joinToString(", ")
                                        val summary = buildString {
                                            if (fixedSummary.isNotBlank()) {
                                                append("固定: $fixedSummary")
                                            }
                                            if (rule.randomEnabled) {
                                                if (isNotEmpty()) append(" | ")
                                                append("随机: ${rule.randomWindowStart}-${rule.randomWindowEnd} (${rule.randomCountPerDay}次/天)")
                                            }
                                        }
                                        Text(
                                            text = summary,
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                        Text(
                                            text = if (rule.enabled) stringResource(R.string.auto_chat_enabled) else stringResource(R.string.auto_chat_disabled),
                                            style = MaterialTheme.typography.labelSmall,
                                            color = if (rule.enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline
                                        )
                                    }
                                },
                                trailingContent = {
                                    Switch(
                                        checked = rule.enabled,
                                        onCheckedChange = { vm.toggleRuleEnabled(rule) }
                                    )
                                }
                            )
                        }
                    }
                }
            }
        }
    }
}
