package me.rerere.rikkahub.ui.pages.setting.autochat

import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import me.rerere.hugeicons.HugeIcons
import me.rerere.hugeicons.stroke.Add01
import me.rerere.hugeicons.stroke.Alert01
import me.rerere.hugeicons.stroke.Cancel01
import me.rerere.hugeicons.stroke.Delete02
import me.rerere.hugeicons.stroke.Message01
import me.rerere.hugeicons.stroke.Play
import me.rerere.hugeicons.stroke.Tick01
import me.rerere.hugeicons.stroke.Time02
import me.rerere.hugeicons.stroke.UserCircle
import me.rerere.rikkahub.R
import me.rerere.rikkahub.Screen
import me.rerere.rikkahub.data.autochat.AutoChatTimeEngine
import me.rerere.rikkahub.data.datastore.getAssistantById
import me.rerere.rikkahub.data.model.Assistant
import me.rerere.rikkahub.data.model.AutoChatPromptDefaults
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.Conversation
import me.rerere.rikkahub.ui.components.nav.BackButton
import me.rerere.rikkahub.ui.components.ui.CardGroup
import me.rerere.rikkahub.ui.components.ui.CardGroupIcon
import me.rerere.rikkahub.ui.components.ui.UIAvatar
import me.rerere.rikkahub.ui.context.LocalNavController
import me.rerere.rikkahub.ui.context.LocalSettings
import me.rerere.rikkahub.utils.NotificationUtil
import me.rerere.rikkahub.utils.plus
import org.koin.androidx.compose.koinViewModel
import kotlin.math.roundToInt
import kotlin.uuid.Uuid

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun SettingAutoChatDetailPage(
    ruleIdStr: String,
    vm: AutoChatViewModel = koinViewModel()
) {
    val context = LocalContext.current
    val navController = LocalNavController.current
    val settings = LocalSettings.current
    val rules by vm.rules.collectAsStateWithLifecycle()
    val testState by vm.testState.collectAsStateWithLifecycle()
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val hasNotificationPermission = remember { NotificationUtil.hasNotificationPermission(context) }

    val existingRule = remember(ruleIdStr, rules) {
        runCatching { Uuid.parse(ruleIdStr) }.getOrNull()?.let { targetId ->
            rules.find { it.id == targetId }
        }
    }

    val isNew = existingRule == null
    val isDefaultTemplate = ruleIdStr == "default"

    // 表单状态
    var ruleName by remember(existingRule) {
        mutableStateOf(existingRule?.name ?: if (isDefaultTemplate) "早安与工作日常" else "")
    }
    var selectedAssistantId by remember(existingRule) {
        mutableStateOf(existingRule?.assistantId ?: settings.assistants.firstOrNull()?.id)
    }
    var selectedConversationId by remember(existingRule) {
        mutableStateOf(existingRule?.conversationId)
    }
    var enabled by remember(existingRule) {
        mutableStateOf(existingRule?.enabled ?: false)
    }

    // 固定时间
    val fixedTimes = remember(existingRule) {
        mutableStateListOf<String>().apply {
            addAll(existingRule?.fixedTimes ?: listOf("08:00"))
        }
    }

    // 随机时间
    var randomEnabled by remember(existingRule) {
        mutableStateOf(existingRule?.randomEnabled ?: true)
    }
    val randomDays = remember(existingRule) {
        mutableStateListOf<Int>().apply {
            addAll(existingRule?.randomDaysOfWeek ?: listOf(1, 2, 3, 4, 5))
        }
    }
    var randomWindowStart by remember(existingRule) {
        mutableStateOf(existingRule?.randomWindowStart ?: "09:00")
    }
    var randomWindowEnd by remember(existingRule) {
        mutableStateOf(existingRule?.randomWindowEnd ?: "18:00")
    }
    var randomCount by remember(existingRule) {
        mutableIntStateOf(existingRule?.randomCountPerDay ?: 1)
    }
    var minIntervalMinutes by remember(existingRule) {
        mutableIntStateOf(existingRule?.minIntervalMinutes ?: 90)
    }

    // 安静时段
    var quietHoursEnabled by remember(existingRule) {
        mutableStateOf(existingRule?.quietHoursEnabled ?: true)
    }
    var quietHoursStart by remember(existingRule) {
        mutableStateOf(existingRule?.quietHoursStart ?: "22:00")
    }
    var quietHoursEnd by remember(existingRule) {
        mutableStateOf(existingRule?.quietHoursEnd ?: "07:00")
    }

    // 配额与延迟
    var dailyMax by remember(existingRule) {
        mutableIntStateOf(existingRule?.dailyMaxMessages ?: 2)
    }
    var skipIfUnread by remember(existingRule) {
        mutableStateOf(existingRule?.skipIfUnread ?: false)
    }
    var maxDelayMinutes by remember(existingRule) {
        mutableIntStateOf(existingRule?.maxDelayMinutes ?: 60)
    }

    // 通知
    var enableNotification by remember(existingRule) {
        mutableStateOf(existingRule?.enableNotification ?: true)
    }
    var showPreview by remember(existingRule) {
        mutableStateOf(existingRule?.showPreview ?: false)
    }

    // Prompts
    var fixedPrompt by remember(existingRule) {
        mutableStateOf(existingRule?.fixedPrompt ?: AutoChatPromptDefaults.DEFAULT_FIXED_PROMPT)
    }
    var randomPrompt by remember(existingRule) {
        mutableStateOf(existingRule?.randomPrompt ?: AutoChatPromptDefaults.DEFAULT_RANDOM_PROMPT)
    }

    // 会话列表
    var availableConversations by remember { mutableStateOf<List<Conversation>>(emptyList()) }
    LaunchedEffect(selectedAssistantId) {
        val assistantId = selectedAssistantId
        if (assistantId != null) {
            val convs = vm.getConversationsForAssistant(assistantId)
            availableConversations = convs
            if (convs.none { it.id == selectedConversationId }) {
                selectedConversationId = convs.firstOrNull()?.id
            }
        } else {
            availableConversations = emptyList()
            selectedConversationId = null
        }
    }

    // 对话框状态
    var showAssistantDialog by remember { mutableStateOf(false) }
    var showConversationDialog by remember { mutableStateOf(false) }
    var showDeleteConfirmDialog by remember { mutableStateOf(false) }
    var showAddTimeDialog by remember { mutableStateOf(false) }

    fun buildCurrentRule(): AutoChatRule? {
        val aId = selectedAssistantId ?: return null
        val cId = selectedConversationId ?: return null
        return AutoChatRule(
            id = existingRule?.id ?: Uuid.random(),
            name = ruleName.trim(),
            assistantId = aId,
            conversationId = cId,
            enabled = enabled,
            fixedTimes = fixedTimes.distinct().sorted(),
            randomEnabled = randomEnabled,
            randomDaysOfWeek = randomDays.toSet(),
            randomWindowStart = randomWindowStart.trim(),
            randomWindowEnd = randomWindowEnd.trim(),
            randomCountPerDay = randomCount,
            minIntervalMinutes = minIntervalMinutes,
            quietHoursEnabled = quietHoursEnabled,
            quietHoursStart = quietHoursStart.trim(),
            quietHoursEnd = quietHoursEnd.trim(),
            dailyMaxMessages = dailyMax,
            skipIfUnread = skipIfUnread,
            fixedPrompt = fixedPrompt.trim(),
            randomPrompt = randomPrompt.trim(),
            maxDelayMinutes = maxDelayMinutes,
            enableNotification = enableNotification,
            showPreview = showPreview,
            version = existingRule?.version ?: 1L,
            createdAt = existingRule?.createdAt ?: System.currentTimeMillis(),
        )
    }

    fun validateAndSave() {
        val rule = buildCurrentRule()
        if (rule == null) {
            Toast.makeText(context, R.string.auto_chat_validation_need_selection, Toast.LENGTH_SHORT).show()
            return
        }

        // 1. 固定时间是否在安静时段冲突检查
        val qStart = AutoChatTimeEngine.parseLocalTime(rule.quietHoursStart)
        val qEnd = AutoChatTimeEngine.parseLocalTime(rule.quietHoursEnd)
        for (fixed in rule.fixedTimes) {
            val fTime = AutoChatTimeEngine.parseLocalTime(fixed)
            if (AutoChatTimeEngine.isTimeInQuietHours(fTime, rule.quietHoursEnabled, qStart, qEnd)) {
                val msg = context.getString(
                    R.string.auto_chat_conflict_fixed_quiet,
                    fixed,
                    rule.quietHoursStart,
                    rule.quietHoursEnd
                )
                Toast.makeText(context, msg, Toast.LENGTH_LONG).show()
                return
            }
        }

        // 2. 随机窗口有效时间检查
        if (rule.randomEnabled) {
            val wStart = AutoChatTimeEngine.parseLocalTime(rule.randomWindowStart)
            val wEnd = AutoChatTimeEngine.parseLocalTime(rule.randomWindowEnd)
            val timeEngine = AutoChatTimeEngine()
            val available = timeEngine.calculateAvailableRandomIntervals(
                wStart, wEnd, rule.quietHoursEnabled, qStart, qEnd
            )
            if (available.isEmpty()) {
                Toast.makeText(context, R.string.auto_chat_conflict_random_quiet, Toast.LENGTH_LONG).show()
                return
            }
        }

        // 3. 固定次数是否已超上限
        if (rule.fixedTimes.size > rule.dailyMaxMessages) {
            val msg = context.getString(
                R.string.auto_chat_conflict_daily_limit,
                rule.fixedTimes.size,
                rule.dailyMaxMessages
            )
            Toast.makeText(context, msg, Toast.LENGTH_LONG).show()
            return
        }

        vm.saveRule(rule) {
            navController.popBackStack()
        }
    }

    Scaffold(
        modifier = Modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = {
                    Text(if (isNew) stringResource(R.string.auto_chat_btn_create) else stringResource(R.string.auto_chat_rule_name))
                },
                navigationIcon = { BackButton() },
                actions = {
                    if (!isNew) {
                        IconButton(onClick = { showDeleteConfirmDialog = true }) {
                            Icon(HugeIcons.Delete02, contentDescription = stringResource(R.string.auto_chat_delete))
                        }
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        }
    ) { innerPadding ->
        val currentAssistant = remember(selectedAssistantId, settings) {
            selectedAssistantId?.let { settings.getAssistantById(it) }
        }
        val currentConversation = remember(selectedConversationId, availableConversations) {
            availableConversations.find { it.id == selectedConversationId }
        }

        LazyColumn(
            contentPadding = innerPadding + PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            modifier = Modifier.fillMaxSize()
        ) {
            // 基本绑定设置
            item {
                CardGroup(
                    title = { Text(stringResource(R.string.assistant_detail_basic_desc)) }
                ) {
                    item(
                        onClick = { showAssistantDialog = true },
                        leadingContent = {
                            if (currentAssistant != null) {
                                UIAvatar(name = currentAssistant.name, value = currentAssistant.avatar, modifier = Modifier.size(36.dp))
                            } else {
                                Icon(HugeIcons.UserCircle, contentDescription = null, modifier = Modifier.size(36.dp))
                            }
                        },
                        headlineContent = { Text(stringResource(R.string.auto_chat_select_assistant)) },
                        supportingContent = {
                            Text(currentAssistant?.name ?: stringResource(R.string.auto_chat_select_assistant_hint))
                        }
                    )

                    item(
                        onClick = { showConversationDialog = true },
                        leadingContent = {
                            Icon(HugeIcons.Message01, contentDescription = null, modifier = Modifier.size(36.dp))
                        },
                        headlineContent = { Text(stringResource(R.string.auto_chat_select_conversation)) },
                        supportingContent = {
                            if (availableConversations.isEmpty()) {
                                Text(
                                    stringResource(R.string.auto_chat_no_conversation),
                                    color = MaterialTheme.colorScheme.error
                                )
                            } else {
                                Text(currentConversation?.title ?: stringResource(R.string.auto_chat_select_conversation_hint))
                            }
                        }
                    )

                    formItem(
                        headlineContent = { Text(stringResource(R.string.auto_chat_rule_name)) }
                    ) {
                        OutlinedTextField(
                            value = ruleName,
                            onValueChange = { ruleName = it },
                            placeholder = { Text(stringResource(R.string.auto_chat_rule_name_hint)) },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_enabled)) },
                        supportingContent = { Text(if (enabled) stringResource(R.string.auto_chat_enabled) else stringResource(R.string.auto_chat_disabled)) },
                        trailingContent = {
                            Switch(checked = enabled, onCheckedChange = { enabled = it })
                        }
                    )
                }
            }

            // 固定时间问候
            item {
                CardGroup(
                    title = { Text(stringResource(R.string.auto_chat_fixed_schedule)) }
                ) {
                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_fixed_schedule)) },
                        supportingContent = { Text(stringResource(R.string.auto_chat_fixed_schedule_desc)) }
                    )

                    fixedTimes.forEachIndexed { index, timeStr ->
                        item(
                            headlineContent = { Text(timeStr, style = MaterialTheme.typography.titleMedium) },
                            trailingContent = {
                                if (fixedTimes.size > 1) {
                                    IconButton(onClick = { fixedTimes.removeAt(index) }) {
                                        Icon(HugeIcons.Delete02, contentDescription = null)
                                    }
                                }
                            }
                        )
                    }

                    item(
                        onClick = { showAddTimeDialog = true },
                        leadingContent = { Icon(HugeIcons.Add01, contentDescription = null) },
                        headlineContent = { Text(stringResource(R.string.auto_chat_add_time)) }
                    )
                }
            }

            // 工作时间随机对话
            item {
                CardGroup(
                    title = { Text(stringResource(R.string.auto_chat_random_schedule)) }
                ) {
                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_random_enable)) },
                        supportingContent = { Text(stringResource(R.string.auto_chat_random_schedule_desc)) },
                        trailingContent = {
                            Switch(checked = randomEnabled, onCheckedChange = { randomEnabled = it })
                        }
                    )

                    if (randomEnabled) {
                        formItem(
                            headlineContent = { Text(stringResource(R.string.auto_chat_random_days)) }
                        ) {
                            val days = listOf(
                                1 to stringResource(R.string.auto_chat_day_mon),
                                2 to stringResource(R.string.auto_chat_day_tue),
                                3 to stringResource(R.string.auto_chat_day_wed),
                                4 to stringResource(R.string.auto_chat_day_thu),
                                5 to stringResource(R.string.auto_chat_day_fri),
                                6 to stringResource(R.string.auto_chat_day_sat),
                                7 to stringResource(R.string.auto_chat_day_sun),
                            )
                            FlowRow(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                days.forEach { (dayVal, dayLabel) ->
                                    val selected = dayVal in randomDays
                                    FilterChip(
                                        selected = selected,
                                        onClick = {
                                            if (selected) {
                                                if (randomDays.size > 1) randomDays.remove(dayVal)
                                            } else {
                                                randomDays.add(dayVal)
                                            }
                                        },
                                        label = { Text(dayLabel) }
                                    )
                                }
                            }
                        }

                        formItem(
                            headlineContent = { Text(stringResource(R.string.auto_chat_random_window)) }
                        ) {
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                OutlinedTextField(
                                    value = randomWindowStart,
                                    onValueChange = { randomWindowStart = it },
                                    label = { Text("开始 (HH:mm)") },
                                    singleLine = true,
                                    modifier = Modifier.weight(1f)
                                )
                                OutlinedTextField(
                                    value = randomWindowEnd,
                                    onValueChange = { randomWindowEnd = it },
                                    label = { Text("结束 (HH:mm)") },
                                    singleLine = true,
                                    modifier = Modifier.weight(1f)
                                )
                            }
                        }

                        formItem(
                            headlineContent = { Text("${stringResource(R.string.auto_chat_random_count)}: $randomCount 次") }
                        ) {
                            Slider(
                                value = randomCount.toFloat(),
                                onValueChange = { randomCount = it.roundToInt() },
                                valueRange = 1f..3f,
                                steps = 1,
                                modifier = Modifier.fillMaxWidth()
                            )
                        }

                        formItem(
                            headlineContent = { Text("${stringResource(R.string.auto_chat_min_interval)}: $minIntervalMinutes 分钟") },
                            supportingContent = { Text(stringResource(R.string.auto_chat_min_interval_desc)) }
                        ) {
                            Slider(
                                value = minIntervalMinutes.toFloat(),
                                onValueChange = { minIntervalMinutes = it.roundToInt() },
                                valueRange = 0f..360f,
                                modifier = Modifier.fillMaxWidth()
                            )
                        }
                    }
                }
            }

            // 安静时段与防打扰
            item {
                CardGroup(
                    title = { Text(stringResource(R.string.auto_chat_quiet_hours)) }
                ) {
                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_quiet_hours_enable)) },
                        supportingContent = { Text(stringResource(R.string.auto_chat_quiet_hours_desc)) },
                        trailingContent = {
                            Switch(checked = quietHoursEnabled, onCheckedChange = { quietHoursEnabled = it })
                        }
                    )

                    if (quietHoursEnabled) {
                        formItem(
                            headlineContent = { Text("$quietHoursStart - $quietHoursEnd") }
                        ) {
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                OutlinedTextField(
                                    value = quietHoursStart,
                                    onValueChange = { quietHoursStart = it },
                                    label = { Text(stringResource(R.string.auto_chat_quiet_hours_start)) },
                                    singleLine = true,
                                    modifier = Modifier.weight(1f)
                                )
                                OutlinedTextField(
                                    value = quietHoursEnd,
                                    onValueChange = { quietHoursEnd = it },
                                    label = { Text(stringResource(R.string.auto_chat_quiet_hours_end)) },
                                    singleLine = true,
                                    modifier = Modifier.weight(1f)
                                )
                            }
                        }
                    }

                    formItem(
                        headlineContent = { Text("${stringResource(R.string.auto_chat_daily_max)}: $dailyMax 条") },
                        supportingContent = { Text(stringResource(R.string.auto_chat_daily_max_desc)) }
                    ) {
                        Slider(
                            value = dailyMax.toFloat(),
                            onValueChange = { dailyMax = it.roundToInt() },
                            valueRange = 1f..10f,
                            steps = 8,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_skip_unread)) },
                        supportingContent = { Text(stringResource(R.string.auto_chat_skip_unread_desc)) },
                        trailingContent = {
                            Switch(checked = skipIfUnread, onCheckedChange = { skipIfUnread = it })
                        }
                    )

                    formItem(
                        headlineContent = { Text("${stringResource(R.string.auto_chat_max_delay)}: $maxDelayMinutes 分钟") },
                        supportingContent = { Text(stringResource(R.string.auto_chat_max_delay_desc)) }
                    ) {
                        Slider(
                            value = maxDelayMinutes.toFloat(),
                            onValueChange = { maxDelayMinutes = it.roundToInt() },
                            valueRange = 0f..180f,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                }
            }

            // 通知设置
            item {
                CardGroup(
                    title = { Text(stringResource(R.string.auto_chat_notification)) }
                ) {
                    if (!hasNotificationPermission) {
                        item(
                            headlineContent = {
                                Text(
                                    stringResource(R.string.auto_chat_notification_permission_warn),
                                    color = MaterialTheme.colorScheme.error,
                                    style = MaterialTheme.typography.bodySmall
                                )
                            }
                        )
                    }

                    item(
                        headlineContent = { Text(stringResource(R.string.auto_chat_enable_notification)) },
                        trailingContent = {
                            Switch(checked = enableNotification, onCheckedChange = { enableNotification = it })
                        }
                    )

                    if (enableNotification) {
                        item(
                            headlineContent = { Text(stringResource(R.string.auto_chat_show_preview)) },
                            trailingContent = {
                                Switch(checked = showPreview, onCheckedChange = { showPreview = it })
                            }
                        )
                    }
                }
            }

            // 任务提示词设置
            item {
                CardGroup(
                    title = { Text("任务指令 (Prompt)") }
                ) {
                    formItem(
                        headlineContent = { Text(stringResource(R.string.auto_chat_fixed_prompt)) }
                    ) {
                        OutlinedTextField(
                            value = fixedPrompt,
                            onValueChange = { fixedPrompt = it },
                            minLines = 3,
                            maxLines = 6,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    formItem(
                        headlineContent = { Text(stringResource(R.string.auto_chat_random_prompt)) }
                    ) {
                        OutlinedTextField(
                            value = randomPrompt,
                            onValueChange = { randomPrompt = it },
                            minLines = 3,
                            maxLines = 6,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                }
            }

            // 隐私提示
            item {
                Text(
                    text = stringResource(R.string.auto_chat_privacy_notice),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp)
                )
            }

            // 测试与状态
            item {
                Column(
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    when (val state = testState) {
                        is TestState.Testing -> {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                CircularProgressIndicator(modifier = Modifier.size(20.dp))
                                Text(stringResource(R.string.auto_chat_testing), style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                        is TestState.Success -> {
                            Surface(
                                color = MaterialTheme.colorScheme.primaryContainer,
                                shape = MaterialTheme.shapes.medium,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Text(
                                        stringResource(R.string.auto_chat_test_success),
                                        style = MaterialTheme.typography.labelLarge,
                                        color = MaterialTheme.colorScheme.onPrimaryContainer
                                    )
                                    Spacer(modifier = Modifier.height(4.dp))
                                    Text(
                                        state.text,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onPrimaryContainer
                                    )
                                }
                            }
                        }
                        is TestState.Failed -> {
                            Surface(
                                color = MaterialTheme.colorScheme.errorContainer,
                                shape = MaterialTheme.shapes.medium,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Text(
                                        stringResource(R.string.auto_chat_test_failed, state.error),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onErrorContainer
                                    )
                                }
                            }
                        }
                        TestState.Idle -> {}
                    }

                    Row(
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        OutlinedButton(
                            onClick = {
                                val rule = buildCurrentRule()
                                if (rule == null) {
                                    Toast.makeText(context, R.string.auto_chat_validation_need_selection, Toast.LENGTH_SHORT).show()
                                } else {
                                    vm.testRule(rule)
                                }
                            },
                            enabled = testState !is TestState.Testing,
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(HugeIcons.Play, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(modifier = Modifier.size(6.dp))
                            Text(stringResource(R.string.auto_chat_test_now))
                        }

                        Button(
                            onClick = { validateAndSave() },
                            modifier = Modifier.weight(1f)
                        ) {
                            Text(stringResource(R.string.auto_chat_save))
                        }
                    }
                }
            }
        }
    }

    // 选择角色对话框
    if (showAssistantDialog) {
        AlertDialog(
            onDismissRequest = { showAssistantDialog = false },
            title = { Text(stringResource(R.string.auto_chat_select_assistant)) },
            text = {
                LazyColumn(modifier = Modifier.fillMaxWidth()) {
                    items(settings.assistants.size) { idx ->
                        val a = settings.assistants[idx]
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable {
                                    selectedAssistantId = a.id
                                    showAssistantDialog = false
                                }
                                .padding(vertical = 10.dp)
                        ) {
                            UIAvatar(name = a.name, value = a.avatar, modifier = Modifier.size(36.dp))
                            Text(a.name.ifBlank { "未命名角色" }, style = MaterialTheme.typography.bodyLarge)
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showAssistantDialog = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }

    // 选择会话对话框
    if (showConversationDialog) {
        AlertDialog(
            onDismissRequest = { showConversationDialog = false },
            title = { Text(stringResource(R.string.auto_chat_select_conversation)) },
            text = {
                if (availableConversations.isEmpty()) {
                    Text(stringResource(R.string.auto_chat_no_conversation))
                } else {
                    LazyColumn(modifier = Modifier.fillMaxWidth()) {
                        items(availableConversations.size) { idx ->
                            val c = availableConversations[idx]
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        selectedConversationId = c.id
                                        showConversationDialog = false
                                    }
                                    .padding(vertical = 10.dp)
                            ) {
                                Icon(HugeIcons.Message01, contentDescription = null, modifier = Modifier.size(24.dp))
                                Text(c.title.ifBlank { "会话 #${c.id.toString().take(6)}" }, style = MaterialTheme.typography.bodyLarge)
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showConversationDialog = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }

    // 添加固定时间对话框
    if (showAddTimeDialog) {
        var newTime by remember { mutableStateOf("12:00") }
        AlertDialog(
            onDismissRequest = { showAddTimeDialog = false },
            title = { Text(stringResource(R.string.auto_chat_add_time)) },
            text = {
                OutlinedTextField(
                    value = newTime,
                    onValueChange = { newTime = it },
                    label = { Text("时间 (HH:mm)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val trimmed = newTime.trim()
                        if (trimmed.isNotBlank() && trimmed !in fixedTimes) {
                            fixedTimes.add(trimmed)
                        }
                        showAddTimeDialog = false
                    }
                ) {
                    Text(stringResource(R.string.add))
                }
            },
            dismissButton = {
                TextButton(onClick = { showAddTimeDialog = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }

    // 删除确认对话框
    if (showDeleteConfirmDialog) {
        AlertDialog(
            onDismissRequest = { showDeleteConfirmDialog = false },
            title = { Text(stringResource(R.string.auto_chat_delete)) },
            text = { Text(stringResource(R.string.auto_chat_delete_confirm)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        showDeleteConfirmDialog = false
                        existingRule?.id?.let {
                            vm.deleteRule(it) {
                                navController.popBackStack()
                            }
                        }
                    }
                ) {
                    Text(stringResource(R.string.delete), color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteConfirmDialog = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }
}
