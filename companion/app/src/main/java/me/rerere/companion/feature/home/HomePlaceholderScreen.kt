package me.rerere.companion.feature.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * 4A 占位主页：只验证“会话存在 → 进入主界面 / 退出 → 回登录”的导航链路。
 * 4B 会替换为角色列表。
 */
@Composable
fun HomePlaceholderScreen(
    serverUrl: String,
    onLogout: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("已登录", style = MaterialTheme.typography.headlineSmall)
        Text(serverUrl, style = MaterialTheme.typography.bodyMedium)
        Button(onClick = onLogout) {
            Text("退出登录")
        }
    }
}
