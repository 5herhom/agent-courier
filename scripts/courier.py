"""未安装 wheel 时的源码入口；使用稳定的脚本绝对路径调用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_courier.cli import main

raise SystemExit(main())
