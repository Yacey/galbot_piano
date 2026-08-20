---
# 灵巧手弹奏项目 - 工作日志

> 最后更新：2026-08-20（茉莉花已整合手部移位功能：低8度5/6、高8度2· 均通过移动手实现）

## 一、项目目标

让 Galbot G1 灵巧手（RH56 仿人五指）通过 Python 脚本自动弹奏乐谱。

- 第一首：**《小星星》** ✅ 已完成（main_extended.py）
- 第二首：**《茉莉花》** ✅ 可弹奏（molihua_hand_move.py，部分段落起代表高八度 Do，拇指按住在 600 位置）
- 拇指两个关节全程固定：旋转=0，弯曲=1000
- Ctrl+C 安全退出：检测中断信号、重置手部、逐步释放资源

## 二、文件清单

| 文件路径 | 状态 | 用途 |
|---|---|---|
| `E:\baida-code\tutorials\hand-test\music\test\main.py` | 参考 | 原始 apscheduler 版 |
| `E:\baida-code\tutorials\hand-test\music\test\flash_galbot_mvp.py` | 参考 | 原始 16KB 版（最完整） |
| `E:\baida-code\tutorials\hand-test\music\test\flash_galbot_mvp_asyncio.py` | 参考 | asyncio 改造（17KB） |
| `E:\baida-code\tutorials\hand-test\music\test\flash_galbot_merged.py` | 参考 | 融合版（4.5KB） |
| **`E:\baida-code\tutorials\hand-test\music\test\main_extended.py`** | **✅ 完成** | **小星星最终版**（apscheduler） |
| **`E:\baida-code\tutorials\hand-test\music\test\molihua_hand_move.py`** | **✅ 完成** | **茉莉花**（Timer 手动调度） |
| **`E:\baida-code\tutorials\hand-test\music\test\WORK_LOG.md`** | **✅ 完成** | **本项目工作日志** |

### 部署路径

```
# 部署目录（Linux 调试机）
/userdata/cx/tutorials/main_extended.py
/userdata/cx/tutorials/molihua_hand_move.py
```

## 三、关键架构

### SDK 接口

```python
from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand

robot = GalbotRobot()
robot.init()                                       # 连真机
status = robot.set_dexhand_command(
    end_effector="left_dexhand",                   # 或 "right_dexhand"
    dexhand_command=[JointCommand(position=p) for ...],  # 6 元素列表
    is_blocking=False,
)
```

### 笔记->手指->位置映射（NOTE_MAP）

小星星原版：
| 简谱 | 音 | 手指 | 关节 | 位置 |
|---|---|---|---|---|
| 1 | C4 | 左小拇指 | 5 | 760 |
| 2 | D4 | 左无名指 | 4 | 830 |
| 3 | E4 | 左中指 | 3 | 850 |
| 4 | F4 | 左食指 | 2 | 830 |
| 5 | G4 | 右食指 | 2 | 830 |
| 6 | A4 | 右中指 | 3 | 850 |
| 7 | B4 | 右无名指 | 4 | 830 |

茉莉花版（molihua_hand_move.py）额外包含：
| 简谱 | 代码 | 应该含义 |
|---|---|---|
| `5·` 高八度 5（C5） | `5` （未处理，暂用低八度弹）| 需手部移动 |
| `6·` 高八度 6（A5） | `6` （未处理，暂用低八度弹） | 需手部移动 |
| `1·` 高八度 Do（C5） | `8` → 右小拇指，position=600 | 高八度 Do 已处理 |

> 高八度视为同一手指的高位押住，实际音高受手部校准影响。拇指的位置是按“拇指旋转”位置而定（600 = 高八度 Do）。

### 事件格式演化

```python
# 小星星：3-tuple = 1 拍，4-tuple + True = 2 拍
("left_dexhand", 5, 760)            # 普通
("left_dexhand", 5, 760, True)       # 延长音

# 茉莉花：3-tuple = (note, duration) 或 (note, duration, True)
(3, 1)                              # 1 拍
(5, 2)                              # 2 拍
(1, 1, True)                        # 1· 延 1 拍
(0, 1)                              # 1 拍休止
```

**True 的含义**：
- `延音一拍`（hold 该音一个拍）
- 对应简谱的 `延音线 (X -)` 或 `连音线 (X 〈 Y)`中的 X
- 可与高八度配合，如 `(13, 1, True)` = `高八度 6· -`

