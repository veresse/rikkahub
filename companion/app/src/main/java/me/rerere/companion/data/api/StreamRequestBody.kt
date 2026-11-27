package me.rerere.companion.data.api

import java.io.InputStream
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source

/**
 * 把上传流直接写进请求体，避免把整包备份读进内存。
 *
 * `isOneShot()` 必须为 true：OkHttp 不会在重试时重新读取同一个流
 * （重试应由调用方重新打开文件）。
 */
class StreamRequestBody(
    private val open: () -> InputStream,
    private val length: Long = -1L,
) : RequestBody() {

    override fun contentType() = "application/zip".toMediaType()

    override fun contentLength() = length

    override fun isOneShot() = true

    override fun writeTo(sink: BufferedSink) {
        open().use { input -> sink.writeAll(input.source()) }
    }
}
