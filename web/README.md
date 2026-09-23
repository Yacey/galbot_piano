# QZ Piano 网页控制器

一个运行在机器人 Orin 上的网页,用于:

- 查看机器人连接状态 (TCP 探活 / 关键 systemd 服务 / 关键进程)
- 查看 `loop_player.py` 与三首钢琴脚本当前是否运行 (含外部 SSH 启动的进程)
- 一键运行 / 停止 `loop_player.py` / `molihua_hand_move.py` / `farewell.py` / `backlighting.py`
- **复位**: 先把双臂移动到 `FIRST_POSITION_LEFT/RIGHT`,再调用 `motion.move_whole_body_joint_zero()`
- 实时 tail 每个脚本的 stdout/stderr 日志
- 列出机器人所有可访问 IP (LAN 内任何设备都能用)

实现参考 `/userdata/guader/galbot_controller_web/src/controller_server.py`,
但本控制器:

- 不调用 `galbot_sdk`,仅负责起停 piano / reset 子进程 (脚本自身会 `robot.init / motion.init`)
- 不引入 `websockets`,状态/日志均通过 HTTP 轮询
- 仅依赖 Python 3.8+ 标准库,无需 `pip install`
- 监听端口 `8088`,与原 controller_server (`8080`/`8765`) 可并存
- 子进程自动注入 SDK 环境变量 (见下文)

## 运行方式

```bash
cd /userdata/cx/QZ_Piano/web
python3 server.py
```

启动后日志会打印前端地址,例如:

```
[2026-09-23 10:30:00] 前端: http://10.130.78.118:8088
```

在 PC 浏览器打开该地址即可。`Ctrl+C` 结束服务,会先向所有由本控制器启动的
子进程发 SIGINT (→ SIGTERM → SIGKILL) 再退出。

如需换端口:
```bash
python3 server.py --port 9090 --host 0.0.0.0
```

## LAN 访问

服务绑定 `0.0.0.0:8088`,机器人上所有非回环 IPv4 接口都会接受连接。
页面顶部「服务器信息 → LAN 访问地址」会列出当前所有接口 URL,
例如 (典型配置):

| 接口 | 用途 | URL |
|---|---|---|
| `eth0` 192.168.100.88 | 上层/管理网 | http://192.168.100.88:8088 |
| `eth1` 192.168.1.88 | 控制网 / HPU | http://192.168.1.88:8088 |
| `wlan0` 10.130.78.118 | wifi 管理网 | http://10.130.78.118:8088 |
| `docker0` 172.17.0.1 | docker 桥接 | http://172.17.0.1:8088 |

每个条目右侧有「复制」按钮可一键复制 URL。

## 复位功能

**复位 (Reset)** 单独成组,与其他运行中的脚本互斥。点击后弹二次确认:

1. `motion.inverse_kinematics(FIRST_POSITION_LEFT, [left_arm])` 求 IK
2. `robot.set_joint_positions(...)` 阻塞移动左手到 FIRST_POSITION
3. 同理移动右手
4. 等待 `--wait` 秒 (默认 5s) 让手臂稳定
5. `motion.move_whole_body_joint_zero()` 全身归零
6. 等待 5s 让归零动作完成
7. `robot.destroy()` 释放资源

CLI 参数透传支持:
- `--speed 0.5` — 关节速度 (rad/s), 默认 0.5
- `--wait 5.0` — FIRST_POSITION 到位后等待秒数, 默认 5.0
- `--no-first-position` — 跳过 FIRST_POSITION, 直接全身归零
- `--no-whole-body-zero` — 只到 FIRST_POSITION, 不归零

实现脚本: `/userdata/cx/QZ_Piano/main_code/Tools/reset_to_initial.py`
参考: `/userdata/cx/tutorials/galbot_piano/Tools/set_whole_joints_to_zero.py`

## 互斥与防误操作

- `loop_player.py` 自己会依次跑三首曲子 (占用机器人)
- 三首单曲与 loop_player 同时跑会争用机器人,**拒绝**同时启动
- 复位与所有钢琴脚本互斥 (复位时机器人不能被其他任务占用)
- 如果检测到外部 (非本控制器) 已经在跑 piano 相关脚本,本控制器也会
  拒绝启动,需要先用 SSH 等方式停掉
- 「停止」只对**本控制器启动的**脚本生效,无法停止外部进程
- 复位按钮弹出二次确认,提示检查周围障碍 / 急停可用 / 其他任务

## SDK 环境变量注入

子进程 (钢琴 / 复位脚本) 需要 `galbot_sdk`,它位于
`/data/galbot/lib/galbot_sdk`。用户在 SSH 终端里 `~/.bashrc`
会自动 source 并设置 `PYTHONPATH` / `LD_LIBRARY_PATH` /
`PATH`,但 `subprocess.Popen` 不经过 bashrc,所以本控制器在
`start_script()` 里给子进程显式注入:

```python
SDK_ENV = {
    "PYTHONPATH": "/data/galbot/lib",
    "LD_LIBRARY_PATH": "/data/galbot/lib:/home/galbot/.local/lib",
    "PATH": "/data/galbot/lib/galbot_sdk/bin",
}
```

如果机器人 SDK 路径不同,直接修改 `server.py` 里的 `SDK_ENV` 常量。

## 机器人状态判定逻辑

总体 "已连接" 需要同时满足:

1. `192.168.1.88:50050` 或 `192.168.1.88:50051` TCP 可达 (任一即可)
2. `pgrep service_motion_plan` 能找到进程
3. `systemctl is-active galbot_daemon.service` 为 active

任意一项不满足则总体判为异常,具体哪一项坏了会在面板里高亮显示。

## CLI 参数透传

`loop_player.py` 支持 `--rounds / --playlist / --once / --list`。
如需从 Web 透传,可在 `POST /api/run` 的 JSON body 加 `"args": ["..."]`,
例如:

```bash
curl -X POST http://127.0.0.1:8088/api/run \
  -H 'Content-Type: application/json' \
  -d '{"name":"loop_player.py","args":["--rounds","2","--playlist","1"]}'
```

前端默认不暴露这些参数,需要时改 `index.html` 即可。

## 文件结构

```
/userdata/cx/QZ_Piano/web/
├── server.py                # HTTP 服务 (stdlib, 22KB)
├── README.md                # 本文件
├── static/
│   └── index.html           # 前端 (vanilla HTML+CSS+JS, 23KB)
└── logs/                    # 运行后自动创建
    ├── server.log           # 服务自身日志
    └── <script>.log         # 每个脚本的 stdout/stderr
    └── reset_to_initial.py.log  # 复位的日志 (含 [env] 行)

/userdata/cx/QZ_Piano/main_code/
├── loop_player.py           # 歌单编排器 (用户原有)
├── molihua_hand_move.py     # 茉莉花 (用户原有)
├── farewell.py              # 送别 (用户原有)
├── backlighting.py          # 逆光 (用户原有)
└── Tools/
    └── reset_to_initial.py  # 复位脚本 (本控制器新增)
```

## 已知边界

- 不写 systemd service (避免无授权改全局配置)
- 不主动停外部进程 (避免误杀用户手动启动的 session)
- TCP 探测会创建短连接到控制端口,不读协议,仅判断端口可达
- 不代理相机/关节数据 (与原 controller_server 职责分工)
- SDK 路径写死为 `/data/galbot/lib`;若机器人 SDK 位置不同需改 `server.py`
