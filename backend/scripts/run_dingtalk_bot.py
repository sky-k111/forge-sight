"""启动钉钉 Stream 机器人和持久任务 worker。"""

import logging
import sys
from pathlib import Path

# 支持从 backend/ 直接执行 `python scripts/run_dingtalk_bot.py`。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.integrations.dingtalk_runtime import main


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    main()
