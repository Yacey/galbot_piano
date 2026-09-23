#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按需重放单首曲子：在执行原脚本前注入 PLAYBACK_REPEAT_COUNT。

用法：
    python3 Tools/run_score.py --script <name> [--repeat N]

参数
----
  --script NAME   要执行的原脚本名（molihua_hand_move.py / farewell.py / backlighting.py）
  --repeat  N     覆盖原脚本顶部的 PLAYBACK_REPEAT_COUNT；不传则保留原默认值

实现思路
--------
  读原脚本源（用正则替换 'PLAYBACK_REPEAT_COUNT = K' 为新的值），
  再 exec 到 __main__ 命名空间。**不修改原文件**，原脚本行为与手动改默认值一致。

退出码
------
  0   成功
  1   执行异常
  2   参数错误（找不到脚本等）
  130 Ctrl+C
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 钢琴脚本所在目录（与 loop_player.py / 三首曲子同级）
SCRIPT_DIR = Path("/userdata/cx/QZ_Piano/main_code")


def main() -> int:
    p = argparse.ArgumentParser(description="按需重放单首曲子")
    p.add_argument("--script", required=True,
                   help="原脚本名（molihua_hand_move.py / farewell.py / backlighting.py）")
    p.add_argument("--repeat", type=int, default=None,
                   help="覆盖 PLAYBACK_REPEAT_COUNT；不传则保留原默认")
    args = p.parse_args()

    target = SCRIPT_DIR / args.script
    if not target.is_file():
        print("[wrapper] 脚本不存在: {}".format(target), file=sys.stderr)
        return 2

    src = target.read_text(encoding="utf-8")

    if args.repeat is not None:
        # 只替换顶层 'PLAYBACK_REPEAT_COUNT = 数字'（不替换注释/字符串里出现的位置）
        new_src, n = re.subn(
            r'PLAYBACK_REPEAT_COUNT\s*=\s*\d+',
            'PLAYBACK_REPEAT_COUNT = {}'.format(args.repeat),
            src,
            count=1,
        )
        if n == 0:
            print("[wrapper] 警告: 原脚本未找到 PLAYBACK_REPEAT_COUNT 常量，--repeat 无效")
        else:
            print("[wrapper] 注入 PLAYBACK_REPEAT_COUNT = {} (原文件未修改)".format(args.repeat))
            src = new_src
    else:
        print("[wrapper] 使用原脚本默认 PLAYBACK_REPEAT_COUNT")

    # 也设置环境变量，方便未来想用 os.environ 读的脚本
    os.environ["QZ_REPEAT_COUNT"] = str(args.repeat if args.repeat is not None else "")

    # 以 __main__ 身份 exec 原脚本
    ns = {"__name__": "__main__", "__file__": str(target)}
    try:
        exec(compile(src, str(target), "exec"), ns)
    except KeyboardInterrupt:
        print("\n[wrapper] Ctrl+C, 退出")
        return 130
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception as e:
        print("[wrapper] 异常: {}: {}".format(type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