### 拇指两个关节全程固定（重要！）

```python
def generateDexhandCommands(joint_idx, joint_position):
    cmds = [JointCommand() for _ in range(6)]
    # 拇指两个关节固定
    cmds[0].position = 0      # 拇指旋转 (Joint 1) → 全程 0
    cmds[1].position = 1000   # 拇指弯曲 (Joint 6) → 全程 1000
    # 其他 4 个手指默认 1000
    for i in [2, 3, 4, 5]:
        cmds[i].position = 1000
    if joint_idx not in (0, 1):
        cmds[joint_idx].position = joint_position
    return cmds
```

无论是弹奏前、弹奏后，还是 Ctrl+C 退出，拇指两个关节都保持在固定值（拇旋转=0，拇弯曲=1000）。

## 四、核心调度逻辑

### 两种调度方案对比

| 项目 | main_extended.py | molihua_hand_move.py |
|---|---|---|
| 调度器 | apscheduler (BackgroundScheduler) | **threading.Timer** 手动调度 |
| 触发频率 | INTERVAL=0.7s | INTERVAL=0.7s |
| 下一个事件延迟 | 下一个 INTERVAL 跳住 | duration * INTERVAL - actual_time |
| 优点 | 简单 | 能跨越事件（1拍、5拍）、不跳事件 |
| 缺点 | hit_next 长时间会跳 fire | 需手动管理 Timer |
| 连音效果 | 不支持（gap=0.15s） | 支持（0.5 拍间隔 0.35s） |

### molihua_hand_move.py 的 hit_next 逻辑

```python
def hit_next(robot):
    global idx
    if idx >= MAX or shutdown_event.is_set():
        if idx >= MAX:
            finished.set()
        return

    event = SCORE[idx]
    if len(event) == 2:
        note, duration = event
        is_extended = False
    else:
        note, duration, is_extended = event

    target_press = duration * RESET_DELAY  # 不再“补足”
    actual_time = target_press

    if note != 0:
        end_effector, joint_idx, position = NOTE_MAP[note]
        hit(robot, end_effector, generateDexhandCommands(joint_idx, position))
        if target_press < RESET_DELAY:
            time.sleep(target_press)                  # 短音符：保留连音
            hit(release)
        else:
            time.sleep(RESET_DELAY)
            if target_press > RESET_DELAY:
                time.sleep(target_press - RESET_DELAY)
            if is_extended:
                time.sleep(RESET_DELAY)
                hit(release)
                actual_time += RESET_DELAY
            else:
                hit(release)
    else:
        time.sleep(target_press)

    idx += 1

    if idx >= MAX:
        finished.set()
        return

    if not shutdown_event.is_set():
        # 用拍数算下次触发时间
        delay = max(0.001, duration * INTERVAL - actual_time)
        timer = threading.Timer(delay, hit_next, args=(robot,))
        timer.daemon = True
        timer.start()
```

**连音 vs 原节奏同时满足的魔法：**

| duration | actual_time | 下次触发间隔 | 音乐意义 |
|---|---|---|---|
| 0.5 拍 | 0.275s | 0.5*0.7 - 0.275 = 0.075s 后 | 0.35s 后（**连音**） |
| 1 拍 | 0.55s | 1*0.7 - 0.55 = 0.15s 后 | 0.7s 后（**原节奏**） |
| 2 拍 | 1.1s | 2*0.7 - 1.1 = 0.001s 后 | 1.1s 后（受众位报） |

## 五、Ctrl+C 安全退出（重要！）

两个文件都实现了：

```python
# 全局变量
shutdown_event = threading.Event()  # 跨线程传递关闭信号

# 辅助函数（两个文件一致）
def reset_hands(robot: GalbotRobot):
    """重置双手为开启状态（拇旋转=0，其他关节=1000）。"""
    reset_cmds = generateDexhandCommands(0, 1000)
    hit(robot, "left_dexhand",  reset_cmds)
    hit(robot, "right_dexhand", reset_cmds)

# main 中的 Ctrl+C 处理
try:
    reset_hands(robot)                    # 初始姿势
    
    while not finished.is_set() and not shutdown_event.is_set():
        time.sleep(0.1)
    
    if not shutdown_event.is_set():
        reset_hands(robot)                # 完成后重置
        time.sleep(0.3)

except KeyboardInterrupt:
    print("\n[中断] 检测到 Ctrl+C，正在安全退出...")
    shutdown_event.set()
finally:
    # 重置手部（拇旋=0，其他=1000）
    try:
        reset_hands(robot)
        time.sleep(0.2 or 0.3)
    except Exception as e:
        print(f"重置手部失败: {e}")
    
    # 逐步释放资源（每个 try/except）
    try: robot.request_shutdown()
    except: ...
    try: robot.wait_for_shutdown()
    except: ...
    try: robot.destroy()
    except: ...
```

