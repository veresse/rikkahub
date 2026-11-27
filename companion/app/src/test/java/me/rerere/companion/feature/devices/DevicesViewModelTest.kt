package me.rerere.companion.feature.devices

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.api.dto.PairCodeDto
import me.rerere.companion.data.ws.ConnectionState
import me.rerere.companion.data.ws.DeviceStatus
import me.rerere.companion.testutil.FakeAssistantRepository
import me.rerere.companion.testutil.FakeConversationRepository
import me.rerere.companion.testutil.FakeDeviceRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class DevicesViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private fun device(
        id: String = "d1",
        name: String = "客厅机器人",
        online: Boolean = false,
        boundAssistantId: String? = null,
    ) = DeviceDto(
        id = id,
        name = name,
        online = online,
        boundAssistantId = boundAssistantId,
    )

    private fun viewModel(
        repo: FakeDeviceRepository = FakeDeviceRepository(),
        assistants: FakeAssistantRepository = FakeAssistantRepository(),
        conversations: FakeConversationRepository = FakeConversationRepository(),
        statuses: MutableStateFlow<Map<String, DeviceStatus>> = MutableStateFlow(emptyMap()),
        connection: MutableStateFlow<ConnectionState> = MutableStateFlow(ConnectionState.Connected),
        online: Boolean = true,
    ) = DevicesViewModel(
        repository = repo,
        assistantRepository = assistants,
        conversationRepository = conversations,
        deviceStatuses = statuses,
        connection = connection,
        isOnline = { online },
        tickMillis = 1_000,
    )

    @Test
    fun `merges live device status over rest snapshot`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device(online = false) }
        val statuses = MutableStateFlow(mapOf("d1" to DeviceStatus("d1", online = true)))
        val vm = viewModel(repo, statuses = statuses)
        main.scheduler.advanceUntilIdle()

        assertTrue(vm.state.value.devices.single().online)

        statuses.value = mapOf("d1" to DeviceStatus("d1", online = false, reason = "device_revoked"))
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.devices.single().online)
        assertEquals("device_revoked", vm.state.value.devices.single().reason)
    }

    @Test
    fun `rest online is kept when no live status was seen`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device(online = true) }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        assertTrue(vm.state.value.devices.single().online)
    }

    @Test
    fun `pair code counts down and expires`() = runTest(main.dispatcher) {
        val repo = FakeDeviceRepository().apply {
            pairCode = PairCodeDto(code = "654321", expiresIn = 2)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        main.scheduler.runCurrent()
        assertEquals("654321", vm.state.value.pairCode?.code)
        assertEquals(2, vm.state.value.pairCode?.remainingSeconds)

        main.scheduler.advanceTimeBy(1_001)
        main.scheduler.runCurrent()
        assertEquals(1, vm.state.value.pairCode?.remainingSeconds)
        assertEquals(false, vm.state.value.pairCode?.expired)

        main.scheduler.advanceTimeBy(1_001)
        main.scheduler.runCurrent()
        assertTrue(vm.state.value.pairCode?.expired == true)
    }

    @Test
    fun `creating a new pair code replaces the old countdown`() = runTest(main.dispatcher) {
        val repo = FakeDeviceRepository().apply {
            pairCode = PairCodeDto(code = "111111", expiresIn = 5)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        main.scheduler.runCurrent()
        main.scheduler.advanceTimeBy(2_001)
        main.scheduler.runCurrent()
        assertEquals(3, vm.state.value.pairCode?.remainingSeconds)

        repo.pairCode = PairCodeDto(code = "222222", expiresIn = 5)
        vm.createPairCode()
        main.scheduler.runCurrent()
        // 旧 ticker 必须被取消，新码从满秒开始。
        assertEquals("222222", vm.state.value.pairCode?.code)
        assertEquals(5, vm.state.value.pairCode?.remainingSeconds)
    }

    @Test
    fun `rename trims and rejects blank`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.rename("d1", "   ")
        main.scheduler.advanceUntilIdle()
        assertTrue(repo.renames.isEmpty())
        assertEquals("设备名不能为空", vm.state.value.snackbar)

        vm.rename("d1", "  书房机器人  ")
        main.scheduler.advanceUntilIdle()
        assertEquals(listOf("d1" to "书房机器人"), repo.renames)
    }

    @Test
    fun `revoke removes the device row`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.revoke("d1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("d1"), repo.revoked)
        assertTrue(vm.state.value.devices.isEmpty())
    }

    @Test
    fun `bind passes selected assistant and conversation`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val assistants = FakeAssistantRepository().apply {
            items = mutableListOf(AssistantDto(id = "a1", name = "小助手"))
        }
        val conversations = FakeConversationRepository().apply {
            items = mutableListOf(
                ConversationDto(id = "c1", assistantId = "a1", title = "聊天"),
            )
        }
        val vm = viewModel(repo, assistants, conversations)
        main.scheduler.advanceUntilIdle()

        vm.startBind("d1")
        main.scheduler.advanceUntilIdle()
        assertEquals(1, vm.state.value.binding?.assistants?.size)

        vm.setBindingAssistant("a1")
        main.scheduler.advanceUntilIdle()
        assertEquals(1, vm.state.value.binding?.conversations?.size)

        vm.setBindingConversation("c1")
        vm.bind()
        main.scheduler.advanceUntilIdle()

        assertEquals(Triple("d1", "a1", "c1"), repo.binds.single())
        assertNull(vm.state.value.binding)
    }

    @Test
    fun `write actions are refused while offline`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo, online = false)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        vm.rename("d1", "书房")
        vm.revoke("d1")
        vm.startBind("d1")
        main.scheduler.advanceUntilIdle()

        assertTrue(repo.pairRequests.isEmpty())
        assertTrue(repo.renames.isEmpty())
        assertTrue(repo.revoked.isEmpty())
        assertNull(vm.state.value.binding)
        assertEquals("当前离线，无法操作", vm.state.value.snackbar)
    }

    @Test
    fun `device status offline overrides rest online`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device(online = true) }
        val statuses = MutableStateFlow(
            mapOf("d1" to DeviceStatus("d1", online = false, reason = "revoked")),
        )
        val vm = viewModel(repo, statuses = statuses)
        main.scheduler.advanceUntilIdle()

        // 服务端落库快照仍是 online=true，但权威离线事件必须覆盖它。
        assertFalse(vm.state.value.devices.single().online)
        assertEquals("revoked", vm.state.value.devices.single().reason)
    }

    @Test
    fun `list failure surfaces error`() = runTest {
        val repo = FakeDeviceRepository().apply {
            listError = AppError.Network("网络不可用")
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        assertEquals("网络不可用", vm.state.value.error)
        assertFalse(vm.state.value.loading)
    }

    @Test
    fun `revoked devices are filtered out of the list`() = runTest {
        val repo = FakeDeviceRepository().apply {
            devices += device(id = "d1")
            devices += device(id = "d2", name = "旧机器人").copy(isActive = false)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("d1"), vm.state.value.devices.map { it.device.id })
    }

    @Test
    fun `stale list response cannot resurrect a revoked device`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()
        assertEquals(1, vm.state.value.devices.size)

        // 手动刷新拿到的是撤销前的快照；响应被 gate 扣住时执行撤销。
        repo.listGate = kotlinx.coroutines.CompletableDeferred()
        vm.refresh()
        main.scheduler.runCurrent()

        vm.revoke("d1")
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.devices.isEmpty())
        // 被作废的列表请求不会再清 loading：撤销成功时必须自己清掉，否则刷新按钮永久置灰。
        assertFalse(vm.state.value.loading)

        repo.listGate?.complete(Unit)
        repo.listGate = null
        main.scheduler.advanceUntilIdle()

        // 旧请求晚回来不得把已撤销的设备加回列表。
        assertTrue(vm.state.value.devices.isEmpty())
    }

    @Test
    fun `post-write refresh is not dropped while a list request is in flight`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()
        val before = repo.listCalls

        repo.listGate = kotlinx.coroutines.CompletableDeferred()
        vm.refresh()
        main.scheduler.runCurrent()
        // 刷新还在途时重命名成功，随后的 refresh() 必须真的发出去。
        repo.renameGate = null
        vm.rename("d1", "书房机器人")
        main.scheduler.runCurrent()

        assertEquals(before + 2, repo.listCalls)

        repo.listGate?.complete(Unit)
        repo.listGate = null
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.loading)
    }

    @Test
    fun `a second write while busy is rejected with a message`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        repo.renameGate = kotlinx.coroutines.CompletableDeferred()
        vm.rename("d1", "书房机器人")
        main.scheduler.runCurrent()
        assertEquals("d1", vm.state.value.busyId)

        vm.revoke("d1")
        main.scheduler.runCurrent()

        assertTrue(repo.revoked.isEmpty())
        assertEquals("上一个操作还在进行中", vm.state.value.snackbar)

        repo.renameGate?.complete(Unit)
        main.scheduler.advanceUntilIdle()
    }

    @Test
    fun `duplicate pair code taps are ignored while the request is in flight`() = runTest {
        val repo = FakeDeviceRepository().apply {
            pairGate = kotlinx.coroutines.CompletableDeferred()
            pairCode = PairCodeDto(code = "654321", expiresIn = 60)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        vm.createPairCode()
        main.scheduler.runCurrent()

        assertEquals(1, repo.pairRequests.size)
        assertTrue(vm.state.value.pairCreating)

        repo.pairGate?.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.pairCreating)
        assertEquals("654321", vm.state.value.pairCode?.code)
    }

    @Test
    fun `zero ttl pair code is immediately expired`() = runTest {
        val repo = FakeDeviceRepository().apply {
            pairCode = PairCodeDto(code = "000000", expiresIn = 0)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        main.scheduler.runCurrent()

        assertEquals(true, vm.state.value.pairCode?.expired)
        assertEquals(0, vm.state.value.pairCode?.remainingSeconds)
    }

    @Test
    fun `revoke tombstone blocks a refresh issued while the delete is in flight`() = runTest {
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        // 撤销在途时手动刷新：GET 拿到的是撤销前快照，且代次比撤销新，不会被请求代次丢弃。
        repo.revokeGate = kotlinx.coroutines.CompletableDeferred()
        repo.listGate = kotlinx.coroutines.CompletableDeferred()
        vm.revoke("d1")
        main.scheduler.runCurrent()
        vm.refresh()
        main.scheduler.runCurrent()

        repo.revokeGate?.complete(Unit)
        repo.revokeGate = null
        main.scheduler.runCurrent()
        assertTrue(vm.state.value.devices.isEmpty())

        repo.devices = mutableListOf(device())
        repo.listGate?.complete(Unit)
        repo.listGate = null
        main.scheduler.advanceUntilIdle()

        // 墓碑必须挡住这次晚到的旧快照。
        assertTrue(vm.state.value.devices.isEmpty())
    }

    @Test
    fun `reconnect keeps the last known state then refreshes the snapshot`() = runTest {
        // 初始 REST 说在线；随后实时事件明确离线。
        val repo = FakeDeviceRepository().apply { devices += device(online = true) }
        val statuses = MutableStateFlow<Map<String, DeviceStatus>>(emptyMap())
        val connection = MutableStateFlow<ConnectionState>(ConnectionState.Connected)
        val vm = viewModel(repo, statuses = statuses, connection = connection)
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.devices.single().online)

        statuses.value = mapOf("d1" to DeviceStatus("d1", online = false))
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.devices.single().online)
        val before = repo.listCalls

        // 重连时 AppWsManager 清空实时缓存：不得回退到更旧的 REST=true。
        connection.value = ConnectionState.Retrying(attempt = 1, delayMillis = 1_000)
        main.scheduler.runCurrent()
        statuses.value = emptyMap()
        main.scheduler.runCurrent()
        assertFalse(vm.state.value.devices.single().online)

        // 重连成功后强制重拉快照，由 REST 收敛。
        repo.devices = mutableListOf(device(online = false))
        connection.value = ConnectionState.Connected
        main.scheduler.advanceUntilIdle()
        assertEquals(before + 1, repo.listCalls)
        assertFalse(vm.state.value.devices.single().online)
    }

    @Test
    fun `reconnect force refresh replaces a request stuck from the offline period`() = runTest {
        val repo = FakeDeviceRepository().apply {
            devices += device()
            listGate = kotlinx.coroutines.CompletableDeferred()
        }
        val connection = MutableStateFlow<ConnectionState>(ConnectionState.Connected)
        val vm = viewModel(repo, connection = connection)
        main.scheduler.runCurrent()
        assertTrue(vm.state.value.loading)
        assertTrue(vm.state.value.devices.isEmpty())

        // 断网期间发出的请求永远不返回；重连必须取消它并重新拉取。
        repo.listGate = null
        connection.value = ConnectionState.Retrying(attempt = 1, delayMillis = 1_000)
        main.scheduler.runCurrent()
        connection.value = ConnectionState.Connected
        main.scheduler.advanceUntilIdle()

        assertFalse(vm.state.value.loading)
        assertEquals(1, vm.state.value.devices.size)
    }

    @Test
    fun `assistant name load failure keeps the previous names`() = runTest {
        val assistants = FakeAssistantRepository().apply {
            items += AssistantDto(id = "a1", name = "小助手")
        }
        val repo = FakeDeviceRepository().apply { devices += device(boundAssistantId = "a1") }
        val vm = viewModel(repo, assistants = assistants)
        main.scheduler.advanceUntilIdle()
        assertEquals("小助手", vm.state.value.assistantNames["a1"])

        assistants.listError = AppError.Network("网络不可用")
        vm.refresh()
        main.scheduler.advanceUntilIdle()

        // 失败不能把已知角色名清空（否则已绑定设备会显示「未绑定」）。
        assertEquals("小助手", vm.state.value.assistantNames["a1"])
    }

    @Test
    fun `pair code dismissed while creating is not reopened`() = runTest {
        val repo = FakeDeviceRepository().apply {
            pairGate = kotlinx.coroutines.CompletableDeferred()
            pairCode = PairCodeDto(code = "246810", expiresIn = 60)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        main.scheduler.runCurrent()
        vm.dismissPairCode()
        repo.pairGate?.complete(Unit)
        main.scheduler.advanceUntilIdle()

        assertNull(vm.state.value.pairCode)
        assertFalse(vm.state.value.pairCreating)
    }

    @Test
    fun `zero ttl pair code stays expired as time advances`() = runTest {
        val repo = FakeDeviceRepository().apply {
            pairCode = PairCodeDto(code = "000000", expiresIn = 0)
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.createPairCode()
        main.scheduler.runCurrent()
        assertEquals(true, vm.state.value.pairCode?.expired)

        main.scheduler.advanceTimeBy(10_000)
        main.scheduler.runCurrent()
        assertEquals(true, vm.state.value.pairCode?.expired)
        assertEquals(0, vm.state.value.pairCode?.remainingSeconds)
    }

    @Test
    fun `openRemote emits navigation event`() = runTest {
        val events = mutableListOf<DevicesEvent>()
        val repo = FakeDeviceRepository().apply { devices += device() }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()
        // SharedFlow 没有 replay：必须让收集器先真正订阅，tryEmit 才不会把事件丢掉。
        // Unconfined 在 launch 时同步注册订阅，不依赖 TestDispatcher 的调度。
        val collector = CoroutineScope(Dispatchers.Unconfined).launch {
            vm.events.collect { events += it }
        }
        try {
            vm.openRemote("d1")
            assertEquals(listOf(DevicesEvent.OpenRemote("d1", "客厅机器人")), events)
        } finally {
            collector.cancel()
        }
    }
}
