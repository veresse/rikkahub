package me.rerere.companion.feature.settings

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AuthTokenDto
import me.rerere.companion.data.api.dto.DashscopeSettingsDto
import me.rerere.companion.data.api.dto.DeepseekSettingsDto
import me.rerere.companion.data.api.dto.SearchSettingsDto
import me.rerere.companion.data.api.dto.SettingsDto
import me.rerere.companion.data.api.dto.VoiceSettingsDto
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.FakeSettingsRepository
import me.rerere.companion.testutil.FakeTokenRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private val token = AuthTokenDto(
        id = "id0",
        username = "admin",
        deviceName = "当前设备",
        createdAt = 1700000000000,
        expiresAt = 1800000000000,
    )

    private fun sampleView() = SettingsDto(
        deepseek = DeepseekSettingsDto(
            configured = true,
            source = "database",
            baseUrl = "https://api.deepseek.com",
            model = "deepseek-chat",
        ),
        dashscope = DashscopeSettingsDto(
            configured = false,
            source = "unset",
            region = "cn-beijing",
            asrModel = "fun-asr-realtime",
            ttsModel = "qwen-tts",
            ttsVoice = "cherry",
        ),
        voice = VoiceSettingsDto(
            enabled = true,
            provider = "unavailable",
            effective = "unavailable",
            reason = "dashscope_key_missing",
        ),
        search = SearchSettingsDto(enabled = true),
    )

    private suspend fun session(): SessionManager {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        manager.save(
            StoredSession(
                serverUrl = "http://127.0.0.1/",
                token = "t0",
                tokenId = "id0",
                username = "admin",
                expiresAtMillis = Long.MAX_VALUE,
            ),
        )
        return manager
    }

    private fun viewModel(
        settings: FakeSettingsRepository = FakeSettingsRepository().apply { view = sampleView() },
        tokens: FakeTokenRepository = FakeTokenRepository(),
        manager: SessionManager,
        online: Boolean = true,
    ) = SettingsViewModel(
        settingsRepository = settings,
        tokenRepository = tokens,
        sessionManager = manager,
        isOnline = { online },
    )

    private fun TestScope.collectEvents(vm: SettingsViewModel): MutableList<SettingsEvent> {
        val events = mutableListOf<SettingsEvent>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            vm.events.collect { events += it }
        }
        return events
    }

    @Test
    fun `load maps view into form state`() = runTest(main.dispatcher) {
        val tokens = FakeTokenRepository().apply { this.tokens = listOf(token) }
        val vm = viewModel(manager = session(), tokens = tokens)
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertFalse(state.loading)
        assertEquals("https://api.deepseek.com", state.deepseekBaseUrl)
        assertEquals("deepseek-chat", state.deepseekModel)
        assertTrue(state.deepseekConfigured)
        assertEquals("cn-beijing", state.dashscopeRegion)
        assertEquals("cherry", state.dashscopeTtsVoice)
        assertEquals("unavailable", state.voiceEffective)
        assertEquals("dashscope_key_missing", state.voiceReason)
        assertEquals(listOf(token), state.tokens)
        assertEquals("", state.deepseekKeyDraft)
    }

    @Test
    fun `save deepseek sends plain fields and keeps key out of state`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply { view = sampleView() }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()

        vm.setDeepseekBaseUrl(" https://api.deepseek.com ")
        vm.setDeepseekModel("deepseek-reasoner")
        vm.saveDeepseek()
        main.scheduler.advanceUntilIdle()

        val body = settings.lastUpdateBody
        assertEquals("https://api.deepseek.com", body?.deepseekBaseUrl)
        assertEquals("deepseek-reasoner", body?.deepseekModel)
        assertNull(body?.searchEnabled)
        assertEquals(0, settings.secretsCalls)
        assertEquals("", vm.state.value.deepseekKeyDraft)
    }

    @Test
    fun `saving a secret clears the draft and never emits it`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply {
            view = sampleView().copy(
                deepseek = sampleView().deepseek.copy(configured = true, source = "database"),
            )
        }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()
        val events = collectEvents(vm)

        vm.setDeepseekKeyDraft("sk-test-write-only")
        vm.saveDeepseek()
        main.scheduler.advanceUntilIdle()

        assertEquals("sk-test-write-only", settings.lastSecretsBody?.deepseekApiKey)
        assertEquals("", vm.state.value.deepseekKeyDraft)
        assertFalse(vm.state.value.toString().contains("sk-test-write-only"))
        assertFalse(events.joinToString { it.toString() }.contains("sk-test-write-only"))
    }

    @Test
    fun `offline blocks every write`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply { view = sampleView() }
        val tokens = FakeTokenRepository().apply { this.tokens = listOf(token) }
        val vm = viewModel(settings = settings, tokens = tokens, manager = session(), online = false)
        main.scheduler.advanceUntilIdle()

        vm.saveDeepseek()
        vm.saveVoice()
        vm.setSearchEnabled(false)
        vm.revokeToken("id0")
        main.scheduler.advanceUntilIdle()

        assertEquals(0, settings.updateCalls)
        assertEquals(0, settings.secretsCalls)
        assertTrue(tokens.revokeCalls.isEmpty())
    }

    @Test
    fun `revoking the current token clears the session`() = runTest(main.dispatcher) {
        val manager = session()
        val vm = viewModel(
            settings = FakeSettingsRepository().apply { view = sampleView() },
            tokens = FakeTokenRepository().apply { this.tokens = listOf(token) },
            manager = manager,
        )
        main.scheduler.advanceUntilIdle()
        val events = collectEvents(vm)

        vm.revokeToken("id0")
        main.scheduler.advanceUntilIdle()

        assertNull(manager.state.value)
        assertTrue(events.any { it is SettingsEvent.LoggedOut })
    }

    @Test
    fun `revoking another token refreshes the list`() = runTest(main.dispatcher) {
        val manager = session()
        val other = token.copy(id = "id1", deviceName = "旧手机")
        val tokens = FakeTokenRepository().apply { this.tokens = listOf(token, other) }
        val vm = viewModel(manager = manager, tokens = tokens)
        main.scheduler.advanceUntilIdle()
        assertEquals(1, tokens.listCalls)

        vm.revokeToken("id1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("id1"), tokens.revokeCalls)
        assertEquals(2, tokens.listCalls)
        assertTrue(manager.state.value != null)
        assertNull(vm.state.value.revokingId)
    }

    @Test
    fun `revoking an already revoked token removes the stale row`() = runTest(main.dispatcher) {
        val manager = session()
        val other = token.copy(id = "id1", deviceName = "旧手机")
        val tokens = FakeTokenRepository().apply {
            this.tokens = listOf(token, other)
            revokeResult = AppResult.Err(
                AppError.Http("token_not_found", "该登录已不存在或已撤销"),
            )
        }
        val vm = viewModel(manager = manager, tokens = tokens)
        main.scheduler.advanceUntilIdle()
        val events = collectEvents(vm)

        // 服务端已没有 id1：409/404 语义按“已撤销”处理，而不是留在列表里。
        tokens.tokens = listOf(token)
        vm.revokeToken("id1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("id1"), tokens.revokeCalls)
        assertEquals(listOf("id0"), vm.state.value.tokens.map { it.id })
        assertTrue(events.any { it is SettingsEvent.Message && it.text.contains("已不存在") })
    }

    @Test
    fun `search toggle sends only search_enabled`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply {
            view = sampleView().copy(search = SearchSettingsDto(enabled = false))
        }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()

        vm.setSearchEnabled(false)
        main.scheduler.advanceUntilIdle()

        val body = settings.lastUpdateBody
        assertEquals(false, body?.searchEnabled)
        assertNull(body?.deepseekBaseUrl)
        assertNull(body?.deepseekModel)
        assertNull(body?.voiceEnabled)
        assertNull(body?.dashscopeRegion)
        assertFalse(vm.state.value.searchEnabled)
    }

    @Test
    fun `saving a secret without base url reports a readable error`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply {
            view = sampleView().copy(deepseek = DeepseekSettingsDto())
        }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()
        val events = collectEvents(vm)

        vm.setDeepseekKeyDraft("sk-should-not-be-emitted")
        vm.saveDeepseek()
        main.scheduler.advanceUntilIdle()

        assertEquals(0, settings.updateCalls)
        assertEquals(0, settings.secretsCalls)
        assertTrue(events.any { it is SettingsEvent.Message })
        assertFalse(events.joinToString { it.toString() }.contains("sk-should-not-be-emitted"))
    }

    @Test
    fun `network failure while saving keeps the key draft for retry`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply {
            view = sampleView()
            secretsResult = AppResult.Err(AppError.Network("无法连接服务器，请检查网络"))
        }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()

        vm.setDeepseekKeyDraft("sk-retry-me")
        vm.saveDeepseek()
        main.scheduler.advanceUntilIdle()

        assertEquals("sk-retry-me", vm.state.value.deepseekKeyDraft)
        assertFalse(vm.state.value.saving)
    }

    @Test
    fun `events are not replayed to a late collector`() = runTest(main.dispatcher) {
        val settings = FakeSettingsRepository().apply { view = sampleView() }
        val vm = viewModel(settings = settings, manager = session())
        main.scheduler.advanceUntilIdle()

        vm.saveDeepseek()
        main.scheduler.advanceUntilIdle()

        // 旋转屏幕后重新订阅：不能再次弹出上一次的“已保存”。
        val late = collectEvents(vm)
        main.scheduler.advanceUntilIdle()
        assertTrue(late.isEmpty())
    }

    @Test
    fun `visible tokens hide revoked and expired rows`() {
        val now = 1_800_000_000_000L
        val revoked = token.copy(id = "revoked", revokedAt = now - 1)
        val expired = token.copy(id = "expired", expiresAt = now - 1)
        val fresh = token.copy(id = "fresh", expiresAt = now + 1)

        assertEquals(
            listOf("fresh"),
            visibleTokens(listOf(revoked, expired, fresh), now).map { it.id },
        )
    }
}
