# Galbot Piano 🎹🤖

> 用 Galbot 灵巧手在钢琴上弹奏《小星星》的 Python 项目

## About

通过 [galbot_sdk](https://github.com/) 控制 Galbot G1 灵巧手（6 个自由度），把双手放在钢琴琴键上，配合 `apscheduler` 按时间线触发手指按下/归位指令，实现自动弹奏。本仓库包含两个版本的弹奏脚本：

- `piano_test.py` — 早期版本，简单粗暴（每音 1 拍后立刻归位）
- `piano_extended.py` — 改进版，**支持延长音**（"-"，按 2 拍）+ **跨手自动归位**（解决幽灵按下问题）

## 演示曲目

### 《小星星》（Twinkle Twinkle Little Star）

简谱：
```
1 1 5 5 | 6 6 5 - |  4 4 3 3 | 2 2 1 - |
5 5 4 4 | 3 3 2 - |  1 1 5 5 | 6 6 5 - |
4 4 3 3 | 2 2 1 - ||  (A 段重复 5、6 段)
```

6 段，约 34 秒。

## 快速开始

### 硬件要求

- **Galbot G1 机器人**（带灵巧手）
- **RH56 灵巧手**（因时机器人 Inspire RH56，6 个自由度）
- **钢琴**（或电子琴）—— 把灵巧手放在合适琴键位置上

### 软件要求

- Python 3.8+
- `galbot_sdk`（G1 版本）
- `apscheduler`

```bash
pip install apscheduler
# galbot_sdk 由 Galbot 官方提供，需按官方文档安装
```

### 运行

把灵巧手按以下位置架在钢琴上：
- **左小指**（joint 5）→ C4（do）
- **左无名指**（joint 4）→ D4（re）
- **左中指**（joint 3）→ E4（mi）
- **左食指**（joint 2）→ F4（fa）
- **右食指**（joint 2）→ G4（sol）
- **右中指**（joint 3）→ A4（la）

然后运行：

```bash
python piano_extended.py
```

灵巧手会自动：
1. 初始化为全张开状态
2. 按时间线按下/归位对应手指
3. 弹奏完毕后恢复张开状态
4. 释放机器人资源

## 核心设计

### 6 个自由度到琴键的映射

灵巧手有 6 个自由度（左/右各 6 个关节），本项目用以下映射：

| 简谱 | 唱名 | 关节 | 舵机位置 |
|---|---|---|---|
| 1 | do (C) | 左小指 (joint 5) | 760 |
| 2 | re (D) | 左无名指 (joint 4) | 830 |
| 3 | mi (E) | 左中指 (joint 3) | 850 |
| 4 | fa (F) | 左食指 (joint 2) | 830 |
| 5 | sol (G) | 右食指 (joint 2) | 830 |
| 6 | la (A) | 右中指 (joint 3) | 850 |
| 7 | ti (B) | 右无名指 (joint 4) | 830 |

> 位置值是舵机角度（0-1000，1000 = 张开，数值越小越弯曲）。具体数值由灵巧手校准决定，本项目用的数值适用于**因时机器人 RH56 灵巧手**。

### 事件格式

```python
EVENTS = [
    # 3-tuple: (end_effector, joint_idx, joint_position)
    #          普通音，按 1 拍后归位
    ("left_dexhand", 5, 760),   # C4, 1 拍
    
    # 4-tuple: (end_effector, joint_idx, joint_position, True)
    #          延长音，按 2 拍不归位
    ("right_dexhand", 2, 830, True),   # G4 延长一拍
]
```

### 调度原理

```
apscheduler (BackgroundScheduler)
    │  每 INTERVAL=0.7s 触发一次
    ▼
hit_next() (在 hit_next 内部)
    │  1. 解析 EVENTS[idx]
    │  2. hit() 发送按下指令 (generateDexhandCommands)
    │  3. sleep(RESET_DELAY) 等待手指松开
    │  4. hit() 发送归位指令
    │  5. 如果是延长音，额外 sleep(INTERVAL) 让下一个事件延后 1 拍
    │  6. 延长音结束后显式归位（避免跨手"幽灵按下"）
    │  7. idx += 1
    ▼
finished.set() (idx >= MAX 时)
    │
    ▼
main() finally 块：恢复全张开 + 关闭 scheduler + 释放机器人
```

### 关键 Bug 修复（piano_extended.py）

**问题 1：延长音"幽灵按下"**

修复前：第 1 段末尾的延长音 G4 让右食指持续按下 2 拍。整段第 2 段 7 个事件全在左手操作，右食指**没被显式归位**，直到第 3 段第一个 `("right_dexhand", 2, 830)` 事件触发 —— 此时右食指已经停在 830，发送"按到 830"是**空指令**（舵机已到位），所以听到"5 5 4 4..."时第一个 5 没声音。

修复：在延长音 2 拍 hold 结束后，**显式调用 hit() 归位**：
```python
if is_extended:
    time.sleep(INTERVAL)
    hit(robot, end_effector, generateDexhandCommands(0, 1000))   # 强制归位
```

**问题 2：同手同指连按时"空指令"**

如果不做智能归位，G4 → G4 连按时舵机不归位就发新指令，是空指令。

修复：`RESET_ON_EVERY_HIT=True`（默认）+ 智能判断（同手同指连按时强制归位）。

## 文件说明

| 文件 | 说明 |
|---|---|
| `piano_extended.py` | **推荐使用**。支持延长音、跨手自动归位，6 段完整小星星 |
| `piano_test.py` | 早期版本，3-tuple 事件格式，无延长音支持 |

## 参数调优

| 参数 | 默认 | 说明 |
|---|---|---|
| `INTERVAL` | 0.7 | 一拍时长（秒），决定整体速度 |
| `RESET_DELAY` | 0.6 | 按下后多久归位（需 ≥ 灵巧手物理松开时间） |
| `RESET_ON_EVERY_HIT` | True | True=每次都归位；False=智能判断 |

如果发现手指"按不动"或"重叠"，调整 `RESET_DELAY`（建议 0.5~0.7）。

## 扩展方向

- **更多曲目**：把 EVENTS 换成其它简谱（茉莉花、生日快乐等）
- **力度控制**：用 `JointCommand` 的 `effort` 字段实现强弱拍
- **状态反馈**：用 `get_dexhand_state` 读取实际位置做闭环
- **真实节拍**：当前每个音 1 拍简化处理，可扩展支持八分音符（duration=0.5）

## License

MIT

## 致谢

- [因时机器人 Inspire](https://www.inspire-robots.com/) — RH56 灵巧手
- [Galbot](https://www.galbot.com/) — G1 机器人 SDK
