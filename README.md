<div align="center">

# 🎹🤖 Galbot Piano

### 用 Galbot G1 灵巧手自动弹奏《小星星》《茉莉花》

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Galbot SDK](https://img.shields.io/badge/galbot__sdk-g1-orange)](https://github.com/)

[![Twinkle](https://img.shields.io/badge/🎵_Twinkle-Done-brightgreen)](#-小星星twinkle-twinkle-little-star)
[![Molihua](https://img.shields.io/badge/🌸_Molihua-Done-brightgreen)](#-茉莉花jiangsu-folk-song)
[![Hand Move](https://img.shields.io/badge/🤚_Hand_Move-Done-brightgreen)](#-手部位移解决高低八度)
[![Speed Ctrl](https://img.shields.io/badge/⚡_Speed_Ctrl-Done-brightgreen)](#-速度控制解决h移动延迟)

</div>

---

## 📖 项目简介

通过 [galbot_sdk.g1](https://github.com/) 控制 **Galbot G1** 机器人的 **RH56 仿人五指灵巧手**，把双手架在钢琴琴键上，自动按时间线触发手指按下 / 归位指令，实现"机械手弹钢琴"。

### ✨ 项目亮点

| 能力 | 说明 |
|---|---|
| 🎵 **两首完整曲目** | 《小星星》《茉莉花》（江苏民歌，何仿改编版）|
| 🤚 **手部位移** | 通过 IK + 关节速度控制，自动把左手左移 7cm / 右手右移 4.5cm，解决高低八度音 |
| ⏱ **智能调度** | 支持延音线、连音线、8 分音符、休止符 |
| ⚡ **速度可控** | `move_hand_to` 用 `inverse_kinematics` + `set_joint_positions(speed_rad_s=...)`，移动时间可预测 |
| 🛡 **安全退出** | Ctrl+C 安全退出，重置手部 + 释放资源，全程 try/except 兜底 |

---

## 🎵 演示曲目

### 🌟《小星星》（Twinkle Twinkle Little Star）

`piano_extended.py` · 6 段 · 约 34 秒 · apscheduler 调度

简谱：
```
1 1 5 5 | 6 6 5 - |  4 4 3 3 | 2 2 1 - |
5 5 4 4 | 3 3 2 - |  1 1 5 5 | 6 6 5 - |
4 4 3 3 | 2 2 1 - ||  (A 段重复 5、6 段)
```

### 🌸《茉莉花》（Jiangsu Folk Song）

`molihua_hand_move.py` · 伴奏 + 独奏两轮 · 约 60 秒 · `threading.Timer` 手动调度

![茉莉花简谱](musical_score/molihua.png)

**特殊处理**：

| 简谱符号 | 处理方式 |
|---|---|
| `1·` 高八度 Do（C5）| 右手小拇指按 `position=600`（原位）|
| `2·` 高八度 Re（D5）| 右手右移 -4.5cm，弹原指位 |
| `5·6·` 低八度 5/6（G3/A3）| 左手左移 +7cm，弹原指位 |
| `-` 延音线 | duration=2 + `is_extended=True` |
| `⌒` 连音线 | 第一个音 duration 标 True |

---

## 🚀 快速开始

### 硬件要求

- **Galbot G1 机器人**（双臂，灵巧手集成）
- **RH56 灵巧手** × 2（因时机器人 Inspire RH56，每只 6 个自由度）
- **钢琴（或电子琴）** —— 把灵巧手按映射架在合适琴键位置上

### 软件要求

- Python 3.8+
- `galbot_sdk`（G1 版本，需按官方文档安装）
- `apscheduler`（仅《小星星》需要）

```bash
pip install apscheduler
```

### 灵巧手位置摆放

| 简谱 | 唱名 | 关节 | 舵机位置 |
|---|---|---|---|
| 1 | do (C4) | 左小拇指 (joint 5) | 700 |
| 2 | re (D4) | 左无名指 (joint 4) | 830 |
| 3 | mi (E4) | 左中指 (joint 3) | 850 |
| 4 | fa (F4) | 左食指 (joint 2) | 830 |
| 5 | sol (G4) | 右食指 (joint 2) | 830 |
| 6 | la (A4) | 右中指 (joint 3) | 850 |
| 7 | ti (B4) | 右无名指 (joint 4) | 830 |
| 1· | do (C5) | 右小拇指 (joint 5) | 600 |

> 位置值范围 0-1000，**1000 = 张开**，数值越小越弯曲。具体数值由 RH56 灵巧手校准决定。

### 运行

```bash
# 🎵 小星星
python piano_extended.py

# 🌸 茉莉花（含手部位移）
python molihua_hand_move.py
```

### 本地 Web 调试台

仓库提供无需额外 Web 框架的本地调试页面，可查看机器人连接状态、选择曲目、启动/软停止演奏，并发送双灵巧手安全归零指令：

```bash
python webui/server.py
```

访问 `http://127.0.0.1:8080`。详情见 [webui/README.md](webui/README.md)。当前“姿态归零”仅覆盖双灵巧手安全张开，不会擅自执行未经真机确认的全身关节归零。

灵巧手会自动：
1. 初始化为全张开状态
2. 按时间线按下 / 归位对应手指
3. **茉莉花**：在高低八度音前自动平移手腕到合适位置
4. 弹奏完毕后恢复张开状态
5. 释放机器人资源

---

## 🏗 核心架构

### SDK 接口

```python
from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand, MotionStatus

robot = GalbotRobot()
robot.init()                                       # 连真机
status = robot.set_dexhand_command(
    end_effector="left_dexhand",                   # 或 "right_dexhand"
    dexhand_command=[JointCommand(position=p) for ...],  # 6 元素列表
    is_blocking=False,
)
```

### 音符 → 手指 → 位置 映射（NOTE_MAP）

```python
NOTE_MAP = {
    1:  ("left_dexhand",  5, 700),  # 1 = C4
    2:  ("left_dexhand",  4, 830),  # 2 = D4
    3:  ("left_dexhand",  3, 850),  # 3 = E4
    4:  ("left_dexhand",  2, 830),  # 4 = F4
    5:  ("right_dexhand", 2, 830),  # 5 = G4
    6:  ("right_dexhand", 3, 850),  # 6 = A4
    7:  ("right_dexhand", 4, 830),  # 7 = B4
    8:  ("right_dexhand", 5, 600),  # 1· = C5（高八度 Do）
    9:  ("right_dexhand", 4, 830),  # 2· = D5（高八度 Re，需右移）
    10: ("left_dexhand",  5, 700),  # 低 5· = G3（低八度 5，需左移）
    11: ("left_dexhand",  4, 830),  # 低 6· = A3（低八度 6，需左移）
}
```

### 拇指全程固定

无论弹奏前、弹奏后，还是 Ctrl+C 退出，**拇指两个关节都保持**：

```python
cmds[0].position = 0      # 拇指旋转 (Joint 1)  → 全程 0
cmds[1].position = 1000   # 拇指弯曲 (Joint 6)  → 全程 1000
```

### 事件格式

```python
# 小星星：3-tuple = 普通音，4-tuple + True = 延音 1 拍
("left_dexhand", 5, 700)             # 普通
("right_dexhand", 2, 830, True)      # 延音一拍

# 茉莉花：(note, duration) 或 (note, duration, is_tied)
(3, 1)                               # 1 拍
(5, 2)                               # 2 拍
(1, 1, True)                         # 1· 延 1 拍
(0, 1)                               # 1 拍休止符
(8, 0.5)                             # 0.5 拍（8 分音符）
```

---

## ⚡ 速度控制（手部位移）

### 核心思路

为了解决物理手部平移需要约 2 秒，但 `motion_plan_multi_waypoints` 内部速度不可控的问题，`move_hand_to` 改为：

```python
def move_hand_to(motion, robot, target_pose, joint_group,
                speed_rad_s=HAND_MOVE_SPEED_RAD_S):
    # 1) 求逆运动学：把目标末端位姿 -> 关节角度
    status, positions = motion.inverse_kinematics(target_pose, [joint_group])
    if status != MotionStatus.SUCCESS:
        print(f"[IK] 失败 ({joint_group}): {status}")
        return
    joint_positions = positions[joint_group]

    # 2) 以指定速度驱动关节到目标角度（非阻塞）
    status = robot.set_joint_positions(
        joint_positions=joint_positions,
        joint_groups=[joint_group],
        speed_rad_s=speed_rad_s,
        is_blocking=False,
    )
```

### 关键参数

| 参数 | 值 | 说明 |
|---|---|---|
| `HAND_MOVE_SPEED_RAD_S` | 0.3 | 关节速度（弧度/秒），0.2 起慢慢加 |
| `HAND_MOVE_DELAY` | 2.0 | 物理到位等待时间（原 0.1 偏小）|
| `LEFT_HAND_OFFSET_LEFT` | +0.07 | 左手左移 7cm（弹低 5/6）|
| `RIGHT_HAND_OFFSET_RIGHT` | -0.045 | 右手右移 4.5cm（弹高 2·）|

### 移位管理（带 lookahead）

为避免连续触发的来回切换，`hit_next` 用 lookahead 判断：

```python
cur_needs_left  = note in [10, 11]   # 当前音需左移
next_needs_left = (next_note in [10, 11]) if next_note is not None else False

if cur_needs_left and not left_hand_moved:
    target = list(left_origin_pose); target[1] += LEFT_HAND_OFFSET_LEFT
    move_hand_to(motion, robot, target, G1JointGroup.left_arm)
    time.sleep(HAND_MOVE_DELAY)
    left_hand_moved = True
elif not cur_needs_left and not next_needs_left and left_hand_moved:
    # 当前不需要、下一个也不需要 → 回原位
    move_hand_to(motion, robot, list(left_origin_pose), G1JointGroup.left_arm)
    time.sleep(HAND_MOVE_DELAY)
    left_hand_moved = False
```

---

## 📊 两种调度方案对比

| 维度 | `piano_extended.py` | `molihua_hand_move.py` |
|---|---|---|
| 调度器 | `apscheduler.BackgroundScheduler` | `threading.Timer` 手动调度 |
| 触发频率 | `INTERVAL=0.7s` 固定 | `INTERVAL=0.7s` 基准 |
| 跨事件 | ❌ 长事件会跳 fire | ✅ duration × INTERVAL - actual_time |
| 延音线 | ⚠️ 用 `is_extended=True` 模拟 | ✅ 原生支持 |
| 连音线 | ❌ gap=0.15s | ✅ 0.5 拍间隔 0.35s |
| 手部位移 | ❌ 无 | ✅ IK + 速度控制 |
| 8 分音符 | ⚠️ 简化处理 | ✅ duration=0.5 |

**选择建议**：
- **入门 / 演示**：用 `piano_extended.py`（简单稳定）
- **复杂曲目 / 需要严格节奏**：用 `molihua_hand_move.py`（Timer 精确控制）

---

## 📁 文件清单

| 文件 | 行数 | 状态 | 用途 |
|---|---|---|---|
| **`molihua_hand_move.py`** | ~380 | ✅ **生产** | 茉莉花最终版（手部位移 + 速度控制）|
| **`piano_extended.py`** | ~250 | ✅ **生产** | 小星星最终版（apscheduler 调度）|
| `main_extended_molihua.py` | ~370 | 📚 参考 | 茉莉花基础版（无手部位移）|
| `piano_test.py` | ~150 | 📜 历史 | 小星星早期版（3-tuple 事件格式）|
| `musical_score/molihua.png` | — | ✅ | 茉莉花简谱配图 |
| `WorkLog/WORK_LOG.md` | 700+ | ✅ | 完整开发日志（含踩过的坑）|

---

## 🐛 已踩过的坑（详见 `WorkLog/WORK_LOG.md`）

### 坑 A：`ControlStatus.SUCCESS` vs `MotionStatus.SUCCESS`
**问题**：原版用 `ControlStatus.SUCCESS` 与 `motion.get_end_effector_pose_on_chain()` 返回值比较 → 永远是 False（不同枚举）  
**修复**：分开 import，`motion` 相关用 `MotionStatus.SUCCESS`，`set_dexhand_command` 用 `ControlStatus.SUCCESS`

### 坑 B：低八度 5/6 与中央 5/6 混淆
**问题**：`hit_next` 触发条件原本是 `note in [5, 6]`，导致"好一朵美丽的茉莉花"的 5/6 也错误触发手部移位  
**修复**：触发条件改为 `note in [10, 11]`，SCORE 中低八度音改用 10/11 编号

### 坑 C：`get_command_for_note` 错误重映射
**问题**：原版 `if note == 5: return NOTE_MAP[1]` —— 把中央 5 错误重映射到左手小拇指  
**修复**：简化为 `return NOTE_MAP.get(note)`，NOTE_MAP 本身已按编号映射好手指关节

### 坑 D：延长音"幽灵按下"
**问题**：第 1 段末尾延长音 G4 让右食指持续按下 2 拍，第 2 段全在左手操作 → 右食指未显式归位  
**修复**：延长音 2 拍 hold 结束后显式调用 `hit()` 归位

---

## 🎛 参数调优

| 参数 | 默认 | 适用 | 说明 |
|---|---|---|---|
| `INTERVAL` | 0.7 | 所有 | 一拍时长（秒），决定整体速度 |
| `RESET_DELAY` | 0.55 | 所有 | 按下后归位等待（需 ≥ 物理松开时间）|
| `HAND_MOVE_SPEED_RAD_S` | 0.3 | molihua | 手部位移速度（弧度/秒）|
| `HAND_MOVE_DELAY` | 2.0 | molihua | 手部位移后等待时间 |
| `LEFT_HAND_OFFSET_LEFT` | +0.07 | molihua | 左手左移距离（米）|
| `RIGHT_HAND_OFFSET_RIGHT` | -0.045 | molihua | 右手右移距离（米）|

---

## 🚧 已知问题 / 待优化

1. **高八度 6· 还没特殊处理**：当前弹成中央 A4，如需弹 A5 需扩展 NOTE_MAP
2. **IK 失败兜底**：当前 IK 失败时仅打印日志并 return，可在异常位姿处加入回退
3. **物理伺服校准**：`NOTE_MAP[1]`（700）和 `NOTE_MAP[2]`（830）需上机验证响应
4. **真实节拍细化**：当前简化处理 8 分音符，可扩展支持 16 分音符（duration=0.25）

---

## 🔮 扩展方向

- 🎼 **更多曲目**：生日快乐、致爱丽丝、欢乐颂等
- 🎚 **力度控制**：用 `JointCommand.effort` 字段实现强弱拍
- 📡 **状态反馈**：用 `get_dexhand_state` 读取实际位置做闭环控制
- 🎤 **实时识谱**：摄像头读取乐谱 → 自动转 SCORE 事件
- 🤖 **双手协调**：用 `motion_plan_multi_waypoints` 同步双手位姿

---

## 📜 License

MIT

---

## 🙏 致谢

- 🤖 [Galbot](https://www.galbot.com/) — G1 机器人 SDK
- 🦾 [因时机器人 Inspire](https://www.inspire-robots.com/) — RH56 灵巧手
- 🌸 茉莉花旋律 — 江苏民歌（何仿改编，祖英演唱版）
