package me.rerere.companion.feature.remote

import me.rerere.companion.data.ws.ConnectionState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 遥控门控纯函数：三个条件都必须满足。
 *
 * 急停/滑条不另设函数，直接用 [canUseRemote]——目录是否加载成功不参与判断，
 * 所以这里不再保留「canStop 与 canUseRemote 同值」的恒真断言。
 */
class RemoteScreenGateTest {

    @Test
    fun `remote requires connected socket online device and network`() {
        assertTrue(canUseRemote(ConnectionState.Connected, deviceOnline = true, networkOnline = true))
        assertFalse(canUseRemote(ConnectionState.Stopped, deviceOnline = true, networkOnline = true))
        assertFalse(canUseRemote(ConnectionState.Connecting, deviceOnline = true, networkOnline = true))
        assertFalse(
            canUseRemote(
                ConnectionState.Retrying(attempt = 2, delayMillis = 1_000),
                deviceOnline = true,
                networkOnline = true,
            ),
        )
        assertFalse(canUseRemote(ConnectionState.Connected, deviceOnline = false, networkOnline = true))
        assertFalse(canUseRemote(ConnectionState.Connected, deviceOnline = true, networkOnline = false))
    }

    @Test
    fun `catalog retry needs only the socket and the network`() {
        // 目录来自服务端，与设备是否在线无关：设备离线时也应该能重拉目录。
        assertTrue(canRefreshCatalog(ConnectionState.Connected, networkOnline = true))
        assertFalse(canRefreshCatalog(ConnectionState.Stopped, networkOnline = true))
        assertFalse(canRefreshCatalog(ConnectionState.Connected, networkOnline = false))
    }
}
