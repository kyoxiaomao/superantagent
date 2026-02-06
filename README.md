# 蚂蚁多智能体桌宠 (Ant Multi-Agent Desktop Pet)

基于 AgentScope 框架开发的桌面多智能体桌宠系统。

## 快速开始

1.  激活虚拟环境：
    ```bash
    # Windows (PowerShell)
    .\.venv\Scripts\Activate.ps1
    ```
2.  安装依赖：
    ```bash
    pip install -r requirements.txt
    ```
3.  配置环境变量：
    复制 `.env.example` 为 `.env` 并填入 API Key。
4.  运行：
    ```bash
    python main.py
    ```

## 启动清理（便于测试）

设置环境变量 `ANT_RESET_ON_START=true` 后，每次启动会清除上一轮动态产生的数据（记忆/动态技能/生成文档/部分日志），便于重复测试。

PowerShell 示例：

```powershell
$env:ANT_RESET_ON_START="true"
python main.py
```

UI 模式同理：

```powershell
$env:ANT_RESET_ON_START="true"
python -m ui.chat_app
```

## 目录结构
目前仓库以代码为准，核心入口与目录：

- [main.py](file:///d:/antagent/main.py)：终端模式入口（常驻群聊运行时）
- [services/runtime.py](file:///d:/antagent/services/runtime.py)：常驻 MsgHub + 心跳调度 + 工具缺失闭环
- [agents/](file:///d:/antagent/agents)：五角色智能体
- [agents/skills/](file:///d:/antagent/agents/skills)：兵蚁生成的技能脚本与文档
- [ui/](file:///d:/antagent/ui)：桌面UI（Tkinter）

## GLM-4.5-Air 模型适配

已提供基于智谱 HTTP API 的模型适配器 [glm_chat_model.py](file:///d:/antagent/services/glm_chat_model.py)，用于在 AgentScope 中以 `ChatModelBase` 方式接入 GLM。

```python
import os
from services.glm_chat_model import GLMChatModel

model = GLMChatModel(
    model_name=os.getenv("GLM_MODEL", "glm-4.5-air"),
    api_key=os.getenv("GLM_API_KEY"),
    base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    stream=False,
)
```

## UI 对话框（Tkinter）

提供一个简单的桌面对话框，用于显示群聊消息并发送用户输入。头像资源暂用 `ui/resources/animations` 下的图片。

启动方式：

```bash
python -m ui.chat_app
```

实现要点：

- tkinter：UI 主线程负责窗口与渲染（Text + Scrollbar + Entry）
- Pillow：加载与缩放 PNG 头像（PhotoImage 缓存避免闪烁）
- asyncio + 后台线程：在后台运行 ColonyRuntime，持续心跳与群聊不阻塞 UI
- 线程安全队列：将 AgentScope 的“打印消息队列”转发到 UI 线程，用 Tk `after()` 定时刷新

## 事件日志（全量回放）

除 AgentScope 默认日志外，项目会额外输出一份“结构化事件日志”（JSONL），用于按时间回放每个 agent 的状态、心跳、消息与工具调用。

- 文件位置：`./logs/events_<ui|terminal>_YYYYmmdd_HHMMSS.jsonl`
- 单行格式（JSON）：`ts/run/level/agent/event_type/payload`
- 覆盖事件：
  - `agent_status`：busy/idle
  - `heartbeat`：心跳触发
  - `user_text`：用户输入
  - `dispatch` / `dispatch_tool_create` / `planning_start` / `planning_done`
  - `observe` / `pre_reply` / `post_reply` / `print`（包含 tool_use/tool_result/thinking/text blocks，内容会做长度裁剪）
  - `error` / `runtime_crash`


.\.venv\Scripts\Activate.ps1; python -m ui.chat_app
.\.venv\Scripts\Activate.ps1; python -m ui.role_editor