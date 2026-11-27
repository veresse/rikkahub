package me.rerere.companion.testutil

import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import me.rerere.companion.data.ws.WsConnector
import me.rerere.companion.data.ws.WsFrame
import me.rerere.companion.data.ws.WsSink

/**
 * 按脚本依次建立连接的假传输层。
 *
 * 脚本用完后返回一个永不结束的流，避免测试里因为反复重连出现“重连风暴”。
 */
class FakeConnector(
    private val scripts: MutableList<Flow<WsFrame>>,
) : WsConnector {
    val attempts = mutableListOf<String>()

    override fun connect(url: String, token: String): Flow<WsFrame> {
        attempts += url
        return if (scripts.isEmpty()) flow { awaitCancellation() } else scripts.removeAt(0)
    }
}

/** 发完给定帧后保持连接（直到调用方取消）。 */
fun keepOpen(vararg frames: WsFrame): Flow<WsFrame> = flow {
    frames.forEach { emit(it) }
    awaitCancellation()
}

/** 记录 App → 服务端命令的假句柄；[send] 恒为成功，真实失败由 `WsFrame.Closed` 模拟。 */
class RecordingSink : WsSink {
    val sent = mutableListOf<String>()

    override fun send(text: String): Boolean {
        sent += text
        return true
    }
}
