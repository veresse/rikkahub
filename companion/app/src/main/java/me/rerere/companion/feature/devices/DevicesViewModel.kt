package me.rerere.companion.feature.devices

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.api.dto.PairCodeDto
import me.rerere.companion.data.repo.AssistantRepository
import me.rerere.companion.data.repo.ConversationRepository
import me.rerere.companion.data.repo.DeviceRepository
import me.rerere.companion.data.ws.ConnectionState
import me.rerere.companion.data.ws.DeviceStatus

/** 列表里的一行：REST 快照 + `/app/ws` 的实时在线状态（后者优先）。 */
data class DeviceRow(
    val device: DeviceDto,
    val online: Boolean,
    val reason: String = "",
)

data class PairCodeUi(
    val code: String,
    val remainingSeconds: Int,
    val expired: Boolean = false,
)

/** 绑定对话框；选项从角色/会话仓库加载，选择结果由本页持有。 */
data class BindingUi(
    val deviceId: String,
    val deviceName: String,
    val assistantId: String? = null,
    val conversationId: String? = null,
    val assistants: List<AssistantDto> = emptyList(),
    val conversations: List<ConversationDto> = emptyList(),
    val loading: Boolean = false,
)

data class DevicesUiState(
    val devices: List<DeviceRow> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val pairCode: PairCodeUi? = null,
    /** 配对码请求在途：防止双击/重复点「重新生成」打满服务端限频。 */
    val pairCreating: Boolean = false,
    val busyId: String? = null,
    val binding: BindingUi? = null,
    val assistantNames: Map<String, String> = emptyMap(),
    val snackbar: String? = null,
)

sealed interface DevicesEvent {
    data class OpenRemote(val deviceId: String, val deviceName: String) : DevicesEvent
}

/**
 * 设备管理页状态机。
 *
 * - 在线状态 = `/app/ws` 的实时值优先，其次才是 `GET /api/devices` 的 `online`；
 * - 所有写操作（配对码/改名/绑定/撤销）先过网络门控，离线时只提示不发请求；
 * - 配对码倒计时由本页 ticker 驱动，过期后保留弹窗并置 `expired`，方便用户重新生成。
 */