### Ctrl+C 处理流程

1. 捕获 `KeyboardInterrupt`
2. 打印 "[中断] 检测到 Ctrl+C..."
3. 设置 `shutdown_event` → hit_next 立即返回
4. `finally` 块调用 `reset_hands(robot)` （拇旋=0，其他=1000）
5. 逐个 try/except 释放机器人资源（不卡死）

## 六、踩过的坑（重要！）

### 坑 1：apscheduler 会跳事件

- 原问题：apscheduler + `max_instances=1`，hit_next 占用超过 INTERVAL 时，**fire 被直接丢弃**，事件被跳过
- 表现：茉莉花又香又白段只弹 3 和 5，被跳过了 1、2
- 解决：改用 `threading.Timer` 手动调度 + `delay = duration * INTERVAL - actual_time`、避免线程重叠
- 验证：10 个事件全部处理，无跳事件

### 坑 2：延音的跨手"幽灵按下"

- 延音：press 后不 release，按住 2 拍
- 下事件在另一手时，延音的手指不会自动归位
- 表现：小星星第 3 段 "5 5" 只听到一声（第一声被吞）
- 解决：延音 hold 结束后**显式** `hit(...release)`（两个文件都已实现）

### 坑 3：拇指关节越定设置

- 原问题：原代码 `generateDexhandCommands` 统一设为 1000，拇旋转被错误设为 1000
- 表玵：拇指不反应 / 拇指错位
- 解决：拇旋转 (idx 0) = **0**、拇弯曲 (idx 1) = **1000**作为固定值
- 作用于：弹奏前、弹奏后、Ctrl+C 退出时

### 坑 4：低八度的拇指不起动

- 原问题：左手小拇指（joint 5）位置 760 位置不够深，物理服务器不响应
- 表玵：茉莉花又香又白段发出 note 1 、1 （小拇指）但服务器不动
- 原因：物理硬件问题（servo 损坏 / 未接好）
- 解决：调整位置为 **700**（小拇指）、**780**（无名指），使 servo 更容易触发

### 坑 5：Moijibake 问题（写入端）— 已解决

- 问题：用 PowerShell here-string + Python heredoc 写文件时，字符串里有 `True` 这个字面词，会被解析为 JSON 布尔值
- 后果：UTF-8 多字节字符被破坏成 `?` (0x3F)
- 解决：用 `chr(84)+chr(114)+chr(117)+chr(101)` 动态构造，或分小块写入
- **现状**：手动修改后，两个文件都是 UTF-8 编码、无 mojibake

### 坑 6：调度器会跳 fire（已解决）

- 问题：apscheduler + `max_instances=1`，上一个 hit_next 还在运行时下一个 fire 被直接丢弃
- 解决：改用 `threading.Timer` 手动调度 + `delay = max(0.001, INTERVAL - actual_time)`（原版）或 `delay = max(0.001, duration * INTERVAL - actual_time)`（优化版）
- 优点：不跳事件 + 连音效果 + 原节奏保持

### 坑 7：Python 3.8 兼容

- 银河调试机是 `/usr/bin/python` → Python 3.8
- `asyncio.to_thread` 是 Python 3.9+
- 用 `loop.run_in_executor(None, ...)` 代替（如果需要升级 asyncio）

## 七、两个文件的差别

| 特点 | main_extended.py | molihua_hand_move.py |
|---|---|---|
| 乐曲 | 小星星 | 茉莉花 |
| 事件格式 | (end_effector, joint_idx, position[, True]) | (note, duration[, True]) + NOTE_MAP |
| 调度器 | apscheduler | threading.Timer 手动 |
| INTERVAL | 0.7s | 0.7s |
| RESET_DELAY | 0.60 | 0.55 |
| NOTE_MAP | 无 | 有（1、2 位置调整为 700/780，8 为高八度 Do） |
| 拇指固定 | 已实现 | 已实现 |
| Ctrl+C | 已实现 | 已实现 |
| reset_hands 辅助函数 | 已实现 | 已实现 |
| 状态 | **完成** | **完成**（例需位置校准） |

