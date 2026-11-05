# 物理接口编号与 Hub 端口映射

> 目标：把 Windows USB 树里的“第几个 Hub、第几号端口”翻译成用户能看到的物理接口编号，避免按枚举顺序把设备放到错误卡片上。

## 一、编号定义

| 物理编号 | 接口 | 连接器 | 当前映射 | 状态 |
|---|---|---|---|---|
| 1 | 供电 Type-C（PD） | Type-C | 不进 USB 树，读取系统供电状态 | ✅ 已确认 |
| 2 | Type-C 数据 | Type-C | 顶层 USB2 Hub 端口 4 | 🟡 已标定 USB2 侧，USB3 侧待标定 |
| 3 | USB-A 1 | USB-A | 顶层 SuperSpeed Hub 端口 2 | 🟡 已标定 USB3 侧，USB2 侧待标定 |
| 4 | USB-A 2 | USB-A | SS 子Hub 端口 4 + USB2 子Hub 端口 4 | ✅ 两侧已标定 |
| 5 | USB-A 3 | USB-A | SS 子Hub 端口 3 + USB2 子Hub 端口 3 | ✅ 两侧已标定 |

说明：沟通中提到的“Type-B”按产品规格理解为 **USB-A**（官方参数为 3×USB-A + 1×USB-C 数据 + PD）。若实际接口类型不同，只需修改 `config/port_map.json` 的 `label` / `connector` 字段，编号不变。

## 二、本机实测依据

当前扩展坞会枚举出 4 个小米 Hub：

| Hub 实例 | 角色 | 说明 |
|---|---|---|
| `USB\VID_2717&PID_50BA\6&24176d9b&0&5` | SuperSpeed（SS） | 直连根 Hub；端口 2 = 物理 3（USB-A 1），端口 3/4 待标定 |
| `USB\VID_2717&PID_50BB\6&24176d9b&0&1` | USB2 | 直连根 Hub；端口 4 = 物理 2（Type-C 数据），端口 2/3 待标定 |
| `USB\VID_2717&PID_50BA\7&2ae5b31f&0&1` | SS 子 Hub | 端口 2 = RTL8153 千兆网卡；端口 3 = 物理 5（USB3 侧）；端口 4 = 物理 4（USB3 侧） |
| `USB\VID_2717&PID_50BB\7&80fb6c5&0&1` | USB2 子 Hub | 端口 3 = 物理 5（USB2 侧）；端口 4 = 物理 4（USB2 侧）；其余端口待标定/内部设备 |

实测对应关系：

- **物理 2（Type-C 数据）**：手表充电器 `05AC:0503` 与中兴手机 `19D2:0307` 均出现在 `顶层 USB2 Hub 端口 4`；USB3 侧待标定。
- **物理 3（USB-A 1）**：移动硬盘 `152D:0583`，接此口时出现在 `顶层 SuperSpeed Hub 端口 2`。
- **物理 5（USB-A 3）**：移动硬盘 `152D:0583` 在 5 口时出现在 `SS 子Hub 端口 3`；16GB U 盘 `1609:3A04` 在 5 口时出现在 `USB2 子Hub 端口 3`。因此 5 口两侧均已标定。
- **物理 4（USB-A 2）**：移动硬盘 `152D:0583` 在 4 口时出现在 `SS 子Hub 端口 4`；16GB U 盘 `1609:3A04` 在 4 口时出现在 `USB2 子Hub 端口 4`。因此 4 口两侧均已标定。

因此不能再按“Hub 实例排序 + 端口排序”自动填 4 个 USB 卡片；v0.1 会把设备填到错误卡片，和物理丝印不一致。

## 三、映射规则

1. 顶层 Hub：实例 ID 后缀以 `6&` 开头；子 Hub：实例 ID 后缀以 `7&` 开头。
2. 顶层 Hub 和子 Hub 都可能承载外部接口，例如：
   - 顶层 SS Hub 端口 2 → 物理 3；
   - SS 子Hub 端口 3 → 物理 5（USB3 侧）；
   - SS 子Hub 端口 4 → 物理 4（USB3 侧）；
   - USB2 子Hub 端口 3 → 物理 5（USB2 侧）；
   - 顶层 USB2 Hub 端口 4 → 物理 2（USB2 侧）。
3. 子 Hub 上的 LAN / 读卡器 / 音频设备按设备类别排除，不参与物理口映射。
4. 顶层 Hub 按 `PID` / 描述区分角色：
   - `PID_50BA` 或描述含 `SuperSpeed` → `ss`
   - `PID_50BB` → `usb2`
5. 每个物理接口可以配置一个或多个 `hub_ports`，用 `level` 区分 `top` / `nested`：USB3 设备会出现在 `ss` 侧，USB2 设备会出现在 `usb2` 侧，两侧端口号可以不同。
6. 未在配置中出现的端口不会被强行塞进某个 USB-A 卡片，而是作为“未标定”显示在界面“数据说明”区域，并写入 `--dump` 的 `unmapped_usb`。

## 四、配置文件

文件：`config/port_map.json`

```json
{
  "number": 5,
  "id": "usb_a3",
  "label": "USB-A 3",
  "connector": "usb_a",
  "hub_ports": [
    { "role": "ss", "level": "nested", "port": 3 }
  ],
  "evidence": "实测：移动硬盘..."
}
```

- `role` 只能是 `ss` 或 `usb2`。
- `level` 只能是 `top`（顶层 Hub）或 `nested`（子 Hub），省略时默认 `top`。
- `port` 是 Hub 下游端口号（从 1 开始）。
- 编号必须连续为 1–5，`id` 固定为 `pd`、`usb_c`、`usb_a1`、`usb_a2`、`usb_a3`。
- 配置缺失或格式错误时使用内置默认表，并在界面显示警告。

## 五、后续标定步骤（剩余速率侧）

1. 运行 `run.bat --dump`（或 `python -m dockmon --dump`）。
2. 把已知设备插入待标定的物理接口（例如 USB-A 2）。
3. 在 `--dump` 输出中查看：
   - `unmapped_usb`：未映射设备的 `hub_role`、`hub_level`、`hub_port`、`vid_pid`；
   - `hubs[].ports`：对应 Hub 的端口连接状态。
4. 若设备是 USB3，记下 `SS Hub #n` 或 `SS 子Hub #n`；若设备是 USB2，记下 `USB2 Hub #n` 或 `USB2 子Hub #n`。
5. 在 `config/port_map.json` 的对应接口 `hub_ports` 中追加该键，刷新界面确认设备落到正确卡片。
6. 同一个物理接口的 USB3 / USB2 两侧都标定后，把 `evidence` 和状态改为已确认。

建议标定顺序：

- 用 USB2 设备插 USB-A 1，补物理 3 的 USB2 侧（USB3 侧已有锚点）；
- 用 USB3 设备插 Type-C 数据口，补物理 2 的 SS 侧；
- 剩余速率侧补齐后，把对应接口状态改为“两侧已标定”。

## 六、当前限制

- 物理 4、5 两侧均已标定。
- 物理 2/3 目前只标定了一侧；插入另一类速率的设备时可能显示为“未标定”，按上表补齐即可。
- 端口号由扩展坞内部布线决定，不保证与物理丝印连续；必须以插拔实测为准。
