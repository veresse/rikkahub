package me.rerere.companion.feature.chat

import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import me.rerere.companion.data.api.dto.ConversationDetailDto
import me.rerere.companion.data.api.dto.MessageNodeDto
import me.rerere.companion.data.api.dto.MessagePartDto
import me.rerere.companion.data.api.dto.UiMessageDto
import me.rerere.companion.data.sse.ChatEvent
import me.rerere.companion.data.ws.TurnState
import me.rerere.companion.data.ws.WsEvent
import me.rerere.companion.testutil.FakeChatRepository
import me.rerere.companion.testutil.FakeConversationRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ChatViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private val history = ConversationDetailDto(
        id = "c1",
        title = "会话标题",
        nodes = listOf(
            MessageNodeDto(
                id = "n1",
                nodeIndex = 0,
                messages = listOf(
                    UiMessageDto(
                        id = "u1",
                        role = "USER",
                        parts = listOf(MessagePartDto(text = "你好")),
                    ),
                ),
                selectIndex = 0,
            ),
            MessageNodeDto(
                id = "n2",
                nodeIndex = 1,
                messages = listOf(
                    UiMessageDto(
                        id = "a1",
                        role = "ASSISTANT",
                        parts = listOf(
                            MessagePartDto(text = "在的"),
                            MessagePartDto(reasoning = "想了想"),
                        ),
                    ),
                ),
                selectIndex = 0,
            ),
        ),
    )

    private fun viewModel(
        conversations: FakeConversationRepository = FakeConversationRepository().apply { detail = history },
        chat: FakeChatRepository = FakeChatRepository(),
        turnStates: MutableStateFlow<Map<String, TurnState>>? = null,
        wsEvents: MutableSharedFlow<WsEvent>? = null,
        online: Boolean = true,
    ) = ChatViewModel(
        conversationId = "c1",
        assistantName = "角色",
        conversationRepository = conversations,
        chatRepository = chat,
        turnStates = turnStates,
        wsEvents = wsEvents,
        isOnline = { online },
    )

    @Test
    fun `loads history into ui messages`() = runTest {
        val vm = viewModel()
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals(false, state.loading)
        assertEquals("会话标题", state.title)
        assertEquals(listOf("USER", "ASSISTANT"), state.messages.map { it.role })
        assertEquals("你好", state.messages[0].text)
        assertEquals("在的", state.messages[1].text)
        assertEquals("想了想", state.messages[1].reasoning)
        assertEquals("u1", state.messages[0].userMessageId)
        assertEquals("n1", state.messages[0].nodeId)
    }

    @Test
    fun `send streams content and stores server ids`() = runTest {
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flowOf(
                    ChatEvent.Content("你"),
                    ChatEvent.Content("好"),
                    ChatEvent.Reasoning("思考"),
                    ChatEvent.Done("stop", "m2", "u2", replayed = false),
                )
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("  在吗  ")
        vm.send()
        main.scheduler.advanceUntilIdle()

        val request = chat.requests.single()
        assertEquals("c1", request.conversationId)
        assertEquals("在吗", request.message)
        assertTrue(!request.clientMsgId.isNullOrBlank())

        val state = vm.state.value
        assertEquals("", state.input)
        assertEquals(false, state.sending)
        assertEquals(false, state.generating)
        val user = state.messages[state.messages.lastIndex - 1]
        val assistant = state.messages.last()
        assertEquals("USER", user.role)
        assertEquals("u2", user.userMessageId)
        assertEquals("u2", user.nodeId)
        assertEquals("你好", assistant.text)
        assertEquals("思考", assistant.reasoning)
        assertEquals("m2", assistant.messageId)
        assertEquals(false, assistant.streaming)
        assertFalse(assistant.failed)
    }

    @Test
    fun `failure is retried with the same client message id`() = runTest {
        val chat = FakeChatRepository()
        var attempt = 0
        chat.streamFactory = {
            attempt += 1
            if (attempt == 1) {
                flowOf(ChatEvent.Content("半"), ChatEvent.Failed("network", "网络中断"))
            } else {
                flowOf(ChatEvent.Content("完整"), ChatEvent.Done("stop", "m2", "u2", false))
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.messages.last().failed)

        vm.retry(vm.state.value.messages.last().key)
        main.scheduler.advanceUntilIdle()

        assertEquals(2, chat.requests.size)
        // 服务端靠 client_msg_id 幂等；重试必须是同一个 id，否则会重复落库。
        assertEquals(chat.requests[0].clientMsgId, chat.requests[1].clientMsgId)
        val assistant = vm.state.value.messages.last()
        assertEquals(false, assistant.failed)
        assertEquals("m2", assistant.messageId)
        assertEquals("完整", assistant.text)
    }

    @Test
    fun `stop keeps partial text and marks interrupted`() = runTest {
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flow {
                    emit(ChatEvent.Content("半句"))
                    awaitCancellation()
                }
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.messages.last().streaming)

        vm.stop()
        main.scheduler.advanceUntilIdle()

        val assistant = vm.state.value.messages.last()
        assertEquals("半句", assistant.text)
        assertEquals(false, assistant.streaming)
        assertTrue(assistant.interrupted)
        assertFalse(vm.state.value.sending)
    }

    @Test
    fun `stop removes an empty assistant bubble`() = runTest {
        val chat = FakeChatRepository().apply {
            streamFactory = { flow { awaitCancellation() } }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()
        val before = vm.state.value.messages.size

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()
        assertEquals(before + 2, vm.state.value.messages.size)

        vm.stop()
        main.scheduler.advanceUntilIdle()

        // 没有任何内容的助手气泡不应留在历史里。
        assertEquals(before + 1, vm.state.value.messages.size)
        assertEquals("USER", vm.state.value.messages.last().role)
    }

    @Test
    fun `remote turn state disables sending`() = runTest {
        val turnStates = MutableStateFlow<Map<String, TurnState>>(emptyMap())
        val vm = viewModel(turnStates = turnStates)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.generating)

        turnStates.value = mapOf(
            "c1" to TurnState(
                conversationId = "c1",
                state = "generating",
                source = "voice",
                turnKey = "k1",
            ),
        )
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.generating)

        turnStates.value = emptyMap()
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.generating)
    }

    @Test
    fun `voice events fill the draft without sending`() = runTest {
        val ws = MutableSharedFlow<WsEvent>(extraBufferCapacity = 8)
        val vm = viewModel(wsEvents = ws)
        main.scheduler.advanceUntilIdle()

        ws.tryEmit(
            WsEvent.Voice(
                event = "asr_final",
                deviceId = "d1",
                turnId = "t1",
                text = "  今天天气不错  ",
                conversationId = "c1",
                userMessageId = null,
                finishReason = null,
                interrupted = false,
            ),
        )
        main.scheduler.advanceUntilIdle()
        assertEquals("今天天气不错", vm.state.value.voiceDraft)

        vm.useVoiceDraft()
        assertEquals("今天天气不错", vm.state.value.input)
        assertNull(vm.state.value.voiceDraft)
    }

    @Test
    fun `voice event without conversation id is ignored`() = runTest {
        val ws = MutableSharedFlow<WsEvent>(extraBufferCapacity = 8)
        val vm = viewModel(wsEvents = ws)
        main.scheduler.advanceUntilIdle()

        ws.tryEmit(
            WsEvent.Voice(
                event = "asr_final",
                deviceId = "d1",
                turnId = "t1",
                text = "别的会话的转写",
                conversationId = null,
                userMessageId = null,
                finishReason = null,
                interrupted = false,
            ),
        )
        main.scheduler.advanceUntilIdle()

        // 无法判断归属的事件宁可丢弃，也不能串到当前会话。
        assertNull(vm.state.value.voiceDraft)
    }

    @Test
    fun `offline send is refused before any request`() = runTest {
        val chat = FakeChatRepository()
        val vm = viewModel(chat = chat, online = false)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()

        assertTrue(chat.requests.isEmpty())
        // 只保留历史里的两条，没有追加本地用户气泡/助手气泡。
        assertEquals(2, vm.state.value.messages.size)
        assertEquals("当前离线，无法发送", vm.state.value.snackbar)
    }

    @Test
    fun `send while history is loading is refused`() = runTest {
        val chat = FakeChatRepository()
        val vm = viewModel(chat = chat)
        // 不推进调度器：此时 detail 还没返回，state.loading == true。
        assertTrue(vm.state.value.loading)
        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()

        assertTrue(chat.requests.isEmpty())
        // 历史加载完成，没有被新轮次作废。
        assertEquals(2, vm.state.value.messages.size)
        assertFalse(vm.state.value.loading)
    }

    @Test
    fun `retry after seconds is shown on failure`() = runTest {
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flowOf(ChatEvent.Failed("rate_limited", "请稍后再试", retryAfterSeconds = 30))
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()

        assertTrue(vm.state.value.messages.last().failed)
        assertTrue(vm.state.value.snackbar.orEmpty().contains("30"))
    }

    @Test
    fun `io error after done keeps the completed answer`() = runTest {
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flow {
                    emit(ChatEvent.Content("完整回答"))
                    emit(ChatEvent.Done("stop", "m2", "u2", replayed = false))
                    throw java.io.IOException("boom")
                }
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()

        val assistant = vm.state.value.messages.last()
        assertEquals("完整回答", assistant.text)
        assertFalse(assistant.failed)
        assertFalse(assistant.streaming)
        assertNull(vm.state.value.snackbar)
    }

    @Test
    fun `reload is ignored while a turn is streaming`() = runTest {
        val conversations = FakeConversationRepository()
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flow {
                    emit(ChatEvent.Content("半句"))
                    awaitCancellation()
                }
            }
        }
        val vm = viewModel(conversations = conversations, chat = chat)
        main.scheduler.advanceUntilIdle()
        val loadsBefore = conversations.detailRequests.size

        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()
        vm.reload()
        main.scheduler.advanceUntilIdle()

        // 生成中重建列表会把本地流式气泡换掉，后续 delta 写不进去。
        assertEquals(loadsBefore, conversations.detailRequests.size)

        vm.stop()
        main.scheduler.advanceUntilIdle()
        vm.reload()
        main.scheduler.advanceUntilIdle()
        assertEquals(loadsBefore + 1, conversations.detailRequests.size)
    }

    @Test
    fun `retry targets the clicked failed bubble`() = runTest {
        val chat = FakeChatRepository()
        var attempt = 0
        chat.streamFactory = {
            attempt += 1
            when (attempt) {
                1 -> flowOf(ChatEvent.Content("一"), ChatEvent.Failed("network", "网络中断"))
                2 -> flowOf(ChatEvent.Content("二"), ChatEvent.Failed("network", "网络中断"))
                else -> flowOf(ChatEvent.Content("重试成功"), ChatEvent.Done("stop", "m3", "u3", false))
            }
        }
        val vm = viewModel(chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.onInputChange("第一句")
        vm.send()
        main.scheduler.advanceUntilIdle()
        val firstFailedKey = vm.state.value.messages.last().key
        vm.onInputChange("第二句")
        vm.send()
        main.scheduler.advanceUntilIdle()

        vm.retry(firstFailedKey)
        main.scheduler.advanceUntilIdle()

        // 点旧气泡必须重试旧轮次，而不是“最后一条失败”。
        assertEquals(3, chat.requests.size)
        assertEquals(chat.requests[0].clientMsgId, chat.requests[2].clientMsgId)
        assertEquals(
            "重试成功",
            vm.state.value.messages.first { it.key == firstFailedKey }.text,
        )
    }

    @Test
    fun `replayed done reloads conversation`() = runTest {
        val conversations = FakeConversationRepository().apply { detail = history }
        val chat = FakeChatRepository().apply {
            streamFactory = {
                flowOf(ChatEvent.Done("stop", "m1", "u1", replayed = true))
            }
        }
        val vm = viewModel(conversations = conversations, chat = chat)
        main.scheduler.advanceUntilIdle()
        assertEquals("会话标题", vm.state.value.title)

        // 幂等重放：服务端说这一轮已经落库，本地必须重新拉详情而不是相信流式拼装。
        conversations.detail = history.copy(title = "重新拉取的标题")
        vm.onInputChange("在吗")
        vm.send()
        main.scheduler.advanceUntilIdle()

        assertEquals("重新拉取的标题", vm.state.value.title)
        assertEquals(2, vm.state.value.messages.size)
    }

    @Test
    fun `correction is refused when the message was never stored`() = runTest {
        val unsaved = ConversationDetailDto(
            id = "c1",
            nodes = listOf(
                MessageNodeDto(
                    id = "n1",
                    nodeIndex = 0,
                    messages = listOf(
                        UiMessageDto(
                            id = "",
                            role = "USER",
                            parts = listOf(MessagePartDto(text = "临时")),
                        ),
                    ),
                    selectIndex = 0,
                ),
            ),
        )
        val conversations = FakeConversationRepository().apply { detail = unsaved }
        val chat = FakeChatRepository()
        val vm = viewModel(conversations = conversations, chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.startCorrection("node-n1")
        vm.correct("改一下")
        main.scheduler.advanceUntilIdle()

        assertTrue(conversations.corrections.isEmpty())
        assertNull(vm.state.value.correctionTarget)
        assertEquals("该消息尚未保存，暂不能纠正", vm.state.value.snackbar)
    }

    @Test
    fun `voice turn end without conversation id is ignored`() = runTest {
        val ws = MutableSharedFlow<WsEvent>(extraBufferCapacity = 8)
        val vm = viewModel(wsEvents = ws)
        main.scheduler.advanceUntilIdle()

        ws.tryEmit(
            WsEvent.Voice(
                event = "turn_end",
                deviceId = "d1",
                turnId = "t1",
                text = null,
                conversationId = null,
                userMessageId = null,
                finishReason = "interrupted",
                interrupted = true,
            ),
        )
        main.scheduler.advanceUntilIdle()

        // 无法判断归属的中断事件也不能改当前会话状态。
        assertFalse(vm.state.value.voiceInterrupted)
    }

    @Test
    fun `stale voice turn end does not overwrite the new turn`() = runTest {
        val ws = MutableSharedFlow<WsEvent>(extraBufferCapacity = 8)
        val vm = viewModel(wsEvents = ws)
        main.scheduler.advanceUntilIdle()

        fun voice(event: String, turnId: String, interrupted: Boolean) = WsEvent.Voice(
            event = event,
            deviceId = "d1",
            turnId = turnId,
            text = null,
            conversationId = "c1",
            userMessageId = null,
            finishReason = null,
            interrupted = interrupted,
        )

        ws.tryEmit(voice("turn_start", "t1", false))
        ws.tryEmit(voice("turn_start", "t2", false))
        // 旧轮取消清理晚到：不得把 t1 的中断状态贴到正在进行的 t2 上。
        ws.tryEmit(voice("turn_end", "t1", true))
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.voiceInterrupted)

        ws.tryEmit(voice("turn_end", "t2", true))
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.voiceInterrupted)
    }

    @Test
    fun `voice turn end marks interruption`() = runTest {
        val ws = MutableSharedFlow<WsEvent>(extraBufferCapacity = 8)
        val vm = viewModel(wsEvents = ws)
        main.scheduler.advanceUntilIdle()

        ws.tryEmit(
            WsEvent.Voice(
                event = "turn_end",
                deviceId = "d1",
                turnId = "t1",
                text = null,
                conversationId = "c1",
                userMessageId = null,
                finishReason = "interrupted",
                interrupted = true,
            ),
        )
        main.scheduler.advanceUntilIdle()

        assertTrue(vm.state.value.voiceInterrupted)
    }

    @Test
    fun `correction only updates stored user text`() = runTest {
        val conversations = FakeConversationRepository().apply { detail = history }
        val chat = FakeChatRepository()
        val vm = viewModel(conversations = conversations, chat = chat)
        main.scheduler.advanceUntilIdle()

        vm.startCorrection("node-n1")
        assertNotNull(vm.state.value.correctionTarget)
        vm.correct("  你好呀  ")
        main.scheduler.advanceUntilIdle()

        val correction = conversations.corrections.single()
        assertEquals("c1", correction.conversationId)
        assertEquals("n1", correction.nodeId)
        assertEquals("u1", correction.messageId)
        assertEquals("你好呀", correction.text)
        assertEquals("你好呀", vm.state.value.messages.first().text)
        assertNull(vm.state.value.correctionTarget)
        // 只改文本，不自动重生成。
        assertTrue(chat.requests.isEmpty())
    }
}