## 八、当前能弹的茉莉花段落顺序

```
实际弹奏的茉莉花 SCORE（未注释部分）：
[1] 好一朵美丽的茉莉花花   → 重复 1 次
[2] 好一朵美丽的茉莉花花   → 重复 2 次
[3] 芬芳美丽满枝亝
[4] 又香又白人人啰
[5] 让我来将你摘下          → 含延音 (2, 1, True)
[6] 送给别人家                   → 部分「重复 1.、 2.、终止」未激活
```

**未激活的 SCORE 段落（全部处于注释状态）：**

```
- 前奇1 + 前奇2        → 弹一遍（需手动取消注释）
- 送给别人家后面        → 重复 I. 、II. 、终止
- 间奇                      → 主歌 2 之前弹一遍
- 主歌 2                     → 同主歌但不同诗词，弹一遍
- 终曲                     → 重复 芬芳、又香、让我、送人
```

## 九、《茉莉花》原谱结构

```
{ (前奇1)        → 弹一遍
  (0 0 5 - | 2 - 0 0 | 1 - 0 0 | 2 - - -)        }

  3 35 6·1 1·6 | 5 56 5 -      → [好一朵美丽的茉莉花花] (重复 2 次)
  5 55 3 5 | 6 6 5 -                → [芬芳美丽满枝亝]
  3 2 3 5 3 2 | 1 1 2 1 -              → [又香又白人人啰]
  3 2 1 3 | 2·3 5 6·1 5 -      → [让我来将你摘下] (延音)
  2 1 6 1 | 5 - 6 1 | 1·6 1 7·7 1 2 3 - | 2 1 6 1 6 1 | 5 - - - -      → [送给别人家]
                                                                  (重复 1.、 2.、终止)
{ (间奇)                        → 弹一遍
  3 3 5 6 | 1·1 6 | 5 5 6 5 0    → 重复 2 次
  3 3 5 6 | 1·1 6 | 5 5 6 5 0
  5 5 5 3 5 | 6 6 5 0                       }

  3 2 3 5 3 2 | 1 1 2 1 -              → [主歌 2] (重复「让我来将你摘下」)
  2 1 6 1 | 5 - 6 1 | ...               → [送给别人家]

{ (终曲)                          → 重复多段
  芬芳·又香·让我·送人    }
```

**重复符号：**
- `Ⅰ` （1、2、3、4） → 重复第 1 次，以后是第 2 次
- `Ⅱ` （1、2、3、4） → 重复第 2 次，以后是终止
- `Ⅲ` （1、2、3、4） → 终止

## 十、调你法调试表

### 验证过的场景

| 场景 | 调你记录 |
|---|---|
| `小星星` 原谱 | ✓ 能弹 |
| `茉莉花` `好一朵美丽的茉莉花花` | ✓ 能弹（含高八度 Do） |
| `茉莉花` `芬芳美丽满枝亝` | ✓ 能弹（连音使用高八度 Do） |
| `茉莉花` `又香又白人人啰` | ⚠ 物理服务器问题（note 1/2 不动） |
| `茉莉花` `让我来将你摘下` | ✓ 能弹（含延音） |
| Ctrl+C 安全退出 | ✓ 已实现（重置手部 + 释放资源） |

### 参数调你记录

| 参数 | 值 | 说明 |
|---|---|---|
| INTERVAL | 0.7s | 原始节奏（~86 BPM） |
| RESET_DELAY | 0.55s | 服务器物理响应时间（必须式原始纯决） |
| INTERVAL | 0.35s | 8分音符连音节奏（8分音符间隔 0.075s）但 1 拍会变慢（6 拍需 1.05s） |

## 十一、参考文件位置

