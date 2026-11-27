package me.rerere.companion.feature.login

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import me.rerere.companion.core.UrlError

@Composable
fun LoginScreen(
    state: LoginUiState,
    onServerUrlChange: (String) -> Unit,
    onUsernameChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Companion", style = MaterialTheme.typography.headlineMedium)
        Text(
            "连接你的桌面机器人服务端",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        OutlinedTextField(
            value = state.serverUrl,
            onValueChange = onServerUrlChange,
            modifier = Modifier.fillMaxWidth(),
            label = { Text("服务器地址") },
            placeholder = { Text("https://example.com") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            isError = state.urlError != null,
            supportingText = state.urlError?.let { error ->
                { Text(urlErrorText(error)) }
            },
        )

        OutlinedTextField(
            value = state.username,
            onValueChange = onUsernameChange,
            modifier = Modifier.fillMaxWidth(),
            label = { Text("用户名") },
            singleLine = true,
        )

        OutlinedTextField(
            value = state.password,
            onValueChange = onPasswordChange,
            modifier = Modifier.fillMaxWidth(),
            label = { Text("管理员口令") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        )

        state.error?.let { message ->
            Text(
                message,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        Button(
            onClick = onSubmit,
            enabled = state.canSubmit && !state.submitting,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                when {
                    state.submitting -> "登录中…"
                    state.retryAfterSeconds > 0 -> "请等待 ${state.retryAfterSeconds} 秒"
                    else -> "登录"
                },
            )
        }
    }
}

private fun urlErrorText(error: UrlError): String = when (error) {
    UrlError.EMPTY -> "请填写服务器地址"
    UrlError.BAD_SCHEME -> "地址必须以 https:// 开头"
    UrlError.CLEARTEXT_NOT_ALLOWED -> "仅调试版允许连接 localhost / 10.0.2.2 的 http 地址"
    UrlError.INVALID_HOST -> "地址格式不正确"
    UrlError.INVALID_PORT -> "端口必须在 1–65535 之间"
    UrlError.INVALID_PATH -> "地址路径不能包含 . 或 .. 片段"
    UrlError.HAS_QUERY_OR_FRAGMENT -> "地址不能包含查询参数或锚点"
    UrlError.HAS_USERINFO -> "地址不能包含用户名或密码"
}
