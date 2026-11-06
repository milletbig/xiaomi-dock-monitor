# 可行性分析：小米十合一扩展坞 Windows 实时监控

## 结论

**接口占用、设备身份、USB 协商速率、网口/存储吞吐、显示与供电状态可以实现；每个接口的真实功率（W）与 PD 电压/电流无法通过纯软件实现。**

本项目因此定位为“**拓扑 + 状态 + 可测量吞吐**”的监控面板，功率部分只展示规格上限，并明确标注。

## 1. 官方产品参数（来源：mi.com 商品页 `product_id=21150`）

| 项目 | 规格 |
|---|---|
| 上行 | Type-C 连接线：USB 3.0 + DP1.4；无 PD 时 5V⎓3A MAX |
| PD 输入 | 20V⎓5A（100W MAX），仅供电，不支持数据 |
| 对电脑输出 | 20V⎓4.5A（90W MAX），扩展坞/外设预留约 10W |
| USB | 3×USB-A + 1×USB-C，USB 3.0 5Gbps，单口 5V⎓1.5A MAX，支持 BC1.2；4 口共享 5Gbps |
| HDMI | HDMI 2.0，最高 4K@60Hz（DP Alt Mode） |
| 网口 | RJ45 千兆自适应 |
| 卡槽 | SD + TF，最高 104MB/s，双槽不可同时读 |
| 音频 | 3.5mm，支持麦克风与线控 |
| 十口 | 3×USB-A + USB-C 数据 + PD + HDMI + TF + SD + LAN + Audio |

## 2. 数据可得性

| 信息 | 可行性 | 依据 |
|---|---|---|
| 端口是否连接、设备 VID/PID | ✅ | USB Hub 用户态 IOCTL，`GUID_DEVINTERFACE_USB_HUB` |
| 端口对应设备名/类别 | ✅ | `IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME` → 注册表 `Driver` 映射 |
| 协商速率 | ✅ | `IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2` 的标志位 |
| 网口实时吞吐 | ✅ | `psutil.net_io_counters()` 差分 |
| HDMI 是否接显示器 | ✅ 推断 | `EnumDisplayDevicesW` |
| SD/TF 是否插卡 | ✅ 推断 | `GetDriveTypeW` 可移动盘 |
| 每口真实功率 | ❌ | 无电量计；Hub 描述符无电流字段；PD 合同不暴露给用户态 |
| PD 输入/输出功率 | ❌ | UCSI 只面向主机自身 Type-C 口，且非普通应用 API |

## 3. 为什么功率拿不到

1. USB Hub 协议没有“端口电流”字段，只有设备申请上限 `bMaxPower`（且在本项目使用的 IOCTL 路径中拿不到实测值）。
2. PD 协商在扩展坞的 PD 控制器内部完成，Windows 只看到“电脑在充电”。
3. 该扩展坞免驱、无厂商管理软件、无自定义 HID 遥测接口。
4. 想测真实功率，只能在 PD 输入端串 USB-C 功率计，或换带电流检测的扩展坞。

## 4. 已在本机验证的事实

- 枚举到小米 `VID_2717` 的 USB3.1 / USB2.1 Hub，共 4 个。
- 读取到端口 1/2/4 的连接状态、设备描述符、设备地址。
- SS 设备的 `EX_V2.Flags` 返回 `0x00000003`（Operating/Capable SuperSpeed），对应 5Gbps。
- 通过驱动键名成功映射到 `RTL8153` 千兆网卡与 HID 键盘设备实例。
- 物理口映射已得到八个实测锚点，5 个接口全部标定：Type-C 数据口（编号 2）→ 顶层 SuperSpeed Hub 端口 3（USB3 侧）与顶层 USB2 Hub 端口 4（USB2 侧）；USB-A 1（编号 3）→ 顶层 SuperSpeed Hub 端口 2（USB3 侧）与顶层 USB2 Hub 端口 2（USB2 侧）；USB-A 2（编号 4）→ SS 子Hub 端口 4 与 USB2 子Hub 端口 4；USB-A 3（编号 5）→ SS 子Hub 端口 3 与 USB2 子Hub 端口 3（见 `docs/port-mapping.md`）。
- `Microsoft-Windows-USB-USBHUB3` / `USBPORT` / `UCX` / `USBXHCI` ETW Provider 在本机存在，为 P1 的每口吞吐提供基础。

## 5. 参考

- [IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/usbioctl/ni-usbioctl-ioctl_usb_get_node_connection_information_ex_v2)
- [UCSI 驱动说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/ucsi)
- [小米商城商品页](https://www.mi.com/shop/buy/detail?product_id=21150)
