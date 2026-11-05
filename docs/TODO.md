# 待办与可行性清单

> 原则：先做“Windows 能拿到、且能验证”的数据；拿不到的（真实功率）不做假数据，只显示规格上限。
> 验证环境：Windows 11 + 小米 VID_2717 USB-C Hub（USB3.1/USB2.1 双 Hub，下游 RTL8153 千兆网卡）。
> 物理口编号：1=供电 Type-C，2=Type-C 数据，3–5=USB-A 1–3；详见 `docs/port-mapping.md`。

## 一、已确认可执行（v0.1 范围）

| ID | 待办事项 | 可行性 | 验证依据 | 状态 |
|---|---|---|---|---|
| M0 | 仓库脚手架：README / LICENSE / pyproject / run.bat / CI | ✅ 可执行 | 本仓库已初始化 | ✅ 完成 |
| M1 | 枚举外部 USB Hub（含 SymbolicName、VID/PID） | ✅ 可执行 | 注册表 `Enum\USB` + `Device Parameters\SymbolicName`；实测枚举到 4 个小米 Hub | ✅ 完成 |
| M2 | 读取每个 Hub 端口连接状态 / 速度 / 设备描述符 | ✅ 可执行 | `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX` / `_V2`；实测读到 4 口状态与 5Gbps 标志 | ✅ 完成 |
| M3 | 端口 → Windows 设备实例映射 | ✅ 可执行 | `IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME` + 注册表 `Driver` 值；实测映射到 RTL8153/HID | ✅ 完成 |
| M4 | 解析设备名 / 类别（Net、DiskDrive、Media、HID…） | ✅ 可执行 | `FriendlyName` / `DeviceDesc` / `ClassGUID`；实测读到 “Realtek USB GbE Family Controller” | ✅ 完成 |
| M5 | 解析协商速率（1.5M / 12M / 480M / 5Gbps） | ✅ 可执行 | `EX_V2.Flags` 的 `DeviceIsOperatingAtSuperSpeedOrHigher` + `EX.Speed` | ✅ 完成 |
| M6 | 网口状态 + 实时收发速率 | ✅ 可执行 | 一次 `Get-NetAdapter` 定位网卡，之后 `psutil.net_io_counters()` 差分 | ✅ 完成 |
| M7 | 供电状态（AC / 电池）与功率规格展示 | ✅ 可执行（规格） | `psutil.sensors_battery()`；功率为产品规格，非实测 | ✅ 完成 |
| M8 | HDMI / 可移动存储 / USB 音频的“系统级推断” | ✅ 可执行（推断） | `EnumDisplayDevicesW`、`GetDriveTypeW`、`Win32_SoundDevice` | ✅ 完成 |
| M9 | Tkinter 主界面：10 个接口卡片 + 汇总 + 刷新 | ✅ 可执行 | 本仓库 `src/dockmon/ui.py` | ✅ 完成 |
| M10 | CLI `--dump` 输出 JSON 快照，便于排障 | ✅ 可执行 | `python -m dockmon --dump` | ✅ 完成 |
| M11 | 单元测试 + GitHub Actions（Windows） | ✅ 可执行 | `python -m unittest discover -s tests` | ✅ 完成 |

## 二、后续可执行（按优先级）

| ID | 待办事项 | 可行性 | 说明 | 状态 |
|---|---|---|---|---|
| P1 | 每个 USB 口的实时吞吐（MB/s） | 🟡 可行但复杂 | 用 USB ETW（`Microsoft-Windows-USB-USBHUB3` / `USBPORT` / `UCX` / `USBXHCI`）按设备地址统计；需管理员、1s 聚合、有 CPU 开销 | ⏳ 待办 |
| P2 | 物理口 ↔ Hub 端口映射配置 | ✅ 可执行 | 已按“供电 C=1、数据 C=2、USB-A=3–5”编号；`config/port_map.json` 记录实测锚点（C→顶层 USB2#4、A1→顶层 SS#2、A2→SS 子Hub#4、A3→SS 子Hub#3 + USB2 子Hub#3）；2/3/4 的另一速率侧待标定 | 🟡 进行中 |
| P3 | SD/TF 读写速率 | ✅ 可执行 | `psutil.disk_io_counters(perdisk=True)` + `Get-Disk`/`Get-Partition` 将盘符映射到 PhysicalDrive | ⏳ 待办 |
| P4 | 托盘模式 / 开机自启 / 单实例 | ✅ 可执行 | `pystray` 或 Win32 托盘 API；避免每次弹主窗口 | ⏳ 待办 |
| P5 | 打包成单文件 exe | ✅ 可执行 | PyInstaller（`--noconsole`），附带版本信息 | ⏳ 待办 |
| P6 | 历史曲线与导出 CSV | ✅ 可执行 | 采样环形缓冲 + 简单折线图 | ⏳ 待办 |
| P7 | 多扩展坞/多品牌支持 | ✅ 可执行 | 目前按小米 VID `2717` 识别；可扩展为任意外部 Hub 组 | ⏳ 待办 |
| P8 | 真实每口功率 / PD 电压电流 | ❌ 软件不可行 | 扩展坞无电量计、无厂商遥测接口；需外接 USB-C 功率计或硬件改造 | ❌ 不做 |

## 三、验收标准（v0.1）

1. 插入/拔出任意 USB 设备，界面 1–2 秒内更新对应端口状态与设备名。
2. 5Gbps 设备显示 `5 Gbps`，USB 2.0 设备显示 `480 Mbps`，全速设备显示 `12 Mbps`。
3. 接上网线后，网口卡片显示链路速率，并持续显示 RX/TX 实时速率。
4. 未检测到扩展坞时，界面明确提示“未检测到小米扩展坞”，而不是显示空白。
5. 所有功率字段均带“规格/上限”标注，不出现伪造的实测瓦数。
6. `python -m unittest discover -s tests` 全部通过；`--dump` 能输出合法 JSON。

## 四、已知限制

- 端口状态 IOCTL 通常需要管理员权限；非管理员可能只能看到部分信息。
- 设备热插拔会造成短暂重枚举，界面可能出现 1 个刷新周期的空白。
- SD/TF 通过“可移动盘”推断，U 盘会被误计入；后续用读卡器设备实例修正。
- HDMI 走 DP Alt Mode，不在 USB 树中；只能推断“有外接显示”，无法读 HDMI 链路速率。
- 音频只检测 USB 音频设备，3.5mm 插孔检测取决于编解码器能力。
