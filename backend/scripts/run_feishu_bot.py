"""启动飞书企业自建应用机器人长连接。"""

import sys
from pathlib import Path

# 支持从 backend/ 直接执行 `python scripts/run_feishu_bot.py`。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.integrations.feishu_p2p import main


if __name__ == "__main__":
    main()
