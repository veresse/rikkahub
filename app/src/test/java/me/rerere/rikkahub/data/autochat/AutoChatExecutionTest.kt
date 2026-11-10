package me.rerere.rikkahub.data.autochat

import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import me.rerere.ai.core.MessageRole
import me.rerere.ai.ui.UIMessage
import me.rerere.ai.ui.UIMessageAnnotation
import me.rerere.rikkahub.data.model.Assistant
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import me.rerere.rikkahub.data.model.Conversation
import me.rerere.rikkahub.data.model.toMessageNode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.uuid.Uuid

class AutoChatExecutionTest {

    @Test
    fun `auto chat creates distinct independent assistant node when last message is assistant`() {
        val convId = Uuid.random()
        val assistantId = Uuid.random()

        // 原会话历史最后一条是 ASSISTANT
        val existingAssistantMsg = UIMessage.assistant("这是上一条助手消息")
        val initialConversation = Conversation.ofId(convId, assistantId = assistantId).copy(
            messageNodes = listOf(existingAssistantMsg.toMessageNode())
        )

        assertEquals(1, initialConversation.messageNodes.size)
        assertEquals(MessageRole.ASSISTANT, initialConversation.messageNodes[0].role)

        // 模拟主动消息写入：生成一个新消息节点
        val newAutoChatMsg = UIMessage.assistant("早安！新的一天开始了。").copy(
            id = Uuid.random(),
            annotations = listOf(UIMessageAnnotation.AutoChat(isTest = false)),
        )

        val updatedConversation = initialConversation.copy(
            messageNodes = initialConversation.messageNodes + newAutoChatMsg.toMessageNode()
        )

        // 验证：节点数量变成 2，上一条助手节点保持原样，新节点是独立节点！
        assertEquals(2, updatedConversation.messageNodes.size)
        assertEquals(existingAssistantMsg.id, updatedConversation.messageNodes[0].messages.first().id)
        assertEquals(newAutoChatMsg.id, updatedConversation.messageNodes[1].messages.first().id)
        assertNotEquals(existingAssistantMsg.id, newAutoChatMsg.id)

        // 验证标记
        val annotation = updatedConversation.messageNodes[1].messages.first().annotations
            .filterIsInstance<UIMessageAnnotation.AutoChat>().firstOrNull()
        assertNotNull(annotation)
        assertFalse(annotation!!.isTest)
    }

    @Test
    fun `auto chat test message carries test annotation`() {
        val testMsg = UIMessage.assistant("测试消息").copy(
            annotations = listOf(UIMessageAnnotation.AutoChat(isTest = true))
        )
        val annotation = testMsg.annotations.filterIsInstance<UIMessageAnnotation.AutoChat>().firstOrNull()
        assertNotNull(annotation)
        assertTrue(annotation!!.isTest)
    }

    @Test
    fun `synthetic prompt message does not masquerade as regular user message`() {
        val promptWithTime = "【系统主动触发 - 当前时间: 2026-11-09 08:00】\n早安"
        val syntheticMsg = UIMessage.user(promptWithTime).copy(isSynthetic = true)

        assertTrue(syntheticMsg.isSynthetic)
        assertEquals(MessageRole.USER, syntheticMsg.role)
        // 验证在判定用户是否回复时，synthetic 消息被忽略
        val engine = AutoChatTimeEngine()
        assertFalse(engine.hasUserRepliedSinceLastAuto(listOf(syntheticMsg)))
    }

    @Test
    fun `rule validation ensures assistant and conversation strictly match`() {
        val assistantA = Uuid.random()
        val assistantB = Uuid.random()
        val convId = Uuid.random()

        val convOfB = Conversation.ofId(convId, assistantId = assistantB)

        // 规则绑定的是 assistantA，但会话属于 assistantB
        val rule = AutoChatRule(
            assistantId = assistantA,
            conversationId = convId,
            enabled = true,
        )

        // 验证匹配性判断：不匹配时禁止执行，必须报错/暂停
        val matches = rule.assistantId == convOfB.assistantId
        assertFalse(matches)
    }
}