class DevicesViewModel(
    private val repository: DeviceRepository,
    private val assistantRepository: AssistantRepository,
    private val conversationRepository: ConversationRepository,
    private val deviceStatuses: StateFlow<Map<String, DeviceStatus>>,
    private val connection: StateFlow<ConnectionState>,
    private val isOnline: () -> Boolean,
    private val tickMillis: Long = 1_000L,
) : ViewModel() {

    private val _state = MutableStateFlow(DevicesUiState())
    val state: StateFlow<DevicesUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<DevicesEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<DevicesEvent> = _events.asSharedFlow()

    private var pairJob: Job? = null

    /**
     * 本次会话里已撤销的设备 id。
     *
     * 撤销与手动刷新可能并发：DELETE 提交前发出的 GET 仍会返回撤销前快照，
     * 只靠请求代次挡不住，必须按墓碑过滤。
     */
    private val revokedIds = mutableSetOf<String>()

    /** 用户在请求在途时关掉了配对码弹窗：成功后不得把它顶回来。 */
    private var pairDismissed = false

    /** 递增后，旧的列表请求结果不再覆盖新状态（撤销后旧快照不得“复活”设备）。 */
    private var refreshGeneration = 0

    /** 在途的列表请求；强制刷新（重连）会取消它，避免旧请求吞掉新刷新。 */
    private var refreshJob: Job? = null

    init {
        refresh()
        viewModelScope.launch {
            deviceStatuses.collect(::applyStatuses)
        }
        viewModelScope.launch {
            // 断线重连后 `AppWsManager` 会清空实时缓存（服务端建连不补发 device.status）：
            // 重拉一次 REST，避免停留在本页时一直显示断线前的在线态。
            var previous = connection.value
            connection.collect { next ->
                if (next == ConnectionState.Connected && previous != ConnectionState.Connected) {
                    // 强制刷新：断网时发出的旧请求可能挂着，不能让它吞掉重连后的刷新。
                    refresh(force = true)
                }
                previous = next
            }
        }
    }

    fun refresh(force: Boolean = false) {
        // 首次进入时 VM init 与 `AppNav` 的 LaunchedEffect 都会调 refresh()：
        // 空列表 + 加载中说明已经有一次在途，直接复用，避免固定打两次请求。
        // `force=true`（重连）例外：取消在途请求，保证断网时的挂起请求不会吞掉这次刷新。
        if (!force && _state.value.loading && _state.value.devices.isEmpty()) return
        if (force) refreshJob?.cancel()
        val generation = ++refreshGeneration
        _state.update { it.copy(loading = true, error = null) }
        refreshJob = viewModelScope.launch {
            val result = repository.list()
            // 写操作成功后会 refresh()：旧请求晚回来不得把已撤销的设备加回去。
            if (generation != refreshGeneration) return@launch
            when (result) {
                is AppResult.Ok -> _state.update { current ->
                    current.copy(
                        loading = false,
                        error = null,
                        // 服务端保留已撤销设备（is_active=false）用于审计；列表只展示有效设备。
                        // 墓碑挡住“撤销提交前发出的 GET”返回的旧快照。
                        devices = result.value
                            .filter { it.isActive && it.id !in revokedIds }
                            .map(::toRow),
                    )
                }

                is AppResult.Err -> _state.update { current ->
                    current.copy(
                        loading = false,
                        error = if (current.devices.isEmpty()) {
                            result.error.message
                        } else {
                            current.error
                        },
                        snackbar = if (current.devices.isEmpty()) {
                            current.snackbar
                        } else {
                            result.error.message
                        },
                    )
                }
            }
        }
        // 角色名独立加载：失败不能拖慢设备列表，也不能把已绑定的设备显示成“未绑定”。
        viewModelScope.launch {
            val names = loadAssistantNames() ?: return@launch
            _state.update { it.copy(assistantNames = names) }
        }
    }

    fun createPairCode(
        assistantId: String? = null,
        conversationId: String? = null,
    ) {
        if (!requireOnline()) return
        if (_state.value.pairCreating) return
        // 不要在请求发出前取消旧 ticker：请求失败时旧码还在倒计时，
        // 提前取消会让弹窗冻结在一个永不过期的“剩余 0:xx”上。
        pairDismissed = false
        _state.update { it.copy(pairCreating = true) }
        viewModelScope.launch {
            when (val result = repository.createPairCode(assistantId, conversationId)) {
                is AppResult.Ok -> {
                    _state.update { it.copy(pairCreating = false) }
                    // 请求在途时用户已经关掉弹窗：不要把新码顶回来。
                    if (!pairDismissed) startPairCountdown(result.value)
                }

                is AppResult.Err -> _state.update {
                    it.copy(pairCreating = false, snackbar = result.error.message)
                }
            }
        }
    }

    fun dismissPairCode() {
        pairDismissed = true
        pairJob?.cancel()
        pairJob = null
        _state.update { it.copy(pairCode = null) }
    }

    fun rename(deviceId: String, rawName: String) {
        if (!requireOnline()) return
        val name = rawName.trim()
        if (name.isEmpty()) {
            _state.update { it.copy(snackbar = "设备名不能为空") }
            return
        }
        if (rejectWhileBusy()) return
        _state.update { it.copy(busyId = deviceId) }
        viewModelScope.launch {
            when (val result = repository.rename(deviceId, name)) {
                is AppResult.Ok -> {
                    _state.update { it.copy(busyId = null, snackbar = "已重命名") }
                    refresh()
                }

                is AppResult.Err -> _state.update {
                    it.copy(busyId = null, snackbar = result.error.message)
                }
            }
        }
    }

    fun revoke(deviceId: String) {
        if (!requireOnline()) return
        if (rejectWhileBusy()) return
        // 只作废“撤销开始前”发出的列表请求（代次更旧）。撤销开始后才发出的请求
        // 代次更新，不能靠代次丢弃；它们的旧快照由 revokedIds 墓碑过滤（见 refresh）。
        val generationAtRevoke = refreshGeneration
        _state.update { it.copy(busyId = deviceId) }
        viewModelScope.launch {
            when (val result = repository.revoke(deviceId)) {
                is AppResult.Ok -> {
                    // 作废在途的列表请求：它拿到的是撤销前的快照，返回后会“复活”设备。
                    // 被作废的请求不会再清 loading，由这里自己收尾（否则刷新按钮永久置灰）。
                    revokedIds += deviceId
                    val superseded = refreshGeneration == generationAtRevoke
                    if (superseded) refreshGeneration += 1
                    _state.update { current ->
                        current.copy(
                            busyId = null,
                            loading = if (superseded) false else current.loading,
                            devices = current.devices.filterNot { it.device.id == deviceId },
                            snackbar = "设备已撤销，需恢复出厂后重新配对",
                        )
                    }
                }

                is AppResult.Err -> _state.update {
                    it.copy(busyId = null, snackbar = result.error.message)
                }
            }
        }
    }

    fun startBind(deviceId: String) {
        if (!requireOnline()) return
        if (rejectWhileBusy()) return
        val row = _state.value.devices.firstOrNull { it.device.id == deviceId } ?: run {
            _state.update { it.copy(snackbar = "设备已不在列表中，请刷新") }
            return
        }
        _state.update {
            it.copy(
                binding = BindingUi(
                    deviceId = deviceId,
                    deviceName = row.device.name,
                    assistantId = row.device.boundAssistantId,
                    conversationId = row.device.boundConversationId,
                    loading = true,
                ),
            )
        }
        viewModelScope.launch {
            when (val result = assistantRepository.list()) {
                is AppResult.Ok -> _state.update { current ->
                    val binding = current.binding?.takeIf { it.deviceId == deviceId }
                        ?: return@update current
                    current.copy(
                        binding = binding.copy(assistants = result.value, loading = false),
                    )
                }

                is AppResult.Err -> _state.update {
                    it.copy(binding = null, snackbar = result.error.message)
                }
            }
        }
        row.device.boundAssistantId?.let { loadConversations(deviceId, it) }
    }

    fun setBindingAssistant(assistantId: String?) {
        _state.update { current ->
            val binding = current.binding ?: return@update current
            current.copy(
                binding = binding.copy(
                    assistantId = assistantId,
                    conversationId = null,
                    conversations = emptyList(),
                ),
            )
        }
        val binding = _state.value.binding ?: return
        if (assistantId != null) loadConversations(binding.deviceId, assistantId)
    }

    fun setBindingConversation(conversationId: String?) {
        _state.update { current ->
            val binding = current.binding ?: return@update current
            current.copy(binding = binding.copy(conversationId = conversationId))
        }
    }

    fun bind() {
        if (!requireOnline()) return
        val binding = _state.value.binding ?: return
        if (binding.loading) return
        if (rejectWhileBusy()) return
        _state.update { it.copy(busyId = binding.deviceId) }
        viewModelScope.launch {
            when (
                val result = repository.bind(
                    binding.deviceId,
                    binding.assistantId,
                    binding.conversationId,
                )
            ) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(binding = null, busyId = null, snackbar = "已保存绑定")
                    }
                    refresh()
                }

                is AppResult.Err -> _state.update {
                    it.copy(busyId = null, snackbar = result.error.message)
                }
            }
        }
    }

    fun dismissBinding() {
        _state.update { it.copy(binding = null) }
    }

    fun openRemote(deviceId: String) {
        val row = _state.value.devices.firstOrNull { it.device.id == deviceId } ?: return
        // 离线也允许进入遥控页：页面会整页置灰并说明原因，比“点了没反应”清楚。
        _events.tryEmit(DevicesEvent.OpenRemote(deviceId, row.device.name))
    }

    fun dismissSnackbar() {
        _state.update { it.copy(snackbar = null) }
    }

    /** 倒计时按毫秒推进，`tickMillis` 不整除 1000 时也不会漂移。 */
    private fun startPairCountdown(pair: PairCodeDto) {
        pairJob?.cancel()
        val expired = pair.expiresIn <= 0
        _state.update {
            it.copy(
                pairCode = PairCodeUi(
                    code = pair.code,
                    remainingSeconds = pair.expiresIn.coerceAtLeast(0),
                    expired = expired,
                ),
            )
        }
        // TTL 为 0 的码一出现就是过期，不能停在“剩余 0:00”却仍显示可用。
        if (expired) return
        pairJob = viewModelScope.launch {
            var remainingMs = pair.expiresIn * 1_000L
            while (remainingMs > 0) {
                delay(tickMillis)
                remainingMs = (remainingMs - tickMillis).coerceAtLeast(0)
                val seconds = ((remainingMs + 999) / 1_000).toInt()
                _state.update { current ->
                    val ui = current.pairCode ?: return@update current
                    // 已被新配对码替换：旧 ticker 不得改新码的倒计时。
                    if (ui.code != pair.code) return@update current
                    current.copy(
                        pairCode = ui.copy(
                            remainingSeconds = seconds,
                            expired = remainingMs <= 0L,
                        ),
                    )
                }
            }
        }
    }

    private fun loadConversations(deviceId: String, assistantId: String) {
        viewModelScope.launch {
            when (val result = conversationRepository.list(assistantId)) {
                is AppResult.Ok -> _state.update { current ->
                    val binding = current.binding
                        ?.takeIf { it.deviceId == deviceId && it.assistantId == assistantId }
                        ?: return@update current
                    current.copy(
                        binding = binding.copy(conversations = result.value, loading = false),
                    )
                }

                is AppResult.Err -> _state.update {
                    it.copy(snackbar = result.error.message)
                }
            }
        }
    }

    /** 角色名加载失败返回 `null`：保留上一次已知名字，别把绑定状态改成“未绑定”。 */
    private suspend fun loadAssistantNames(): Map<String, String>? {
        val result = assistantRepository.list()
        return if (result is AppResult.Ok) {
            result.value.associate { it.id to it.name }
        } else {
            null
        }
    }

    private fun toRow(device: DeviceDto): DeviceRow {
        val live = deviceStatuses.value[device.id]
        return DeviceRow(
            device = device,
            online = live?.online ?: device.online,
            reason = live?.reason.orEmpty(),
        )
    }

    private fun applyStatuses(statuses: Map<String, DeviceStatus>) {
        _state.update { current ->
            current.copy(
                devices = current.devices.map { row ->
                    // 实时缓存被清空（重连）时保留最后已知值：回退到更旧的 REST 快照
                    // 可能把刚刚通过 device.status 变离线的设备翻回在线。
                    // 真正的刷新由 connection 收集器触发 refresh() 完成。
                    val live = statuses[row.device.id] ?: return@map row
                    row.copy(online = live.online, reason = live.reason)
                },
            )
        }
    }

    /** 写操作串行化；第二次点击要明确告知，而不是静默丢弃。 */
    private fun rejectWhileBusy(): Boolean {
        if (_state.value.busyId == null) return false
        _state.update { it.copy(snackbar = "上一个操作还在进行中") }
        return true
    }

    private fun requireOnline(): Boolean {
        if (isOnline()) return true
        _state.update { it.copy(snackbar = "当前离线，无法操作") }
        return false
    }
}
