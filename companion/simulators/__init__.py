"""可独立运行的设备 / App 模拟器。

- :class:`device_simulator.SimulatedDevice`：实现 `protocol_v2.md` 的设备侧
  行为（握手、动作执行、急停、上报、校准），可单机联调，也可被 pytest 直接驱动。
- :class:`app_simulator.SimulatedApp`：管理员登录、REST 调用与 `/app/ws`
  双向遥控，供集成测试构造「App ↔ Server ↔ 设备」闭环。

模拟器只用于开发与验收，**不能替代真机验收**（尤其音频链路）。
"""

from companion.simulators.app_simulator import SimulatedApp
from companion.simulators.device_simulator import SimulatedDevice

__all__ = ["SimulatedApp", "SimulatedDevice"]
