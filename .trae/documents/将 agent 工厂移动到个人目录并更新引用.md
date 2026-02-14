## 目标（按你的要求）
- 把各角色/工蚁的“工厂实现文件”移动到对应个人目录。
- **旧位置的工厂文件直接删除**（不保留任何 shim/转发文件）。
- 同步更新所有引用路径，确保启动与导入不报错。

## 将要执行的迁移（实现文件 → 个人目录）
### Queen / King / Soldier
- 移动：
  - `agents/queen/agent.py` → `agents/queen/Queen_Sera/agent.py`
  - `agents/king/agent.py` → `agents/king/King_Tru/agent.py`
  - `agents/soldier/agent.py` → `agents/soldier/Soldier_Ares/agent.py`
- 删除旧文件：以上 3 个旧路径文件删除。
- 更新引用：
  - `agents/queen/__init__.py` 改为 `from agents.queen.Queen_Sera.agent import create_queen_agent`
  - `agents/king/__init__.py` 改为 `from agents.king.King_Tru.agent import create_king_agent`
  - `agents/soldier/__init__.py` 改为 `from agents.soldier.Soldier_Ares.agent import create_soldier_agent`

### Workers
- 移动：
  - `agents/worker/emotion_worker.py` → `agents/worker/Worker_Light/agent.py`
  - `agents/worker/browser_worker.py` → `agents/worker/Worker_Nova/agent.py`
  - `agents/worker/doc_worker.py` → `agents/worker/Worker_Reed/agent.py`
- 删除旧文件：以上 3 个旧路径文件删除。
- 更新引用：
  - `agents/worker/__init__.py` 改为从新路径导入三个 `create_*_agent`（函数名保持不变，避免上层调用改动）。

## Python 可导入性（保证新路径能 import）
- 为每个个人目录新增 `__init__.py`：
  - `agents/queen/Queen_Sera/__init__.py`
  - `agents/king/King_Tru/__init__.py`
  - `agents/soldier/Soldier_Ares/__init__.py`
  - `agents/worker/Worker_Light/__init__.py`
  - `agents/worker/Worker_Nova/__init__.py`
  - `agents/worker/Worker_Reed/__init__.py`

## 全仓引用清零与验证
- 全仓搜索确保不存在以下旧导入：
  - `agents.queen.agent` / `agents.king.agent` / `agents.soldier.agent`
  - `agents.worker.browser_worker` / `agents.worker.doc_worker` / `agents.worker.emotion_worker`
- 运行最小导入验证：import `services.workflow`、`services.runtime`、`agents.queen/king/soldier/worker`，确认无 ImportError。
- 可选清理：删除旧文件对应的 `__pycache__/*.pyc`（不影响功能，但避免误导）。