package me.rerere.companion.core

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 观察系统默认网络是否可用（飞行模式/断网）。
 *
 * 它不代表服务器一定可达（那只能由请求结果判断），但可以保证断网时
 * 写操作控件被禁用，而不是点下去才走一遍超时再弹“无法连接服务器”。
 *
 * 只读状态，不涉及任何用户数据；注册失败（极少数 ROM）时保持当前值。
 */
class ConnectivityMonitor(
    context: Context,
    private val onAvailable: () -> Unit = {},
) {
    private val manager = context.getSystemService(ConnectivityManager::class.java)
    private val _online = MutableStateFlow(manager.hasInternet())
    val online: StateFlow<Boolean> = _online.asStateFlow()

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            refresh()
            onAvailable()
        }

        override fun onCapabilitiesChanged(
            network: Network,
            networkCapabilities: NetworkCapabilities,
        ) {
            refresh()
        }

        override fun onLost(network: Network) {
            refresh()
        }
    }

    fun start() {
        runCatching { manager?.registerDefaultNetworkCallback(callback) }
    }

    fun stop() {
        runCatching { manager?.unregisterNetworkCallback(callback) }
    }

    private fun refresh() {
        _online.value = manager.hasInternet()
    }

    private fun ConnectivityManager?.hasInternet(): Boolean {
        val capabilities = this?.getNetworkCapabilities(this.activeNetwork) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
