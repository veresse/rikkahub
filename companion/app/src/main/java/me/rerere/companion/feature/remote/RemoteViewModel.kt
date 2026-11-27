package me.rerere.companion.feature.remote

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.CatalogActionDto
import me.rerere.companion.data.api.dto.CatalogLimitsDto
import me.rerere.companion.data.repo.DeviceRepository
import me.rerere.companion.data.ws.AppWsManager
import me.rerere.companion.data.ws.ConnectionState
import me.rerere.companion.data.ws.DeviceStatus
import me.rerere.companion.data.ws.WsEvent

/** 底部结果条的语义：`INFO` 只表示受理/信息，成功与否看 `DONE/FAILED/TIMEOUT`。 */
enum class RemoteResultKind { INFO, DONE, FAILED, TIMEOUT }

data class RemoteResult(val text: String, val kind: RemoteResultKind)

data class RemoteUiState(
    val deviceName: String = "",
    val deviceOnline: Boolean = false,
    val offlineReason: String = "",
    /** 设备状态机（协议 §5.2 的 `state`）：idle/listening/thinking/speaking/error。 */
    val deviceState: String = "",
    val connection: ConnectionState = ConnectionState.Stopped,
    val catalogLoading: Boolean = false,
    val catalogError: String? = null,
    val categories: Map<String, String> = emptyMap(),
    val actions: List<CatalogActionDto> = emptyList(),
    val limits: CatalogLimitsDto = CatalogLimitsDto(),
    val volume: Int = 70,
    /** 静音前的音量，取消静音时恢复。 */
    val mutedBefore: Int = 70,
    val pan: Double = 0.0,
    val tilt: Double = 0.0,
    val idleEnabled: Boolean = true,
    val intensity: Int = 2,
    val speed: Int = 2,
    val repeat: Int = 1,
    /** 正在等待 ack/结果的动作名（只用于“动作”类命令，滑条不打扰用户）。 */
    val sendingAction: String? = null,
    /** 校准对话框的草稿偏移；未保存前 `device.report` 不得覆盖。 */
    val offsetPan: Double = 0.0,
    val offsetTilt: Double = 0.0,
    /** 设备已持久化的偏移；取消校准时用它把云台恢复原位。 */
    val savedOffsetPan: Double = 0.0,
    val savedOffsetTilt: Double = 0.0,
    val result: RemoteResult? = null,
)

/** 结果先于 ack 时暂存终态的上限；正常同 socket 上不会发生，留一点余量即可。 */
private const val EARLY_RESULT_LIMIT = 16

/**
 * 遥控页状态机。
 *
 * 关键语义（协议 §8）：
 * - `remote.ack(ok=true)` 只表示受理；`action.result` 才是执行结果。
 * - `action.play/servo.move/volume.set/idle.set/calibrate.save` 的 ack 带 `action_id`，
 *   必须按 `action_id` 把结果对回请求；同一 `action_id` 只认第一次终态。
 * - `action.stop` / `calibrate.preview` / `get_status` 没有 `action_id`，ack 即终态。
 * - 同一控件的连续命令由服务端覆盖旧命令（`servo.move`/`volume.set`），旧命令会收到
 *   `superseded`；它已被新命令取代，不该当作失败展示。
 * - 滑条类命令静默执行：只有真正失败/超时才占用底部结果条。
 */
