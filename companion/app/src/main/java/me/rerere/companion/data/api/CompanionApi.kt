package me.rerere.companion.data.api

import me.rerere.companion.data.api.dto.ActionCatalogDto
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.api.dto.AssetConfirmBody
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.data.api.dto.AssetViewDto
import me.rerere.companion.data.api.dto.AuthTokenDto
import me.rerere.companion.data.api.dto.BackupConfirmBody
import me.rerere.companion.data.api.dto.BackupPreviewDto
import me.rerere.companion.data.api.dto.BackupResultDto
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
import me.rerere.companion.data.api.dto.SecretsUpdateBody
import me.rerere.companion.data.api.dto.SettingsDto
import me.rerere.companion.data.api.dto.SettingsUpdateBody
import me.rerere.companion.data.api.dto.StatusDto
import okhttp3.MultipartBody
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
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

    // ---- 4D：设置 / Token ----

    @GET("api/settings")
    suspend fun getSettings(): SettingsDto

    @PUT("api/settings")
    suspend fun updateSettings(@Body body: SettingsUpdateBody): SettingsDto

    @PUT("api/settings/secrets")
    suspend fun updateSecrets(@Body body: SecretsUpdateBody): SettingsDto

    @DELETE("api/settings/secrets/{key}")
    suspend fun clearSecret(@Path("key") key: String): SettingsDto

    @GET("api/auth/tokens")
    suspend fun listTokens(): List<AuthTokenDto>

    @POST("api/auth/tokens/{id}/revoke")
    suspend fun revokeToken(@Path("id") id: String): StatusDto

    // ---- 4D：备份 ----

    @Multipart
    @POST("api/backup/preview")
    suspend fun previewBackup(@Part file: MultipartBody.Part): BackupPreviewDto

    @POST("api/backup/confirm")
    suspend fun confirmBackup(@Body body: BackupConfirmBody): BackupResultDto

    @Streaming
    @GET("api/backup/export")
    suspend fun exportBackup(): ResponseBody

    // ---- 4D：素材包 ----

    @GET("api/assets")
    suspend fun getAssets(): AssetViewDto

    @Multipart
    @POST("api/assets/preview")
    suspend fun previewAsset(@Part file: MultipartBody.Part): AssetUploadDto

    @POST("api/assets/confirm")
    suspend fun confirmAsset(@Body body: AssetConfirmBody): AssetViewDto

    @POST("api/assets/rollback")
    suspend fun rollbackAssets(): AssetViewDto

    @Streaming
    @GET("api/assets/{packageId}/frame/{index}")
    suspend fun assetFrame(
        @Path("packageId") packageId: String,
        @Path("index") index: Int,
    ): ResponseBody

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