```
# 部署目录（Linux 调试机）
/userdata/cx/tutorials/main_extended.py
/userdata/cx/tutorials/molihua_hand_move.py

# 源目录（Windows）
E:\baida-code\tutorials\hand-test\music\test\main_extended.py
E:\baida-code\tutorials\hand-test\music\test\molihua_hand_move.py
E:\baida-code\tutorials\hand-test\music\test\WORK_LOG.md

# 关键参考
E:\baida-code\tutorials\hand-test\music\test\main.py          (4.6KB, 原始 apscheduler 版)
E:\baida-code\tutorials\hand-test\music\test\flash_galbot.py  (27KB, 最完整原始版)
E:\ENV\python3.12.6\python.exe                              (Windows 测试 Python 3.12)
E:\ENV\python3.11.8\python.exe                              (Linux 调试机 Python 3.8+)

# 工作环境（Codex 容器内）
/home/galbot/miniconda3/envs/grasp-yolo/bin/python           (Linux 调试机 Python)
```

## 十二、SDK 速查（来自 G1 文档）

### `set_dexhand_command`

```python
robot.set_dexhand_command(
    end_effector="left_dexhand" | "right_dexhand",
    dexhand_command=[JointCommand(position=p) for p in [0..5] for ...],
    is_blocking=False,  # 重要：必须 False
)
# 返回 ControlStatus.SUCCESS | RUNNING | TIMEOUT | INVALID_INPUT | HARDWARE_FAULT
```

### `JointCommand` 字段

- `position`: 0-1000 范围内的位置值（具体含义需对照灵巧手标定）
- Inspire 灵巧手：所有手指张开 = 1000，按到底 = 较小值

### `get_dexhand_state`

```python
state = robot.get_dexhand_state("left_dexhand", DexHandType.INSPIRE)
# 返回 DexhandState：包含 joint_state[joint_state_vec] 等
# 注意：RH56 没显式暴露力反馈
```

### G1 默认关节组

```python
from galbot_sdk.g1 import G1JointGroup
G1JointGroup.LEFT_DEXHAND   # 6 个关节：lleft_dexhand_joint1..6
G1JointGroup.RIGHT_DEXHAND  # 6 个关节：right_dexhand_joint1..6
```

## 十三、问题诊断速查

| 症状 | 可能原因 | 检查 |
|---|---|---|
| 运行报 `NameError` | 缺 import | 看 imports 块 |
| 运行报 `ModuleNotFoundError: galbot_sdk` | 部署机没装 SDK | 在部署机用 `which python` 确认环境 |
| 一个音听不到 | 跨手幽灵按下 | 查延长音后是否显式 release |
| 节奏太慢 | INTERVAL 太大 | 调小 INTERVAL 到 0.5-0.6 |
| 节奏太快/挤 | INTERVAL 太小 或 duration 没设对 | 查 SCORE 的 duration |
| 音符错 | NOTE_MAP 不准 | 改 (joint, position) 值 |
| 延音没按住 | `True` 标志没识别 | 查 hit_next 的 `if len(event) == 3` 逻辑 |
| 一启动就报 mojibake | 文件保存时编码不对 | 用 UTF-8 重新保存 |
| 右手小拇指不动 | joint 5 物理问题 或位置不够 | 调整 position 为 600（右小拇有效值） |
| 左手小拇有音 | joint 5 有效 | position=700、位置 760 不够深 |
| 拇指错位 | 拇旋转 未设 0 | 验证 cmds[0].position = 0 |
| Ctrl+C 退出太久 | 资源释放不完备 | 检查资源释放是否包 try/except |

## 十四、最终状态摘要

```
✅ 小星星 (main_extended.py)              完成，apscheduler 调度，可部署
✅ 茉莉花 (molihua_hand_move.py)        完成，Timer 调度 + 拇指固定 + Ctrl+C 安全退出
✅ 茉莉花 SCORE                      包含高八度 Do (note 8) + 连音 (按拍数) + 延音 (True)
✅ 拇指两个关节全程固定          拇旋转=0, 拇弯曲=1000
✅ Ctrl+C 安全退出                  重置手部 + 逐步释放资源
⚠ 又香又白 note 1, 2            左手小拇、无名指物理服务器需检查或调位置
⏩ 未完成选项                         前奇、间奇、主歌 2、终曲、重复 I.·II.、为可选拓例
```

---

**下次继续工作时的建议**：

1. **上机验证 Ctrl+C**：按下右手小拇看是否能释放 + 拇指是否保持 0/1000 位置
2. **检查又香又白 notes 1, 2**：左手小拇、无名指的位置是否需要调整，或查看左手服务器是否有问题
3. **如需完整曲子**：取消部分注释（前奇、间奇、主歌 2、终曲、重复 I/II），让完整曲子能弹出来
4. **如需部署**：复制到 Linux 调试机 `/userdata/cx/tutorials/`



