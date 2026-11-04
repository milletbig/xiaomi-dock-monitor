# 小米十合一扩展坞监控（Xiaomi Dock Monitor）

一个 Windows 小工具：实时显示小米 Type-C 十合一扩展坞的**接口占用、设备身份、USB 协商速率、千兆网口与存储吞吐、供电状态**。

> 状态：`v0.1.0` PoC / 可运行
> 平台：Windows 10 / 11（x64）
> 技术栈：Python 3.10+ / Tkinter / ctypes（注册表枚举 + USB Hub IOCTL）

![界面截图](docs/screenshot.png)

## 它显示什么

- **10 个物理接口的状态卡片**：USB-A ×3、USB-C 数据、PD、HDMI、RJ45、SD、TF、3.5mm 音频
- **每个 USB 口的设备名、VID/PID、链路速率**（1.5Mbps / 12Mbps / 480Mbps / 5Gbps）
- **网口实时收发速率**（RTL8153 等 USB 网卡）
- **可移动存储（SD/TF）状态**
- **显示输出 / 音频设备状态**
- **供电状态与功率规格**：PD 100W 输入、90W 输出、单口 5V⎓1.5A、外设预算约 10W

## 快速开始

```bat
cd E:\GitHub\xiaomi-dock-monitor
run.bat
```

或者：

```bat
cd E:\GitHub\xiaomi-dock-monitor
python -m pip install -e .
python -m dockmon
```

命令行查看当前快照（不开界面）：

```bat
run.bat --dump
```

## 它是怎么拿数据的

| 数据 | 来源 |
|---|---|
| Hub / 端口连接状态 | 注册表枚举 `Enum\USB`，打开 `GUID_DEVINTERFACE_USB_HUB`，调用 `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX` |
| 端口上的设备实例 | `IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME` → 注册表 `Driver` 值映射 |
| 协商速率 | `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2` 的 `Flags` + `EX` 的 `Speed` |
| 设备名称/类别 | `Enum\USB` 下的 `FriendlyName` / `DeviceDesc` / `ClassGUID` |
| 网口吞吐 | `psutil.net_io_counters()`；网卡名称由一次 `Get-NetAdapter` 探测得到 |
| 供电状态 | `psutil.sensors_battery()`；功率数字为产品规格，**不是实测** |
| 显示输出 | `EnumDisplayDevicesW` |
| SD/TF | `GetLogicalDrives` + `GetDriveTypeW` |

## 重要限制

1. **功率不可实测**。这个扩展坞没有电量计/遥测固件，Windows 也不向普通应用暴露 PD 合同。UI 中的“功率”全部是**规格上限**。
2. **HDMI 不在 USB 树里**。它走 DP Alt Mode，只能通过显示子系统推断“有没有外接显示”。
3. **SD/TF 与音频是系统级推断**。可移动盘可能是 U 盘；音频只检测 USB 音频设备。
4. **USB 设备到物理口的映射是自动启发式**。10 合 1 的 3×USB-A + 1×USB-C 与内部 Hub 端口没有公开对应表，默认按 Hub 端口顺序分配；可通过后续配置文件修正。
5. **USB 每口“实时吞吐”尚未实现**。v0.1 给出协商速率 + 网口实时吞吐；每口 USB 吞吐需要 ETW（见 `docs/TODO.md` 的 P1）。

## 文档

- [待办与可行性清单](docs/TODO.md)
- [可行性分析](docs/feasibility.md)

## License

MIT
