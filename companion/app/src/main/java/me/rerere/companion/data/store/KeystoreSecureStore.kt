package me.rerere.companion.data.store

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private val Context.sessionDataStore by preferencesDataStore(name = "session")
private val PAYLOAD_KEY = stringPreferencesKey("payload")

/**
 * 服务器地址与 App Token 的加密存储。
 *
 * - 密文格式：`Base64(iv):Base64(ciphertext)`，写入 Preferences DataStore。
 * - 密钥只存在于 AndroidKeyStore，别名 `companion.session`，AES-256-GCM。
 * - 不要求生物认证：App 需要后台重连。
 * - 密钥失效或跨设备恢复导致解密失败时向上抛异常，由 [SessionManager][me.rerere.companion.data.session.SessionManager]
 *   清空后回登录。
 *
 * 真机验证待办（无设备）：首次写入、重启解密、清除 App 数据后重新登录。
 */
class KeystoreSecureStore(private val context: Context) : SecureStore {

    override suspend fun read(): StoredSession? {
        val raw = context.sessionDataStore.data.first()[PAYLOAD_KEY] ?: return null
        val separator = raw.indexOf(SEPARATOR)
        if (separator <= 0 || separator == raw.lastIndex) {
            error("malformed session payload")
        }
        val iv = Base64.decode(raw.substring(0, separator), Base64.NO_WRAP)
        val cipherText = Base64.decode(raw.substring(separator + 1), Base64.NO_WRAP)

        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, existingOrNewKey(), GCMParameterSpec(TAG_BITS, iv))
        val plain = cipher.doFinal(cipherText).toString(Charsets.UTF_8)
        return SessionCodec.decode(plain) ?: error("malformed session json")
    }

    override suspend fun write(session: StoredSession) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, existingOrNewKey())
        val cipherText = cipher.doFinal(SessionCodec.encode(session).toByteArray(Charsets.UTF_8))
        val payload = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) +
            SEPARATOR +
            Base64.encodeToString(cipherText, Base64.NO_WRAP)
        context.sessionDataStore.edit { preferences ->
            preferences[PAYLOAD_KEY] = payload
        }
    }

    override suspend fun clear() {
        context.sessionDataStore.edit { preferences ->
            preferences.remove(PAYLOAD_KEY)
        }
    }

    private fun existingOrNewKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "companion.session"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val TAG_BITS = 128
        const val SEPARATOR = ':'
    }
}