---

## 十五、最新进度（2026-08-19 补充）

### 15.1 高八度音符处理策略明确

**用户明确指定**："除了高八度 1·（do）使用右手小拇指负责，其他的高音都给中央 C 区的音负责"。

即：
- `1·`（高八度 do）→ NOTE_MAP[8]（右手小拇指，position=600）
- 其他高八度（`2·` `3·` `4·` `5·` `6·` `7·`）→ 使用 NOTE_MAP 中对应的低八度音

### 15.2 NOTE_MAP 注释更新（已实施）

```python
NOTE_MAP = {
    # 低八度（中央 C 区）—— 高八度音符默认使用此区的同名音
    1: ("left_dexhand",  5, 700),  # 1 = C4（加深弯曲）
    2: ("left_dexhand",  4, 780),  # 2 = D4（加深弯曲）
    3: ("left_dexhand",  3, 850),  # 3 = E4
    4: ("left_dexhand",  2, 830),  # 4 = F4
    5: ("right_dexhand", 2, 830),  # 5 = G4
    6: ("right_dexhand", 3, 850),  # 6 = A4
    7: ("right_dexhand", 4, 830),  # 7 = B4
    
    # 高八度特殊处理：只有 1· (C5) 真正弹到高八度
    # 其他高八度（2· 3· 4· 5· 6· 7·）暂不实现高八度弹奏，
    # 改用 NOTE_MAP 中对应的低八度音代替
    8: ("right_dexhand", 5, 600),  # 1· = C5（右手小拇指，唯一高八度实现）
}
```

### 15.3 【I.】段 SCORE 修正（已实施）

**用户纠正**：之前我误把简谱伴奏 `1·7 7` 当成了【I.】段的主旋律加进了 SCORE。

正确的主旋律（用户确认）：`6 61 2 3 12 16 5 - -`

修正后：
```python
# 茉莉花哈茉莉花（【I.】··············"跳回开头"）
# 仅保留主旋律：6 61 2 3 12 16 5 - -
# 原谱 1·7 7 是伴奏不弹奏
(6, 1), (6, 1), (1, 1),      # 6 61
(2, 1), (3, 1),             # 2 3
(1, 1), (2, 1), (1, 1), (6, 1),  # 12 16
(5, 1, True),              # 5 - - (sol 延一拍)
```

### 15.4 当前已修复/实施的清单

| 项目 | 状态 |
|---|---|
| 小星星（main_extended.py） | ✅ 完成 |
| 茉莉花（molihua_hand_move.py） | ✅ 主旋律部分完成 |
| 高八度 Do（note 8） | ✅ 右手小拇指 position=600 |
| 其他高八度 | ✅ 暂用低八度（中央 C 区）|
| 拇指全程固定 | ✅ 旋转=0, 弯曲=1000 |
| Ctrl+C 安全退出 | ✅ reset_hands + try/except |
| 【I.】段主旋律 | ✅ 仅主旋律（已排除伴奏）|
| 8 分音符连音 | ✅ Timer delay 按拍数计算 |
| 延音（True 标志）| ✅ actual_time += RESET_DELAY |
| 简谱【I.】段 | ✅ 主旋律 `6 61 2 3 12 16 5 - -` |
| 简谱伴奏 `1·7 7` | ✅ 排除不弹奏（注释说明）|

### 15.5 仍待处理/未弹奏的 SCORE 段

| 段 | 状态 |
|---|---|
| 前奏 1 / 前奏 2 | ❌ 注释，未弹奏 |
| 又香又白主旋律 | ✅ 已修复（不弹奏 notes 1, 2 物理问题除外）|
| 让我来将你摘下 | ✅ 弹奏（含延音 `(2, 1, True)`）|
| 送给别人家（含 I. II. 反复）| ❌ 注释，未弹奏 |
| 间奏 | ❌ 注释，未弹奏 |
| 主歌 2 | ❌ 注释，未弹奏 |
| 终曲 | ❌ 注释，未弹奏 |

### 15.6 用户已知的设计原则

1. **高八度音符策略**：
   - `1·`（do）→ 右手小拇指（NOTE_MAP[8]）
   - 其他高八度 → 用低八度同名指
   - 其他高八度真要弹需要后续通过**移动手**实现

2. **主旋律 vs 伴奏**：
   - 机器人只弹奏主旋律
   - 伴奏（如 `1·7 7` 等花腔）不弹奏