class RemoteViewModel(
    private val deviceId: String,
    private val deviceName: String,
    private val repository: DeviceRepository,
    private val ws: AppWsManager,
    private val isOnline: () -> Boolean,
    private val debounceMillis: Long = 200,
    private val ackTimeoutMillis: Long = 5_000,
    /** 收到 ack 后释放“执行中”UI 锁的上限；不结算 action_id，真实结果晚到仍可更新。 */
    private val resultUiTimeoutMillis: Long = 15_000,
    /** 结果彻底判定丢失的上限：服务端排队最长 30s + 动作最长 5s。 */
    private val resultTimeoutMillis: Long = 45_000,
) : ViewModel() {

    private val _state = MutableStateFlow(RemoteUiState(deviceName = deviceName))
    val state: StateFlow<RemoteUiState> = _state.asStateFlow()

    private enum class Kind {
        ACTION,
        VOLUME,
        SERVO,
        IDLE,
        STOP,
        PREVIEW,
        CALIBRATE_SAVE,
        GET_STATUS,
    }

    private class Pending(
        val requestId: String,
        val kind: Kind,
        val label: String,
        /** 是否占用 `sendingAction` 与“已受理”结果条；滑条类命令为 false。 */
        val visible: Boolean,
        /** 可见动作的单调序号：旧动作的超时不得覆盖新动作已经写出的结果条。 */
        val seq: Long = 0,
    ) {
        var timeout: Job? = null
        /** ack 后释放 UI 锁的软超时；真实结果到达时取消。 */
        var uiTimeout: Job? = null
    }

    private val awaitingAck = LinkedHashMap<String, Pending>()
    private val awaitingResult = LinkedHashMap<String, Pending>()

    /** 已结算的 `action_id`：迟到/重复的结果不得再改写结果条。 */
    private val settled = LinkedHashSet<String>()

    /** 用户可见的“执行中”动作：`request_id → 标签`，用于拦截返回键。 */
    private val visibleInFlight = LinkedHashMap<String, String>()

    /** 可见动作的序号发号器；[lastVisibleResultSeq] 记录结果条当前描述的是哪一次动作。 */
    private var visibleSeq = 0L
    private var lastVisibleResultSeq = 0L

    /**
     * `action.result` 先于 `remote.ack` 到达时暂存终态（同一 socket 上正常不会发生，
     * 但断线重连/服务端重排时可能出现）。ack 迟到时用它补显结果条，
     * 否则动作的完成/失败永远不展示。上限 16 条，超出丢最旧的。
     */
    private val earlyResults = LinkedHashMap<String, Pair<String, String>>()

    private var volumeJob: Job? = null
    private var panTiltJob: Job? = null
    private var calibrationJob: Job? = null

    private var volumeDirty = false
    private var panTiltDirty = false
    private var idleDirty = false
    private var calibrationDirty = false
    private var statusRequestPending = false
    /** 首次 REST 快照是否已加载：用于区分“启动时的连接回放”和真正的重连。 */
    private var snapshotLoaded = false
    private var started = false

    fun start() {
        if (started) return
        started = true
        _state.update { it.copy(deviceName = this@RemoteViewModel.deviceName.ifBlank { it.deviceName }) }
        // 先订阅再发命令：`events` 无 replay，晚订阅会丢掉紧跟着 ack/结果。
        viewModelScope.launch { ws.connection.collect(::onConnection) }
        viewModelScope.launch { ws.devices.collect(::onDeviceStatuses) }
        viewModelScope.launch { ws.events.collect(::onEvent) }
        viewModelScope.launch { loadDevice() }
        refreshCatalog()
    }

    // -- 目录 / 初始状态 -------------------------------------------------

    fun refreshCatalog() {
        if (_state.value.catalogLoading) return
        _state.update { it.copy(catalogLoading = true, catalogError = null) }
        viewModelScope.launch {
            when (val result = repository.catalog()) {
                is AppResult.Ok -> _state.update { current ->
                    current.copy(
                        catalogLoading = false,
                        catalogError = null,
                        categories = result.value.categories,
                        actions = result.value.actions,
                        limits = result.value.limits,
                        volume = current.volume.coerceIn(
                            result.value.limits.volume.min,
                            result.value.limits.volume.max,
                        ),
                    )
                }

                is AppResult.Err -> _state.update {
                    // 失败不清空已有目录：已加载的动作仍然可用。
                    it.copy(catalogLoading = false, catalogError = result.error.message)
                }
            }
        }
    }

    private suspend fun loadDevice() {
        // 无论成功失败都算“试过一次”：之后的 Connected 才是真正的重连。
        snapshotLoaded = true
        when (val result = repository.list()) {
            is AppResult.Ok -> {
                val device = result.value.firstOrNull { it.id == deviceId }
                if (device == null) {
                    _state.update {
                        it.copy(deviceOnline = false, offlineReason = "设备不存在或已撤销")
                    }
                    return
                }
                val live = ws.devices.value[deviceId]
                _state.update { current ->
                    val online = live?.online ?: device.online
                    current.copy(
                        deviceName = device.name.ifBlank { current.deviceName },
                        deviceOnline = online,
                        offlineReason = if (online) "" else deviceOfflineReason(live?.reason),
                        deviceState = device.state,
                        volume = if (volumeDirty) current.volume else device.volume.coerceIn(0, 100),
                        idleEnabled = if (idleDirty) current.idleEnabled else device.idleEnabled,
                        offsetPan = if (calibrationDirty) current.offsetPan else device.panOffset,
                        offsetTilt = if (calibrationDirty) current.offsetTilt else device.tiltOffset,
                        savedOffsetPan = device.panOffset,
                        savedOffsetTilt = device.tiltOffset,
                        mutedBefore = device.volume.takeIf { it > 0 } ?: current.mutedBefore,
                    )
                }
                requestStatusIfNeeded()
            }

            is AppResult.Err -> _state.update {
                it.copy(deviceOnline = false, offlineReason = "无法获取设备状态")
            }
        }
    }

    // -- 音量 ------------------------------------------------------------

    fun onVolumeChange(value: Int) {
        val limits = _state.value.limits.volume
        volumeDirty = true
        _state.update { it.copy(volume = value.coerceIn(limits.min, limits.max)) }
        volumeJob?.cancel()
        volumeJob = viewModelScope.launch {
            delay(debounceMillis)
            sendVolume()
        }
    }

    fun commitVolume() {
        volumeJob?.cancel()
        volumeJob = null
        sendVolume()
    }

    fun toggleMute() {
        val current = _state.value
        val limits = current.limits.volume
        if (current.volume == limits.muted) {
            val restored = current.mutedBefore
                .coerceIn(limits.min, limits.max)
                .takeIf { it > 0 } ?: limits.default
            volumeDirty = true
            _state.update { it.copy(volume = restored) }
        } else {
            volumeDirty = true
            _state.update { it.copy(mutedBefore = current.volume, volume = limits.muted) }
        }
        // 静音是明确的一次点击，不走防抖。
        volumeJob?.cancel()
        volumeJob = null
        sendVolume()
    }

    private fun sendVolume() {
        val volume = _state.value.volume
        dispatch(Kind.VOLUME, "音量", visible = false) { id ->
            remoteVolumeSet(id, deviceId, volume)
        }
    }

    // -- 云台角度 --------------------------------------------------------

    fun onPanTiltChange(pan: Double, tilt: Double) {
        val limits = _state.value.limits
        panTiltDirty = true
        _state.update {
            it.copy(
                pan = pan.coerceIn(limits.pan.min, limits.pan.max),
                tilt = tilt.coerceIn(limits.tilt.min, limits.tilt.max),
            )
        }
        panTiltJob?.cancel()
        panTiltJob = viewModelScope.launch {
            delay(debounceMillis)
            sendPanTilt()
        }
    }

    fun commitPanTilt() {
        panTiltJob?.cancel()
        panTiltJob = null
        sendPanTilt()
    }

    private fun sendPanTilt() {
        val current = _state.value
        dispatch(Kind.SERVO, "云台", visible = false) { id ->
            remoteServoMove(id, deviceId, current.pan, current.tilt)
        }
    }

    // -- 动作 ------------------------------------------------------------

    /** 动作参数只影响后续点击，不发命令。 */
    fun setIntensity(value: Int) = _state.update { it.copy(intensity = value.coerceIn(1, 3)) }

    fun setSpeed(value: Int) = _state.update { it.copy(speed = value.coerceIn(1, 3)) }

    fun setRepeat(value: Int) = _state.update { it.copy(repeat = value.coerceIn(1, 5)) }

    fun playAction(name: String) {
        val current = _state.value
        val action = current.actions.firstOrNull { it.name == name }
        val label = action?.labelZh?.takeIf { it.isNotBlank() } ?: name
        dispatch(Kind.ACTION, label, visible = true) { id ->
            remoteActionPlay(
                requestId = id,
                deviceId = deviceId,
                name = name,
                intensity = current.intensity,
                speed = current.speed,
                repeat = current.repeat,
            )
        }
    }

    fun stopAll() {
        // 先取消尚未发出的防抖命令：否则 200ms 后云台/校准预览会在急停之后再动一次。
        cancelPendingDebounce()
        // 服务端会把在途动作结算为 stopped：本地先结清，迟到的 done 不得覆盖“已急停”。
        awaitingResult.keys.forEach { settled.add(it) }
        trimSettled()
        awaitingResult.values.forEach {
            it.timeout?.cancel()
            it.uiTimeout?.cancel()
        }
        awaitingResult.clear()
        // 还没收到 ack 的动作直接作废：连它的超时也取消，否则 5 秒后
        // “动作：未确认”会把“已急停”改掉；迟到的 ack 因查不到 pending 被丢弃。
        awaitingAck.values
            .filter { it.kind == Kind.ACTION || it.kind == Kind.CALIBRATE_SAVE }
            .toList()
            .forEach {
                it.timeout?.cancel()
                awaitingAck.remove(it.requestId)
            }
        visibleInFlight.clear()
        refreshSending()

        val sent = dispatch(Kind.STOP, "急停", visible = false) { id ->
            remoteActionStop(id, deviceId, reason = "user_stop")
        }
        if (sent) {
            // ack 只代表受理：措辞不能宣称机器人已经归位。
            // 同时把“结果条描述的是哪次动作”推进到急停：旧的软/硬超时不得覆盖它。
            lastVisibleResultSeq = ++visibleSeq
            _state.update {
                it.copy(result = RemoteResult("急停已下发，等待设备确认", RemoteResultKind.INFO))
            }
        }
    }

    fun toggleIdle() {
        val next = !_state.value.idleEnabled
        idleDirty = true
        _state.update { it.copy(idleEnabled = next) }
        val sent = dispatch(Kind.IDLE, "待机小动作", visible = false) { id ->
            remoteIdleSet(id, deviceId, next)
        }
        if (!sent) {
            idleDirty = false
            _state.update { it.copy(idleEnabled = !next) }
        }
    }

    // -- 校准 ------------------------------------------------------------

    fun previewCalibration(panOffset: Double, tiltOffset: Double) {
        calibrationDirty = true
        _state.update {
            it.copy(
                offsetPan = panOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT),
                offsetTilt = tiltOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT),
            )
        }
        calibrationJob?.cancel()
        calibrationJob = viewModelScope.launch {
            delay(debounceMillis)
            sendPreview()
        }
    }

    fun commitCalibrationPreview() {
        calibrationJob?.cancel()
        calibrationJob = null
        sendPreview()
    }

    fun saveCalibration(panOffset: Double, tiltOffset: Double) {
        val pan = panOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT)
        val tilt = tiltOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT)
        calibrationDirty = true
        _state.update { it.copy(offsetPan = pan, offsetTilt = tilt) }
        val sent = dispatch(Kind.CALIBRATE_SAVE, "保存校准", visible = true) { id ->
            remoteCalibrateSave(id, deviceId, pan, tilt)
        }
        // 命令根本没发出去：草稿不会生效，别让 dirty 永久冻结后续上报。
        if (!sent) calibrationDirty = false
    }

    private fun sendPreview() {
        val current = _state.value
        dispatch(Kind.PREVIEW, "校准预览", visible = false) { id ->
            remoteCalibratePreview(id, deviceId, current.offsetPan, current.offsetTilt)
        }
    }

    /**
     * 关闭校准对话框但没保存：把草稿恢复成设备已持久化的偏移，
     * 并（在线时）再发一次预览，避免物理云台继续带着未保存的偏移。
     */
    fun cancelCalibration() {
        calibrationJob?.cancel()
        calibrationJob = null
        val current = _state.value
        calibrationDirty = false
        _state.update {
            it.copy(
                offsetPan = current.savedOffsetPan,
                offsetTilt = current.savedOffsetTilt,
            )
        }
        val usable = current.deviceOnline &&
            current.connection == ConnectionState.Connected &&
            isOnline()
        if (usable) {
            dispatch(Kind.PREVIEW, "校准预览", visible = false) { id ->
                remoteCalibratePreview(id, deviceId, current.savedOffsetPan, current.savedOffsetTilt)
            }
        }
    }

    fun dismissResult() {
        _state.update { it.copy(result = null) }
    }

    // -- 连接 / 状态 -----------------------------------------------------

    private fun onConnection(connection: ConnectionState) {
        val previous = _state.value.connection
        _state.update { it.copy(connection = connection) }
        if (connection != ConnectionState.Connected) {
            statusRequestPending = false
            clearPending("连接已断开，命令未确认")
        } else if (previous != ConnectionState.Connected && snapshotLoaded) {
            // 重连后缓存可能已过期：重新拉 REST 快照，再用 get_status 校准运行态。
            // 只看 deviceOnline 会漏掉“断线期间才上线”的设备（旧缓存是离线）。
            // `snapshotLoaded` 用来跳过启动时 StateFlow 的回放，避免首屏重复拉两次。
            viewModelScope.launch { loadDevice() }
        }
    }

    private fun onDeviceStatuses(statuses: Map<String, DeviceStatus>) {
        val live = statuses[deviceId] ?: return
        applyOnline(live.online, live.reason)
    }

    private fun applyOnline(online: Boolean, reason: String) {
        val previous = _state.value.deviceOnline
        _state.update {
            it.copy(
                deviceOnline = online,
                offlineReason = if (online) "" else deviceOfflineReason(reason),
            )
        }
        if (!online) {
            // 设备离线时任何未发出的滑条命令都不该再补发。
            cancelPendingDebounce()
            if (previous) clearPending("设备已离线，命令未确认")
        } else if (online && !previous) {
            // 设备恢复在线：缓存的音量/角度可能已过期，补一次状态查询。
            requestStatusIfNeeded()
        }
    }

    private fun requestStatusIfNeeded() {
        if (statusRequestPending) return
        val current = _state.value
        if (!current.deviceOnline) return
        if (current.connection != ConnectionState.Connected) return
        if (!isOnline()) return
        dispatch(Kind.GET_STATUS, "获取状态", visible = false) { id ->
            remoteGetStatus(id, deviceId)
        }
    }

    private fun onEvent(event: WsEvent) {
        when (event) {
            is WsEvent.DeviceReport -> if (event.deviceId == deviceId) applyReport(event.report)
            is WsEvent.RemoteAck -> onAck(event)
            is WsEvent.ActionResult -> onActionResult(event)
            else -> Unit
        }
    }

    private fun applyReport(report: JsonObject) {
        val volume = report.int("volume")?.coerceIn(0, 100)
        val pan = report.double("pan")
        val tilt = report.double("tilt")
        val idle = report.bool("idle")
        val panOffset = report.double("pan_offset")
        val tiltOffset = report.double("tilt_offset")
        val state = report.string("state")
        // `source=server` 是 get_status 快照，可能晚于 device.status(offline) 到达；
        // 在线态只以 device.status / REST 为准，上报只用来刷新运行指标。
        val provesOnline = report.string("source") != "server"
        _state.update { current ->
            current.copy(
                deviceOnline = if (provesOnline) true else current.deviceOnline,
                offlineReason = if (provesOnline) "" else current.offlineReason,
                deviceState = state ?: current.deviceState,
                // 用户正在拖/刚发过命令时保留草稿，不让旧快照把滑条拽回去。
                volume = if (!volumeDirty && volume != null) volume else current.volume,
                mutedBefore = if (volume != null && volume > 0 && current.volume == 0) {
                    volume
                } else {
                    current.mutedBefore
                },
                pan = if (!panTiltDirty && pan != null) pan else current.pan,
                tilt = if (!panTiltDirty && tilt != null) tilt else current.tilt,
                idleEnabled = if (!idleDirty && idle != null) idle else current.idleEnabled,
                offsetPan = if (!calibrationDirty && panOffset != null) {
                    panOffset
                } else {
                    current.offsetPan
                },
                offsetTilt = if (!calibrationDirty && tiltOffset != null) {
                    tiltOffset
                } else {
                    current.offsetTilt
                },
                // 设备上报的偏移就是已持久化的值（保存后固件会回一次 report）。
                // 校准草稿期间冻结：否则「取消校准」会把未保存的偏移当成已保存值恢复。
                savedOffsetPan = if (!calibrationDirty && panOffset != null) {
                    panOffset
                } else {
                    current.savedOffsetPan
                },
                savedOffsetTilt = if (!calibrationDirty && tiltOffset != null) {
                    tiltOffset
                } else {
                    current.savedOffsetTilt
                },
            )
        }
    }

    private fun onAck(ack: WsEvent.RemoteAck) {
        val pending = awaitingAck.remove(ack.requestId) ?: return
        pending.timeout?.cancel()

        // 无论成功失败都要解除“状态查询在途”：否则一次失败的 get_status
        // 会让本页在重连前不再刷新设备状态。
        if (pending.kind == Kind.GET_STATUS) statusRequestPending = false

        if (!ack.ok) {
            if (pending.visible) {
                visibleInFlight.remove(pending.requestId)
                refreshSending()
            }
            if (ack.error == "device_offline") applyOnline(online = false, reason = "")
            if (pending.kind == Kind.CALIBRATE_SAVE) calibrationDirty = false
            // 旧动作的失败不得覆盖更新动作已写出的结果条；滑条类命令失败仍要可见。
            if (!pending.visible || pending.seq >= lastVisibleResultSeq) {
                _state.update {
                    it.copy(result = RemoteResult(remoteAckError(ack.error), RemoteResultKind.FAILED))
                }
            }
            return
        }

        val actionId = ack.actionId
        if (!actionId.isNullOrEmpty()) {
            // 结果先于 ack 到达：ack 迟到时不能再装一个永远不会结算的 pending；
            // 之前暂存的终态在这里补显，否则动作的完成/失败永远看不到。
            if (actionId in settled) {
                if (pending.visible) {
                    visibleInFlight.remove(pending.requestId)
                    refreshSending()
                }
                val early = earlyResults.remove(actionId)
                if (early != null) {
                    // 与正常终态路径共用副作用：保存成功要解冻草稿，离线要翻面板。
                    applyTerminalSideEffects(pending, early.first, early.second)
                    val mapped = mapResult(pending, early.first, early.second)
                    lastVisibleResultSeq = maxOf(lastVisibleResultSeq, pending.seq)
                    if (pending.visible || mapped.kind == RemoteResultKind.FAILED ||
                        mapped.kind == RemoteResultKind.TIMEOUT
                    ) {
                        _state.update { it.copy(result = mapped) }
                    }
                } else if (pending.kind == Kind.CALIBRATE_SAVE) {
                    // 终态已丢失/已被当作别的来源结算：未知态交给后续上报重同步。
                    calibrationDirty = false
                }
                return
            }
            awaitingResult[actionId] = pending
            if (pending.visible) {
                lastVisibleResultSeq = maxOf(lastVisibleResultSeq, pending.seq)
                _state.update {
                    it.copy(result = RemoteResult("已受理：${pending.label}", RemoteResultKind.INFO))
                }
            }
            // 受理不等于成功：等 action.result。
            // 软超时只放行返回键/结果条，不动 `awaitingResult`：服务端排队最长 30s，
            // 真实结果晚到时仍应把“结果未确认”更新成最终状态。
            pending.uiTimeout = viewModelScope.launch {
                delay(resultUiTimeoutMillis)
                if (awaitingResult[actionId] === pending) onResultSlow(pending)
            }
            // 硬兜底：超过“排队 + 动作”上限仍无结果才按丢失结算，
            // 避免 pending 永久占用内存，也避免迟到结果再改结果条。
            pending.timeout = viewModelScope.launch {
                delay(resultTimeoutMillis)
                if (awaitingResult.remove(actionId) === pending) onResultLost(actionId, pending)
            }
            return
        }

        // 无 action_id：ack 即终态（急停 / 校准预览 / 取状态）。
        if (pending.visible) {
            visibleInFlight.remove(pending.requestId)
            refreshSending()
            lastVisibleResultSeq = maxOf(lastVisibleResultSeq, pending.seq)
            _state.update {
                it.copy(result = RemoteResult("已下发：${pending.label}", RemoteResultKind.INFO))
            }
        }
    }

    private fun onActionResult(result: WsEvent.ActionResult) {
        if (result.actionId.isEmpty()) return
        if (result.deviceId.isNotEmpty() && result.deviceId != deviceId) return

        val pending = awaitingResult.remove(result.actionId)
        if (pending != null) {
            settled.add(result.actionId)
            trimSettled()
            pending.timeout?.cancel()
            pending.uiTimeout?.cancel()
            if (pending.visible) {
                visibleInFlight.remove(pending.requestId)
                refreshSending()
            }
            applyTerminalSideEffects(pending, result.status, result.detail)
            val mapped = mapResult(pending, result.status, result.detail)
            // 滑条类命令只在真正失败/超时时占用结果条。
            val worthShowing = pending.visible ||
                mapped.kind == RemoteResultKind.FAILED ||
                mapped.kind == RemoteResultKind.TIMEOUT
            if (worthShowing) {
                lastVisibleResultSeq = maxOf(lastVisibleResultSeq, pending.seq)
                _state.update { it.copy(result = mapped) }
            }
            return
        }

        if (!settled.add(result.actionId)) return
        trimSettled()
        // 结果先于 ack：先暂存终态，ack 到达时补显（见 onAck）。不认识的 app 结果
        // 若直接展示，就会把另一个手机发起的动作/滑条命令当成本页操作。
        if (result.source == "app") {
            earlyResults[result.actionId] = result.status to result.detail
            if (earlyResults.size > EARLY_RESULT_LIMIT) {
                earlyResults.remove(earlyResults.keys.first())
            }
            return
        }
        // 不是本页发起的动作：LLM 的展示出来，设备自身的（如待机动画）静默。
        if (result.source == "llm") {
            val kind = when (result.status) {
                "failed" -> RemoteResultKind.FAILED
                "timeout" -> RemoteResultKind.TIMEOUT
                else -> RemoteResultKind.INFO
            }
            _state.update {
                it.copy(
                    result = RemoteResult(
                        "机器人动作：${resultText(result.status, result.detail)}",
                        kind,
                    ),
                )
            }
        }
    }

    /**
     * 终态的共享副作用：设备离线要翻面板，校准保存无论成败都要解冻草稿。
     *
     * 失败/超时若继续冻结，`savedOffset*` 会停在旧值：「取消校准」会用旧偏移
     * 把刚写入设备的运行时值盖回去（NVS 里其实已是新值）。
     */
    private fun applyTerminalSideEffects(pending: Pending, status: String, detail: String) {
        if (status != "done" && detail == "device_offline") {
            applyOnline(online = false, reason = "")
        }
        if (pending.kind == Kind.CALIBRATE_SAVE) calibrationDirty = false
    }

    private fun mapResult(pending: Pending, status: String, detail: String): RemoteResult {
        val label = pending.label
        return when (status) {
            "done" -> RemoteResult("$label 完成", RemoteResultKind.DONE)
            "timeout" -> RemoteResult("$label 超时", RemoteResultKind.TIMEOUT)
            else -> when (detail) {
                // 被同类新命令覆盖 / 被急停：这是预期行为，不是失败。
                "superseded" -> RemoteResult("$label 已被新的指令覆盖", RemoteResultKind.INFO)
                "stopped" -> RemoteResult("$label 已停止", RemoteResultKind.INFO)
                "device_offline" -> RemoteResult("$label：设备离线", RemoteResultKind.FAILED)
                else -> RemoteResult("$label：${remoteAckError(detail)}", RemoteResultKind.FAILED)
            }
        }
    }

    private fun resultText(status: String, detail: String): String = when (status) {
        "done" -> "已完成"
        "timeout" -> "超时"
        else -> if (detail.isBlank()) "执行失败" else remoteAckError(detail)
    }

    // -- 发送 ------------------------------------------------------------

    private fun dispatch(
        kind: Kind,
        label: String,
        visible: Boolean,
        build: (requestId: String) -> String,
    ): Boolean {
        if (!canSend()) return false
        val requestId = UUID.randomUUID().toString()
        val pending = Pending(requestId, kind, label, visible, if (visible) ++visibleSeq else 0)
        if (!ws.send(build(requestId))) {
            _state.update {
                it.copy(result = RemoteResult("未送达：连接已断开", RemoteResultKind.FAILED))
            }
            return false
        }
        onCommandSent(kind)
        awaitingAck[requestId] = pending
        if (visible) {
            visibleInFlight[requestId] = label
            // 结果条从现在起描述这次动作：旧动作的软/硬超时不得在等 ack 的窗口里覆盖它。
            lastVisibleResultSeq = maxOf(lastVisibleResultSeq, pending.seq)
            refreshSending()
        }
        if (kind == Kind.GET_STATUS) statusRequestPending = true
        pending.timeout = viewModelScope.launch {
            delay(ackTimeoutMillis)
            if (awaitingAck.remove(requestId) != null) onPendingLost(pending)
        }
        return true
    }

    /** 命令已交给 socket：清掉“用户草稿”标记，后续上报可以恢复跟踪。 */
    private fun onCommandSent(kind: Kind) {
        when (kind) {
            Kind.VOLUME -> volumeDirty = false
            Kind.SERVO -> panTiltDirty = false
            Kind.IDLE -> idleDirty = false
            // 校准草稿要保留到 save 的 action.result(done) 才允许被上报覆盖，
            // 否则“预览 → 上报回填旧偏移”会把用户拖到一半的滑条拽回去。
            else -> Unit
        }
    }

    private fun onPendingLost(pending: Pending) {
        if (pending.kind == Kind.GET_STATUS) statusRequestPending = false
        if (pending.kind == Kind.CALIBRATE_SAVE) calibrationDirty = false
        if (!pending.visible) return
        // 先解锁 UI：即使文案被更新动作占据，也不能让旧动作永久锁住返回键。
        visibleInFlight.remove(pending.requestId)
        refreshSending()
        if (pending.seq < lastVisibleResultSeq) return
        _state.update {
            it.copy(result = RemoteResult("${pending.label}：未确认", RemoteResultKind.FAILED))
        }
    }

    /**
     * 收到 ack 后一段时间仍没有 `action.result`：只释放 UI 锁（返回键/“执行中”），
     * 不把动作当作已结算——服务端排队上限 30s，真实结果晚到时还要能更新结果条。
     */
    private fun onResultSlow(pending: Pending) {
        if (!pending.visible) return
        // 先解锁 UI；旧动作的解锁与文案是两件事，不能一起被 seq 守卫吞掉。
        visibleInFlight.remove(pending.requestId)
        refreshSending()
        // 比它更新的动作已经写过结果条（“已受理/完成/失败”）：旧的软超时不得覆盖。
        if (pending.seq < lastVisibleResultSeq) return
        _state.update {
            it.copy(result = RemoteResult("${pending.label}：结果未确认", RemoteResultKind.TIMEOUT))
        }
    }

    /**
     * 超过“排队 + 动作”硬上限仍没有 `action.result`：按丢失处理。
     *
     * 记入 `settled` 后迟到的结果会被丢弃，避免结果条在“结果未确认”和终态之间反复跳。
     */
    private fun onResultLost(actionId: String, pending: Pending) {
        settled.add(actionId)
        trimSettled()
        if (pending.kind == Kind.CALIBRATE_SAVE) calibrationDirty = false
        if (!pending.visible) return
        // 软超时后用户可能已经发起新动作，新动作的结果条更权威：旧动作只结算、不改文案。
        // 但 UI 锁必须在这里解除，否则旧动作会永久挡住返回键。
        visibleInFlight.remove(pending.requestId)
        refreshSending()
        if (pending.seq < lastVisibleResultSeq) return
        _state.update {
            it.copy(result = RemoteResult("${pending.label}：结果未确认", RemoteResultKind.TIMEOUT))
        }
    }

    private fun canSend(): Boolean {
        if (!isOnline()) {
            fail("当前离线，无法操作")
            return false
        }
        if (_state.value.connection != ConnectionState.Connected) {
            fail("连接已断开，命令未发送")
            return false
        }
        if (!_state.value.deviceOnline) {
            fail(_state.value.offlineReason.ifBlank { "设备已离线" })
            return false
        }
        return true
    }

    private fun fail(text: String) {
        _state.update { it.copy(result = RemoteResult(text, RemoteResultKind.FAILED)) }
    }

    private fun clearPending(reason: String) {
        // 断开/离线时先取消未发出的防抖，否则 200ms 后会用“当前离线”覆盖断线提示，
        // 甚至在链路恢复的瞬间把旧云台命令补发出去。
        cancelPendingDebounce()
        val hadVisible = visibleInFlight.isNotEmpty()
        awaitingAck.values.forEach { it.timeout?.cancel() }
        awaitingResult.values.forEach {
            it.timeout?.cancel()
            it.uiTimeout?.cancel()
        }
        // 结果可能晚到：先记入 settled，避免被当成“机器人动作”再展示一次。
        settled.addAll(awaitingResult.keys)
        trimSettled()
        // 暂存的“结果先于 ack”终态再也等不到 ack：留着只会占内存。
        earlyResults.clear()
        val savingCalibration = awaitingAck.values.any { it.kind == Kind.CALIBRATE_SAVE } ||
            awaitingResult.values.any { it.kind == Kind.CALIBRATE_SAVE }
        awaitingAck.clear()
        awaitingResult.clear()
        visibleInFlight.clear()
        refreshSending()
        // 保存结果未知时继续冻结草稿，会让「取消校准」用旧偏移盖掉可能已写入的新值。
        if (savingCalibration) calibrationDirty = false
        if (hadVisible) fail(reason)
    }

    /** 取消所有尚未发出的防抖命令（急停/断开时调用）。 */
    private fun cancelPendingDebounce() {
        volumeJob?.cancel()
        volumeJob = null
        panTiltJob?.cancel()
        panTiltJob = null
        calibrationJob?.cancel()
        calibrationJob = null
        // 被取消的草稿永远发不出去：若不清脏标记，后续 `device.report`/REST 快照
        // 会被永久忽略，滑条停在“从未下发”的值上（急停归位后尤其明显）。
        volumeDirty = false
        panTiltDirty = false
    }

    private fun refreshSending() {
        val next = visibleInFlight.values.lastOrNull()
        if (_state.value.sendingAction != next) {
            _state.update { it.copy(sendingAction = next) }
        }
    }

    private fun trimSettled() {
        while (settled.size > MAX_SETTLED) {
            val iterator = settled.iterator()
            iterator.next()
            iterator.remove()
        }
    }

    private fun JsonObject.string(key: String): String? =
        (this[key] as? JsonPrimitive)?.contentOrNull

    private fun JsonObject.int(key: String): Int? =
        (this[key] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()

    private fun JsonObject.double(key: String): Double? =
        (this[key] as? JsonPrimitive)?.contentOrNull?.toDoubleOrNull()

    private fun JsonObject.bool(key: String): Boolean? =
        (this[key] as? JsonPrimitive)?.booleanOrNull

    private companion object {
        /** 只需记住最近一段 action_id；超过后最旧的先淘汰。 */
        const val MAX_SETTLED = 128
    }
}
