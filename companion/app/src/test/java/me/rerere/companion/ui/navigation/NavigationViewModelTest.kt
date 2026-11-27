package me.rerere.companion.ui.navigation

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

class NavigationViewModelTest {

    class Probe : ViewModel() {
        var cleared = false

        override fun onCleared() {
            cleared = true
            super.onCleared()
        }
    }

    private val chat = AppRoute.Chat("c1", "a1", "角色")
    private val otherChat = AppRoute.Chat("c2", "a1", "角色")

    @Test
    fun `clearRoute clears only that route`() {
        val navigation = NavigationViewModel()
        val first = ViewModelProvider(navigation.ownerFor(chat))[Probe::class.java]
        val second = ViewModelProvider(navigation.ownerFor(otherChat))[Probe::class.java]

        navigation.clearRoute(chat)

        // 弹栈必须触发 onCleared，ChatViewModel 才会取消 SSE。
        assertTrue(first.cleared)
        assertFalse(second.cleared)
        assertNotSame(first, ViewModelProvider(navigation.ownerFor(chat))[Probe::class.java])
    }

    @Test
    fun `clearAll clears every route for logout`() {
        val navigation = NavigationViewModel()
        val first = ViewModelProvider(navigation.ownerFor(chat))[Probe::class.java]
        val second = ViewModelProvider(navigation.ownerFor(otherChat))[Probe::class.java]

        navigation.clearAll()

        assertTrue(first.cleared)
        assertTrue(second.cleared)
    }

    @Test
    fun `saved routes survive encode and decode`() {
        val routes = listOf(
            AppRoute.Assistants,
            AppRoute.Conversations("a1", "角色"),
            AppRoute.Chat("c1", "a1", "角色"),
            AppRoute.Memories("a1", "角色"),
            AppRoute.AssistantEdit("a1", "instance-1"),
            AppRoute.AssistantEdit(null, "instance-2"),
        )

        routes.forEach { route ->
            assertEquals(route, decodeRouteForTest(encodeRouteForTest(route)))
        }
    }

    @Test
    fun `corrupted saved route falls back to assistants`() {
        assertEquals(AppRoute.Assistants, decodeRouteForTest("not-json"))
        assertEquals(AppRoute.Assistants, decodeRouteForTest("""{"type":"unknown"}"""))
    }
}
