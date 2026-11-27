package me.rerere.companion.feature.login

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.core.UrlError
import me.rerere.companion.testutil.FakeAuthRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LoginViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private fun viewModel(repo: FakeAuthRepository, allowCleartext: Boolean = false) =
        LoginViewModel(
            repository = repo,
            allowCleartext = allowCleartext,
            elapsedMillis = { main.scheduler.currentTime },
            ticker = { delay(it) },
        )

    @Test
    fun `invalid url blocks repository call`() = runTest {
        val repo = FakeAuthRepository()
        val vm = viewModel(repo)
        vm.onServerUrlChange("ftp://example.com")
        vm.onPasswordChange("pw")

        vm.submit()
        main.scheduler.advanceUntilIdle()

        assertEquals(UrlError.BAD_SCHEME, vm.state.value.urlError)
        assertTrue(repo.calls.isEmpty())
    }

    @Test
    fun `successful login sends normalized url and clears error`() = runTest {
        val repo = FakeAuthRepository()
        val vm = viewModel(repo)
        vm.onServerUrlChange("https://example.com")
        vm.onPasswordChange("pw")

        vm.submit()
        main.scheduler.advanceUntilIdle()

        assertNull(vm.state.value.error)
        assertEquals("https://example.com/", repo.calls.single().serverUrl)
        assertFalse(vm.state.value.submitting)
        // Activity 作用域的 VM 会活到登出之后，成功后必须清口令，否则回登录页会被回填。
        assertEquals("", vm.state.value.password)
    }

    @Test
    fun `401 shows credentials message`() = runTest {
        val repo = FakeAuthRepository(
            result = AppResult.Err(AppError.Http("invalid_credentials", "账号或口令错误")),
        )
        val vm = viewModel(repo)
        vm.onServerUrlChange("https://example.com")
        vm.onPasswordChange("bad")

        vm.submit()
        main.scheduler.advanceUntilIdle()

        assertEquals("账号或口令错误", vm.state.value.error)
        assertTrue(vm.state.value.canSubmit)
    }

    @Test
    fun `rate limited counts down and re-enables submit`() = runTest {
        val repo = FakeAuthRepository(
            result = AppResult.Err(
                AppError.Http("rate_limited", "尝试过于频繁，请稍后再试", retryAfterSeconds = 2),
            ),
        )
        val vm = viewModel(repo)
        vm.onServerUrlChange("https://example.com")
        vm.onPasswordChange("pw")

        vm.submit()
        main.scheduler.runCurrent()

        assertEquals(2, vm.state.value.retryAfterSeconds)
        assertFalse(vm.state.value.canSubmit)

        main.scheduler.advanceTimeBy(1_000)
        main.scheduler.runCurrent()
        assertEquals(1, vm.state.value.retryAfterSeconds)

        main.scheduler.advanceTimeBy(1_000)
        main.scheduler.runCurrent()
        assertEquals(0, vm.state.value.retryAfterSeconds)
        assertTrue(vm.state.value.canSubmit)
    }

    @Test
    fun `rate limited without retry header stays submit-able`() = runTest {
        val repo = FakeAuthRepository(
            result = AppResult.Err(AppError.Http("rate_limited", "尝试过于频繁，请稍后再试")),
        )
        val vm = viewModel(repo)
        vm.onServerUrlChange("https://example.com")
        vm.onPasswordChange("pw")

        vm.submit()
        main.scheduler.advanceUntilIdle()

        assertEquals(0, vm.state.value.retryAfterSeconds)
        assertTrue(vm.state.value.canSubmit)
        assertEquals("尝试过于频繁，请稍后再试", vm.state.value.error)
    }

    @Test
    fun `double tap while submitting calls repository once`() = runTest {
        val repo = FakeAuthRepository()
        val gate = CompletableDeferred<Unit>()
        repo.gate = gate
        val vm = viewModel(repo)
        vm.onServerUrlChange("https://example.com")
        vm.onPasswordChange("pw")

        vm.submit()
        vm.submit()
        main.scheduler.runCurrent()
        assertEquals(1, repo.calls.size)

        gate.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.submitting)
    }
}
