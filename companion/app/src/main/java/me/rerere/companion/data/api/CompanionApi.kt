package me.rerere.companion.data.api

import me.rerere.companion.data.api.dto.ActionCatalogDto
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.api.dto.ChatRequestDto
import me.rerere.companion.data.api.dto.ConversationCreate
import me.rerere.companion.data.api.dto.ConversationDetailDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.api.dto.CorrectionDto
import me.rerere.companion.data.api.dto.CreatedDto
import me.rerere.companion.data.api.dto.DeviceBindBody
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.api.dto.DeviceRenameBody
import me.rerere.companion.data.api.dto.MemoryDto
import me.rerere.companion.data.api.dto.MemoryUpsert
import me.rerere.companion.data.api.dto.MessageTextUpdate
import me.rerere.companion.data.api.dto.PairCodeDto
import me.rerere.companion.data.api.dto.PairCodeRequest
import me.rerere.companion.data.api.dto.StatusDto
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

/**
 * 4B 用到的全部 REST 接口。SSE 聊天单独返回 [ResponseBody]，由
 * `ChatRepository` 按行解析，不交给 Retrofit 反序列化。
 */
interface CompanionApi {
    @GET("api/assistants")
    suspend fun listAssistants(): List<AssistantDto>

    @GET("api/assistants/{id}")
    suspend fun getAssistant(@Path("id") id: String): AssistantDto

    @POST("api/assistants")
    suspend fun createAssistant(@Body body: AssistantDraft): CreatedDto

    @PUT("api/assistants/{id}")
    suspend fun updateAssistant(
        @Path("id") id: String,
        @Body body: AssistantDraft,
    ): StatusDto

    @DELETE("api/assistants/{id}")
    suspend fun deleteAssistant(@Path("id") id: String): StatusDto

    @GET("api/conversations")
    suspend fun listConversations(
        @Query("assistant_id") assistantId: String? = null,
    ): List<ConversationDto>

    @POST("api/conversations")
    suspend fun createConversation(@Body body: ConversationCreate): CreatedDto

    @GET("api/conversations/{id}")
    suspend fun conversationDetail(@Path("id") id: String): ConversationDetailDto

    @DELETE("api/conversations/{id}")
    suspend fun deleteConversation(@Path("id") id: String): StatusDto

    @POST("api/conversations/{cid}/nodes/{nid}/select")
    suspend fun selectBranch(
        @Path("cid") conversationId: String,
        @Path("nid") nodeId: String,
        @Query("index") index: Int,
    ): StatusDto

    @PUT("api/conversations/{cid}/nodes/{nid}/messages/{mid}")
    suspend fun correctMessage(
        @Path("cid") conversationId: String,
        @Path("nid") nodeId: String,
        @Path("mid") messageId: String,
        @Body body: MessageTextUpdate,
    ): CorrectionDto

    @GET("api/memories")
    suspend fun listMemories(
        @Query("assistant_id") assistantId: String? = null,
    ): List<MemoryDto>

    @POST("api/memories")
    suspend fun createMemory(@Body body: MemoryUpsert): CreatedDto

    @PUT("api/memories/{id}")
    suspend fun updateMemory(
        @Path("id") id: String,
        @Body body: MemoryUpsert,
    ): StatusDto

    @DELETE("api/memories/{id}")
    suspend fun deleteMemory(@Path("id") id: String): StatusDto

    // ---- 4C：设备管理 ----

    @GET("api/devices")
    suspend fun listDevices(): List<DeviceDto>

    @POST("api/devices/pair-code")
    suspend fun createPairCode(@Body body: PairCodeRequest): PairCodeDto

    @PUT("api/devices/bind")
    suspend fun bindDevice(@Body body: DeviceBindBody): StatusDto

    @PUT("api/devices/{id}")
    suspend fun renameDevice(
        @Path("id") id: String,
        @Body body: DeviceRenameBody,
    ): StatusDto

    @DELETE("api/devices/{id}")
    suspend fun revokeDevice(@Path("id") id: String): StatusDto

    @GET("api/actions/catalog")
    suspend fun actionCatalog(): ActionCatalogDto

    @Streaming
    @POST("api/chat/completions")
    suspend fun chat(@Body body: ChatRequestDto): ResponseBody

    /**
     * 可取消的 SSE 调用：`Call.cancel()` 会立即关闭底层 socket，
     * 从而打断阻塞中的 `source.readUtf8Line()`（挂起函数版做不到）。
     */
    @Streaming
    @POST("api/chat/completions")
    fun chatCall(@Body body: ChatRequestDto): Call<ResponseBody>
}