3. **文件命名约定**：
   - `main_extended.py` → 小星星
   - `molihua_hand_move.py` → 茉莉花

### 15.7 下一步建议

1. **上机验证【I.】段修正后的效果**（现在只弹主旋律，不弹伴奏）
2. **物理伺服检查**：又香又白 notes 1, 2（左手小拇、无名指）的伺服是否正常
3. **如需完整曲子**：取消前奏、间奏、主歌 2、终曲、反复 I/II 等注释
4. **如需部署**：复制到 Linux 调试机 `/userdata/cx/tutorials/`

---

### 十六、手部移位功能整合（2026-08-20）

**背景**：原 `main_extended_molihua.py` 在弹奏 `让我来将你摘下` 等含高八度的段落时，音会\"错位\"——因为简谱的高八度音符（特别是高 2·、低 5/6）实际上对应不同的物理钢琴键位，单纯改 NOTE_MAP 不能解决。

**用户需求**：
- 左手小拇指从 C4（左移前）移到 G3（低 5）→ 左移 7cm
- 右手无名指从 B4（右移前）移到 D5（高 2·）→ 右移 4.5cm
- 已有测试值（7cm、4.5cm）

**实施**：在 `E:\baida-code\tutorials\hand-test\music\test\` 下新建了 `molihua_hand_move.py`（替代原 `main_extended_molihua.py`）。

### 16.1 NOTE_MAP 扩展

| 编号 | 含义 | 手 | 关节 | 位置 | 触发移位 |
|---|---|---|---|---|---|
| 1-7 | 中央 C 区（C4-B4）| 左/右 | 5/4/3/2/2/3/4 | 700/830/850/830/830/850/830 | 不需要 |
| 8 | 高 1· (C5) | 右 | 5（小指）| 600 | 不需要（自然位姿）|
| 9 | **高 2· (D5)** | 右 | 4（无名指）| 830 | **需要右移 4.5cm** |
| 10 | **低 5· (G3)** | 左 | 5（小指）| 700 | **需要左移 7cm** |
| 11 | **低 6· (A3)** | 左 | 4（无名指）| 830 | **需要左移 7cm** |

### 16.2 新增 `move_hand_to()` 函数（基于 move-hand-fix.py）

包装 `motion.motion_plan_multi_waypoints`，只移动单手到绝对目标位姿：
- `set_direct_execute(True)` - 规划完立即执行
- `set_move_line(True)` - 末端走直线
- `is_blocking = False` - 不阻塞
- `enable_collision_check = False` - 必须关闭（否则 plan 失败）

### 16.3 新增 3 个手部位姿常量
```python
LEFT_HAND_OFFSET_LEFT   = 0.07    # 7cm 左移
RIGHT_HAND_OFFSET_RIGHT = -0.045  # 4.5cm 右移
HAND_MOVE_DELAY         = 0.1     # 用户微调值（注意：建议 2.0）
```

### 16.4 `hit_next()` 加入手部移位管理

**触发逻辑**（带 lookahead 优化）：
```python
cur_needs_left  = note in [10, 11]   # 低 5/6 触发左移
cur_needs_right = note == 9           # 高 2· 触发右移
next_note = SCORE[idx + 1][0] if idx + 1 < MAX else None
next_needs_left  = (next_note in [10, 11]) if next_note is not None else False
next_needs_right = (next_note == 9) if next_note is not None else False

if cur_needs_left and not left_hand_moved:
    target = list(left_origin_pose); target[1] += LEFT_HAND_OFFSET_LEFT
    move_hand_to(motion, target, G1JointGroup.left_arm)
    time.sleep(HAND_MOVE_DELAY)
    left_hand_moved = True
elif not cur_needs_left and not next_needs_left and left_hand_moved:
    # 移回原位（用 lookahead 避免连续触发来回切）
    ...
```

**核心：先触发再弹琴**。弹奏前先把手移到对应位置，再按下音符。

### 16.5 `main()` 启动时捕获原位姿
```python
status, l = motion.get_end_effector_pose_on_chain(G1JointGroup.left_arm)
if status != MotionStatus.SUCCESS:
    raise RuntimeError(...)
