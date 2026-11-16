# P2.4 外接手柄：可行性结论与方案

> 状态：**可行性已查证，方案待选**。
> 结论先行：**上游接口完备，OHOS 侧有一道真实障碍，但可以绕过。**

---

## 1. 上游：完全就绪，不需要改一行

`dm/host/dm_input.h` 已经提供 8 个玩家、每人 5 个轴、独立的陀螺仪通道：

```c
void dm_pad_set(uint64_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);      // 玩家 1
void dm_pad_set_player(int player, uint64_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);
void dm_pad_players(uint32_t mask);        // bit p: 玩家 p+1 有手柄
int  dm_players_reported(void);            // 游戏实际看到的玩家数
void dm_motion_set(int player, const float acc[3], const float rot[3]);   // 六轴
```

注释还说明了一个重要的产品行为：

> a player whose npad the game does not support feeds player 1 instead, so a one-player game sees every controller

**即：插 4 个手柄进一个单人游戏，每个都能控制玩家 1。** 这是正确的手柄语义。

按钮位和 Switch 的 `nn::hid::NpadButton` 完全对齐（`DM_BTN_A`=bit0 … `DM_BTN_DOWN`=bit15），摇杆 ±32767。

**所以 P2.4 的全部工作都在 ArkTS/OHOS 侧。**

---

## 2. OHOS 侧：一道真实障碍

### 2.1 查证结果

| 接口 | 有没有手柄轴数据 |
|---|---|
| ArkTS `@ohos.multimodalInput.inputDevice` | ❌ 能识别 `'joystick'` 设备类型，**只给设备信息，不给轴值** |
| ArkTS `@ohos.multimodalInput.keyEvent` | ❌ 只有 `code: KeyCode`，无 axis |
| ArkTS `@ohos.multimodalInput.inputConsumer` | ❌ 只有 hotkey 和 keyPressed |
| ArkTS 全库搜 `axisEvent` / `absAxis` | ❌ **零结果** |
| **NDK `oh_input_manager.h`** | ⚠️ 有 `Input_AxisEvent` 和 `OH_Input_GetAxisEventAxisValue()` |

### 2.2 但 AxisEvent 的类型只有四种

`oh_axis_type.h` 的完整枚举：

```c
AXIS_TYPE_UNKNOWN,
AXIS_TYPE_SCROLL_VERTICAL,      // 鼠标滚轮
AXIS_TYPE_SCROLL_HORIZONTAL,    // 鼠标横向滚动
AXIS_TYPE_PINCH,                // 双指捏合
AXIS_TYPE_ROTATE                // 双指旋转
```

**没有 `AXIS_TYPE_JOYSTICK_X/Y` 之类的摇杆轴。** 事件类型也只有 `PINCH` 和 `SCROLL` 两种。

**结论：OHOS 的输入系统目前不把手柄摇杆作为轴事件暴露给应用。** 手柄按键（`KeyCode`）可以收到，摇杆不行。

---

## 3. 三条可行路径

### 路径 A：手柄按键映射到虚拟按键（**现在就能做**）

**原理**：OHOS 把外接手柄的**按键**映射成标准 `KeyCode`（`KEYCODE_BUTTON_A` 等）。`Index.ets` 的 `onKeyEvent` 已经能收到，映射到 `dm_pad_set` 即可。

| 手柄按键 | OHOS KeyCode | Switch 位 |
|---|---|---|
| A | 2017 (`KEYCODE_BUTTON_A`) | `DM_BTN_A` |
| B | 2018 | `DM_BTN_B` |
| X | 2019 | `DM_BTN_X` |
| Y | 2020 | `DM_BTN_Y` |
| L1/R1 | 2021/2022 | `DM_BTN_L`/`R` |
| L2/R2 | 2023/2024 | `DM_BTN_ZL`/`ZR` |
| Start | 2025 | `DM_BTN_PLUS` |
| Select | 2026 | `DM_BTN_MINUS` |
| 十字键 | 2012–2015 | `DM_BTN_UP`…`DOWN` |

**摇杆**：多数手柄的摇杆也能被系统映射成方向键（部分设备/手柄支持），或干脆不做。

**代价**：摇杆不可用，或只能当八向方向键。
**工作量**：**半天**（映射表 + 一个 `dm_pad_set` 调用）。
**风险**：低。

### 路径 B：等 OHOS 暴露摇杆轴（**不可控**）

需要华为在 `oh_axis_type.h` 里加摇杆轴枚举。我们无法推进。

### 路径 C：直接读 `/dev/input/event*`（**需要权限**）

Linux 层的 evdev 有完整的摇杆轴（`ABS_X`、`ABS_Y`、`ABS_RX`、`ABS_RY`）。但：
- 需要 `ohos.permission.READ_INPUT_STATE` 或 root
- 普通应用拿不到

**代价**：需要系统级权限，公开发布不可行。

---

## 4. 建议：走路径 A，但先验证

**理由**：
1. 手柄按键覆盖了绝大多数操作（BOTW 除了视角控制，全部按键可用）
2. 半天工作量，无风险
3. 上游 `dm_pad_set` 现成，不用改 native
4. 摇杆缺失可以后续用路径 C 或等 OHOS 补上

**必须先验证一件事**：OHOS 设备上外接手柄的按键**是否真的作为 KeyCode 送到应用**。这需要：
- 一个真实手柄（USB 或蓝牙）
- 或者用 `hdc shell uinput` 模拟按键

---

## 5. 实现清单（路径 A）

| # | 内容 |
|---|---|
| 1 | `Index.ets` 的 `keyMap` 加手柄 KeyCode → `DM_BTN_*` 映射 |
| 2 | 手柄按键走 `dm_pad_set`（独立于虚拟按键的 `dmhost.setPad`） |
| 3 | 设置页显示「已连接手柄」状态（`inputDevice.on('change')`） |
| 4 | 手柄断开时清空其状态（避免卡键） |

### 验收标准

1. 手柄插上后，按键能控制游戏
2. 拔掉手柄不会卡键
3. 虚拟按键和手柄可以同时用
4. 设置页能看到手柄连接状态

---

## 6. 必须先确认

**你的设备上有手柄吗？** 这决定 P2.4 能不能真机验证。

- **有** → 走路径 A，半天做完并验证
- **没有** → 可以先写代码（映射表 + 状态显示），但无法验证按键是否真的到达；也可以先做别的（P2.5 发布、P3.1 60fps）
