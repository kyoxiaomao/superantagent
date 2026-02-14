# SuperAntAgent（多智能体桌宠系统）

本项目基于 AgentScope + Tkinter + Pillow 构建一个“桌宠式多智能体群聊系统”。系统内置蚁王/蚁后/兵蚁/工蚁等角色：用户通过 UI 或终端输入消息，蚁后负责直接回复；蚁王在后台周期性 `update()` 做群聊话题与任务调度；长期记忆通过独立进程 `chromaserver` 托管（Chroma 持久化 + JSONL 回退 + ReMe/FlowLLM 记忆栈）。

## 1. 环境要求

- Python：建议 3.10+（代码中使用了 `X | None` 等语法）
- 系统依赖：
  - Windows：一般自带 Tkinter（可直接运行 UI）
  - Ubuntu/Debian：需要安装 Tk 组件，例如 `sudo apt-get install -y python3-tk`
- 网络：需要可访问模型服务的网络环境

## 2. 安装

在项目根目录执行（Windows PowerShell 示例）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

依赖声明在 [requirements.txt](file:///d:/SuperAntAgent/requirements.txt)。

## 3. 配置（.env）

1）复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

2）编辑 `.env`，至少补齐一个可用的模型 API Key。

### 3.1 关键环境变量说明

- `DASHSCOPE_API_KEY`：当前仓库默认模型配置会优先从该变量读取 API Key（见 [model_configs.yaml](file:///d:/SuperAntAgent/configs/model_configs.yaml) 与 [model_manager.py](file:///d:/SuperAntAgent/models/model_manager.py)）。  
  - 说明：`configs/model_configs.yaml` 中 `llm.api_key_env` 默认写的是 `DASHSCOPE_API_KEY`，因此即使 `.env.example` 里提供了 `GLM_API_KEY/QWEN_API_KEY` 示例，你仍需要确保 `DASHSCOPE_API_KEY` 有值，或自行修改 `configs/model_configs.yaml` 的 `api_key_env`。
- `GLM_API_KEY`：当 `configs/model_configs.yaml` 的 `llm.api_key_env` 指向它时生效。
- `QWEN_MODEL`：`chromaserver` 内部构建记忆栈时使用的对话模型名（见 [service.py](file:///d:/SuperAntAgent/chromaserver/service.py)）。
- `ANT_USER_ID`：向量库 workspace 的用户前缀（默认 `local_user`）。

### 3.2 配置文件说明（常改项）

- [configs/model_configs.yaml](file:///d:/SuperAntAgent/configs/model_configs.yaml)：对话模型/嵌入模型的加载配置（`api_key_env/base_url/model_name` 等）。
- [configs/agent_configs.yaml](file:///d:/SuperAntAgent/configs/agent_configs.yaml)：角色名称、心跳参数、每个角色使用哪个模型 provider。
- [configs/vector_server.yaml](file:///d:/SuperAntAgent/configs/vector_server.yaml)：主程序/管理 UI 访问向量库服务的地址（如 `http://127.0.0.1:8765`）。

## 4. 启动方式

本项目常见有 3 种启动方式：向量库服务（独立进程）、UI 群聊、终端模式。建议先启动向量库服务，再启动 UI/终端（这样可正常预热长期记忆）。

### 4.1 启动向量库服务（chromaserver）

在项目根目录执行：

```powershell
.\.venv\Scripts\Activate.ps1
python -m chromaserver.server
```

- 默认监听：`ANT_VECTOR_SERVER_HOST`（默认 `127.0.0.1`）、`ANT_VECTOR_SERVER_PORT`（默认 `8765`）
- 日志：`chromaserver/_logs/`
- 数据目录（默认）：`chromaserver/data/chroma_vector_store`、`chromaserver/data/jsonl_storage`

更多说明见 [chromaserver/README.md](file:///d:/SuperAntAgent/chromaserver/README.md)。

### 4.2 启动 UI 群聊（桌宠界面）

```powershell
.\.venv\Scripts\Activate.ps1
python -m ui.chat_app
```

UI 启动后会在后台线程初始化 AgentScope 与运行时，并把输出通过 DataCenter 转成 UI 事件流渲染。

### 4.3 启动终端模式

```powershell
.\.venv\Scripts\Activate.ps1
python role_editor.py
```

启动后在终端输入文本，蚁后会直接回复；输入 `exit` 退出。

## 5. 可选管理工具

- 向量库管理 UI：  
  ```powershell
  python -m ui.chroma_admin
  ```
  用于配置服务地址、启动/停止服务、初始化/清库、查看 workspace 信息。
- 角色配置编辑器：  
  ```powershell
  python -m ui.role_editor
  ```
- 技能/工具加载器：  
  ```powershell
  python -m ui.skill_tool_loader
  ```

## 6. 数据与日志目录

- 群聊与 user-queen 对话日志（jsonl）：`message/chatdata/`
- 运行事件日志：`logs/`
- 向量库服务日志：`chromaserver/_logs/`
- 向量库数据：`chromaserver/data/`
- 第三方库日志（部分场景写入）：`memory/vector_store/_logs/`

## 7. 常见问题（排障）

- 启动时报 `未配置 api_key 或 DASHSCOPE_API_KEY`：检查 `.env` 是否设置了 `DASHSCOPE_API_KEY`，或修改 `configs/model_configs.yaml` 的 `api_key_env` 指向你实际使用的环境变量。
- UI 能打开但提示“后台运行时仍在初始化”：属于正常现象；模型加载、角色创建、向量库预热可能耗时。可查看 `logs/` 与 `memory/vector_store/_logs/`。
- 向量库预热失败/跳过：
  - 未配置 `configs/vector_server.yaml` 或服务未启动
  - 先启动 `python -m chromaserver.server` 再启动 UI/终端
  - 查看 `chromaserver/_logs/`
- Linux 下 `import tkinter` 失败：安装 `python3-tk` 后重试。