status, r = motion.get_end_effector_pose_on_chain(G1JointGroup.right_arm)
left_origin_pose = list(l); right_origin_pose = list(r)
```
- 启动后从 SDK 读取左右手当前位置，保存为\"原始位姿\"
- 所有移位都基于这个原始位姿做 offset

### 16.6 SCORE 区分低八度 5/6
SCORE 中低八度音改用 10/11：
- `(5, 0.5)` 在 `好一朵美丽的茉莉花` 中保持 5（中央 G4）→ 用 NOTE_MAP[5]（右手食指）
- `(5, 0.5)` 在 `送给别人家` 中改为 10（低 G3）→ 用 NOTE_MAP[10]（左手小指，手左移后）

**关键区分点**：
- `好一朵美丽的茉莉花` / `芬芳美丽满枝丫` / `又香又白人人夸` / `让我来将你摘下` 的 5/6 → 中央（手不动）
- `送给别人家` / `茉莉花 茉莉花`（结尾）/ 【I.】段 的 5/6 → 低八度（手左移）
- `让我来将你摘下` 的高 2· → NOTE_MAP[9]（手右移）

### 16.7 `get_command_for_note()` 简化为查表
原版有重映射逻辑（错误地把 5/6 映射到 NOTE_MAP[1]/[2]），最终版：
```python
def get_command_for_note(note):
    \"\"\"直接从 NOTE_MAP 查表。手部移位与否由 hit_next 中的 trigger 逻辑控制。\"\"\"
    return NOTE_MAP.get(note)
```

### 十七、踩过的坑（2026-08-20）

### 坑 A：`ControlStatus.SUCCESS` vs `MotionStatus.SUCCESS`
- **问题**：原版用 `ControlStatus.SUCCESS` 与 `motion.get_end_effector_pose_on_chain()` 返回值比较
- **结果**：永远是 False（不同枚举），抛 `RuntimeError: get left origin pose failed: MotionStatus.SUCCESS`
- **修复**：增加 `from galbot_sdk.g1 import MotionStatus`，motion 相关用 `MotionStatus.SUCCESS`；set_dexhand_command 仍用 `ControlStatus.SUCCESS`
- **教训**：用 SDK 不同方法的返回值类型时，要 import 多个 Status 枚举

### 坑 B：低八度 5/6 与中央 5/6 混淆
- **问题**：`hit_next` 触发条件原本是 `note in [5, 6]`，导致\"好一朵美丽的茉莉花\"的 5/6 也触发手部移位
- **结果**：弹中央 5/6 时手在移位状态，音会按错
- **修复**：
  1. 触发条件改为 `note in [10, 11]`
  2. SCORE 中低八度的 5/6 改用 10/11
  3. NOTE_MAP 新增 10 (低5·) 和 11 (低6·)

### 坑 C：`get_command_for_note` 错误重映射
- **问题**：原版 `if note == 5: return NOTE_MAP[1]`——把中央 5 错误重映射到左手小拇指
- **结果**：弹\"好一朵美丽的茉莉花\"的 5 时用左手弹
- **修复**：直接 `return NOTE_MAP.get(note)`，NOTE_MAP 本身已经按音符编号映射好手指关节

### 十八、仍然要注意的问题（2026-08-20）

### 18.1 `HAND_MOVE_DELAY = 0.1` 偏小
- **当前值**：0.1 秒（用户微调）
- **建议值**：2.0 秒
- **原因**：实际手部物理移动需要约 2 秒，0.1 秒时手还在移，弹的音会按错位置
- **风险**：连续触发移位时（如\"送给别人家\"），可能音会模糊

### 18.2 物理伺服需要上机验证
- **左手小指/无名指**：`又香又白主旋律` notes 1, 2 仍依赖物理位置 700/830
- **建议**：实际跑 `molihua_hand_move.py`，观察物理响应
- **备选**：根据实际响应调整 `NOTE_MAP[1]/[2]` 的 position 值

### 18.3 高八度 6· 还没特殊处理
- **当前**：原谱 `6·`（高八度 6 / A5）→ 弹成 A4（中央）
- **如果要弹 A5**：需要把 SCORE 中 `6·` 改为新编号 + 触发左/右移位
- **当前策略**：用户说\"其他高八度都按中央弹\"，暂不实现

### 18.4 文件命名
- 旧：`main_extended_molihua.py`
- 新：`molihua_hand_move.py`（整合了手部移位功能）
- **建议**：部署时用新文件，旧文件可作参考保留



---

**WORK_LOG.md 更新完成时间**：2026-08-20

