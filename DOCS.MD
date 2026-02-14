# SuperAntAgent 产品技术文档（文件级）

## 0. 文档目标与读者

- 面向读者：产品/研发/测试/运维（本仓库为本地桌面应用为主，部署侧以“本机运行”居多）
- 文档目标：
  - 说明本项目“是什么、解决什么问题、怎么跑起来”
  - 用模块化方式梳理整体架构与关键数据流
  - 目录精细到每个代码/配置/说明文件，并简述文件内功能
  - 对核心模块给出“完整运行链路”（入口 → 关键函数调用 → 外部依赖/落盘/网络边界）

## 1. 产品概述

### 1.1 产品定位

SuperAntAgent 是一个桌宠式多智能体群聊系统：在一个 Tkinter UI 窗口里展示“全员群聊”与各角色独立视图，用户输入后由蚁后直接回复；蚁王等角色通过周期性 `update()` 心跳在后台维持状态与（未来可扩展的）群聊话题/任务调度。

### 1.2 角色与分工（产品语义）

- 蚁后（queen）：主对话入口，用户输入直达蚁后，蚁后直接回复用户（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L322-L350)）。
- 蚁王（king）：偏“调度者/话题管理者”，主要通过 `update()` 读取群聊增量并在需要时发言（见 [king/agent.py](file:///d:/SuperAntAgent/agents/king/agent.py#L31-L46)）。
- 兵蚁（soldier）：偏“技能工件管理者”，提供保存技能脚本/文档与列出技能工件工具（见 [soldier/agent.py](file:///d:/SuperAntAgent/agents/soldier/agent.py#L18-L49)）。
- 工蚁（workers）：
  - 浏览器工蚁：加载并执行浏览器相关技能（动态技能脚本位于 `agents/skills/browser_worker/`）。
  - 文档工蚁：加载并执行文档相关技能（动态技能脚本位于 `agents/skills/doc_worker/`）。
  - 情感工蚁：偏陪伴与情绪表达（无工具注册，见 [emotion_worker.py](file:///d:/SuperAntAgent/agents/worker/emotion_worker.py)）。

### 1.3 核心能力清单（产品视角）

- 桌面 UI 群聊：展示群聊消息、各 agent 状态卡（忙闲/心跳）与数据库面板入口（见 [ui/chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py) + [ui/chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py)）。
- 多智能体运行时：启动群聊会话、启动各角色心跳与更新调度、对外提供 `submit_user_text()`（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py)）。
- 动态技能闭环：缺工具 → 兵蚁生成脚本 → 写入 `agents/skills/` → 工蚁加载并注册到 Toolkit（见 [workflow.py](file:///d:/SuperAntAgent/services/workflow.py) + [skill_loader.py](file:///d:/SuperAntAgent/services/skill_loader.py)）。
- 长期记忆：通过独立进程 `chromaserver` 托管 Chroma 向量库与 ReMe 记忆栈，主程序通过 HTTP 调用写入/检索（见 [chromaserver/server.py](file:///d:/SuperAntAgent/chromaserver/server.py) + [chromaserver/client.py](file:///d:/SuperAntAgent/chromaserver/client.py) + [memory/lazy_reme_memory.py](file:///d:/SuperAntAgent/memory/lazy_reme_memory.py)）。
- 管理工具：向量库管理 UI、角色配置编辑器（见 `ui/chroma_admin.py`、`ui/role_editor.py`）。

## 2. 总体架构

### 2.1 进程与边界

本项目常见运行形态是“多进程/多线程”组合：

- 进程 A：主程序（UI 或终端）
  - UI 模式：Tk 主线程渲染 + 后台线程跑 asyncio event loop（详见 §3.1）
  - 终端模式：单进程 asyncio（详见 §3.2）
- 进程 B：向量库服务 `chromaserver`（HTTP 服务 + 内部 asyncio worker 线程，详见 §3.3）

关键边界：

- UI 与后台运行时：通过 `DataCenter` 事件队列隔离（避免跨线程直接操作 Tk 控件）
- 主程序与长期记忆：通过 `chromaserver` 的 HTTP API 隔离（主进程不直接打开 Chroma）

### 2.2 关键数据流（概念图）

1）UI → Runtime：

- 用户在 UI 输入 → `ChatDialog` 回调 `on_send()` → `AsyncColonyBridge.submit_user_text()` → 投递到后台 asyncio loop → `ColonyRuntime.submit_user_text()` → 蚁后 LLM 推理

2）Runtime → UI：

- 蚁后输出通过 AgentScope print 队列 → `AsyncColonyBridge._forward_printing_messages()` → `DataCenter.push_runtime_message()` → UI 主线程 `pump_events()` 拉取 `DataCenter.poll_events()` → 渲染到 `ChatDialog`

3）长期记忆：

- 运行时预热：`runtime.warmup_long_term_memory()` 读取 `configs/vector_server.yaml` 的服务地址，探活后对各 agent 的 `_ant_long_term_memory.ensure_ready()` 执行预热（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L183-L257)）
- 实际读写：主程序通过 `LazyReMeUnifiedLongTermMemory` 走 `RemoteVectorStoreClient` HTTP 调用 `/call`（见 [lazy_reme_memory.py](file:///d:/SuperAntAgent/memory/lazy_reme_memory.py#L34-L75)）

## 3. 完整运行链路（按场景）

本节以“可复现”为标准，逐条写清楚入口命令 → 关键对象创建 → 调用链 → 落盘/网络边界。

### 3.1 UI 群聊链路（完整）

**入口命令**

- `python -m ui.chat_app`

**链路步骤**

1. UI 主线程启动与资源初始化  
   - 入口函数：`ui.chat_app.main()`（见 [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py#L78-L259)）  
   - 读取 `.env`：`load_dotenv(<repo>/.env)`  
   - 清理历史聊天（仅 user_queen 与 group_chat jsonl）：`_清理蚁后聊天记录()`  
   - 启动 `DataCenter.start()` 读取静态角色信息（来自 `configs/agent_configs.yaml`）
2. 启动后台桥接线程（asyncio）  
   - `AsyncColonyBridge.start()` 创建后台线程（见 [async_bridge.py](file:///d:/SuperAntAgent/ui/async_bridge.py#L139-L158)）  
   - 后台线程入口 `_run_loop()` → `asyncio.new_event_loop()` → `loop.run_until_complete(_run_async())`
3. 后台初始化 AgentScope 与 colony/runtime  
   - `_run_async()` 执行：清理 → `agentscope.init()` → 加载模型 → `create_colony()` → `ColonyRuntime.start()`（见 [async_bridge.py](file:///d:/SuperAntAgent/ui/async_bridge.py#L238-L303)）  
   - 模型加载：`services.load_model_bundles(configs/model_configs.yaml)`（见 [model_manager.py](file:///d:/SuperAntAgent/models/model_manager.py#L86-L176)）  
   - 角色创建：`services.workflow.create_colony()`（见 [workflow.py](file:///d:/SuperAntAgent/services/workflow.py#L107-L118)）  
   - 运行时启动：`ColonyRuntime.start()` 打开群聊与启动 update 调度/心跳监控（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L121-L182)）
4. AgentScope print 队列回流到 DataCenter  
   - 后台创建 `print_queue` 并对每个 agent 执行 `agent.set_msg_queue_enabled(True, print_queue)`（见 [async_bridge.py](file:///d:/SuperAntAgent/ui/async_bridge.py#L289-L348)）  
   - `_forward_printing_messages()` 将 Msg 转成 DataCenter 原始事件（`runtime_msg`）
5. UI 主线程事件泵与渲染  
   - `pump_events()` 每 80ms 执行：`data_center.poll_events()` → 分发到 `_渲染事件()`（见 [chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py#L140-L255)）  
   - `ChatDialog` 负责渲染群聊/单 agent 视图、状态卡、流式增量追加（见 [chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py)）
6. 用户发送消息与蚁后回复（含流式）  
   - UI 输入 → `on_send()`：先 UI 本地追加用户消息，再：  
     - `data_center.push_ui_user_text()` 开启“蚁后流式聚合窗口”（见 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py#L114-L125)）  
     - `bridge.submit_user_text()` 投递到后台 loop（见 [async_bridge.py](file:///d:/SuperAntAgent/ui/async_bridge.py#L205-L214)）  
   - 后台 `runtime.submit_user_text()`：构造 `Msg(name="user", role="user", content=raw)` → `await queen(user_msg)` → 产出回复并发 UI 事件（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L322-L367)）  
   - 输出回流：蚁后输出进入 AgentScope print 队列，DataCenter 将其识别为 `user_reply_stream` 增量事件并输出给 UI（见 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py#L239-L244)）

**落盘与外部依赖**

- UI/运行时事件日志：`logs/events_ui_*.jsonl`（初始化见 [event_logger.py](file:///d:/SuperAntAgent/services/event_logger.py#L31-L40)）
- 聊天记录落盘：`message/chatdata/user_queen_*.jsonl`、`message/chatdata/group_chat_*.jsonl`（写入见 [group_chat.py](file:///d:/SuperAntAgent/message/group_chat.py#L176-L229)）
- 模型调用：由 `agentscope` + 模型适配器发起（对话模型见 [glm_chat_model.py](file:///d:/SuperAntAgent/models/glm_chat_model.py)；嵌入模型见 [embedding_model.py](file:///d:/SuperAntAgent/models/embedding_model.py)）
- 长期记忆：预热/读写走 `chromaserver` HTTP（详见 §3.3）

### 3.2 终端模式链路（完整）

**入口命令**

- `python main.py`

**链路步骤**

1. 入口初始化  
   - 读取 `.env`：`load_dotenv(<repo>/.env)`（见 [main.py](file:///d:/SuperAntAgent/main.py#L14-L16)）  
   - 初始化第三方日志（降噪、重定向到文件）：`_setup_quiet_third_party_logs()`
2. 初始化 AgentScope 与事件日志  
   - `agentscope.init(project=..., name=..., logging_level=...)`（见 [main.py](file:///d:/SuperAntAgent/main.py#L128-L133)）  
   - `init_event_logger(..., run_name="terminal")`（见 [main.py](file:///d:/SuperAntAgent/main.py#L124-L127)）
3. 加载模型与创建 colony/runtime  
   - `load_model_bundles(configs/model_configs.yaml)` → `create_colony(...)`（见 [main.py](file:///d:/SuperAntAgent/main.py#L135-L138)）  
   - `ColonyRuntime.start()` 打开群聊并启动 update/心跳（见 [main.py](file:///d:/SuperAntAgent/main.py#L138-L142)）
4. 终端输入循环  
   - `_input_loop()`：读取 stdin → `runtime.submit_user_text(text)` → 输出 `蚁后：...`（见 [main.py](file:///d:/SuperAntAgent/main.py#L145-L157)）

**落盘与外部依赖**

- 与 UI 模式一致：事件日志、聊天 jsonl、（可选）向量库预热/调用

### 3.3 向量库服务链路（chromaserver，完整）

**入口命令**

- `python -m chromaserver.server`

**链路步骤**

1. 启动 HTTP 服务  
   - `chromaserver.server.main()`：读取 `.env`、配置 host/port，创建 `ThreadingHTTPServer`（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L262-L274)）
2. 构建服务状态对象 `_State`  
   - `_State` 初始化 `ServerConfig.from_repo()`、`VectorStoreWorker`、`VectorDbAdmin`（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L137-L143)）
3. 处理请求（路由）  
   - `GET /health`：返回路径信息、worker 线程状态、pid（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L155-L176)）  
   - `GET /info`：调用 `VectorDbAdmin.get_info()` 返回 collection 列表与 count（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L178-L181)）  
   - `POST /init`：初始化各角色 workspace，可选预置 role cards（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L196-L203)）  
   - `POST /call`：统一 RPC 入口（ensure_ready/record/retrieve/record_to_memory/retrieve_from_memory）（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L204-L231)）  
   - `POST /shutdown`：请求服务退出（见 [server.py](file:///d:/SuperAntAgent/chromaserver/server.py#L232-L249)）
4. `VectorStoreWorker` 执行 RPC  
   - `VectorStoreWorker.call()`：确保 worker loop 就绪 → `_ensure_ready()` 获取/缓存记忆对象 → 调用对应方法（见 [service.py](file:///d:/SuperAntAgent/chromaserver/service.py#L67-L208)）
5. 构建 ReMe 统一记忆对象  
   - `_build_reme_memory(spec)`：构建 DashScopeChatModel + embedding_model → `ReMeUnifiedLongTermMemory(...)`（见 [service.py](file:///d:/SuperAntAgent/chromaserver/service.py#L508-L538)）

**关键约束**

- `/info` 返回真实的 Chroma collections 列表（遍历 `client.list_collections()` 并取 count + metadata.workspace_id），不是从 agent_configs 推导。<mccoremem id="03fkldd3doi58u75emhllv935" />
- 向量模型只使用 `qwen3-vl-embedding`（由配置与约束约定，见 [model_configs.yaml](file:///d:/SuperAntAgent/configs/model_configs.yaml)）。<mccoremem id="01KH6JH9BM4VNRBXE6YN16X87S" />

### 3.4 动态技能闭环链路（缺工具 → 生成 → 加载 → 执行）

说明：该闭环主要出现在“蚁王负责调度、工蚁负责执行”的任务编排模式中，代码入口集中在 `services/workflow.py`。

1. 蚁王输出调度 JSON / 或工具缺失触发  
   - `run_turn()` 解析蚁王输出的 JSON 并选择 `dispatch` / `tool_create` 分支（见 [workflow.py](file:///d:/SuperAntAgent/services/workflow.py#L121-L165)）
2. 调度工蚁执行前确保工具可用  
   - `_ensure_tool_ready()` 检查 `worker_agent.toolkit.tools` 是否存在目标工具  
   - 若不存在：广播“缺少工具”消息给群聊 → 再让蚁王决定生成工具参数 → 走 `tool_create`（见 [workflow.py](file:///d:/SuperAntAgent/services/workflow.py#L223-L250)）
3. 兵蚁生成并保存技能工件  
   - 兵蚁 toolkit 注册 `save_skill_script/save_skill_doc/list_skills` 三个工具（见 [soldier/agent.py](file:///d:/SuperAntAgent/agents/soldier/agent.py#L18-L60)）  
   - 实际落盘由 `services/skill_loader.safe_write_skill_file/safe_write_skill_doc` 完成（见 [skill_loader.py](file:///d:/SuperAntAgent/services/skill_loader.py#L19-L60)）
4. 工蚁动态加载技能  
   - `load_utils(toolkit, role_key=...)` 扫描该 role_key 对应的 agent_home 装备库：  
     - skills：遍历 `agent_home/skills/<skill>/skill.py`，加载模块并调用其 `register(toolkit)` 注册工具函数  
     - tools：扫描 `agent_home/tools/<tool>/tool.md | tool.json` 形成工具工件索引（供 UI 展示与后续编排执行）
5. 工蚁执行工具  
   - 工蚁 agent 的 toolkit 已包含新注册函数；后续 ReActAgent 会在推理中调用工具完成任务（工具调用块会在 `ant_agent_base` 的 pre_print hook 中被捕获并沉淀工具记忆，见 [ant_agent_base.py](file:///d:/SuperAntAgent/agents/base/ant_agent_base.py#L275-L301)）

### 3.5 消息落盘链路（群聊与 user-queen）

1. 群聊会话创建：`open_group_chat(chat_id="main", persist_dir=message/chatdata)`（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L141-L150) 与 [group_chat.py](file:///d:/SuperAntAgent/message/group_chat.py#L258-L275)）
2. 群聊消息落盘：`GroupChat.append_history()` 调用 `_append_jsonl()` 写入 `group_chat_YYYYMMDD.jsonl`（见 [group_chat.py](file:///d:/SuperAntAgent/message/group_chat.py#L60-L101)）
3. user-queen 对话落盘：`MessageCenter.append_user_queen()` 写入 `user_queen_YYYYMMDD.jsonl`（见 [group_chat.py](file:///d:/SuperAntAgent/message/group_chat.py#L185-L229)）

## 4. 目录与文件级说明（逐文件）

说明：本节按目录列出“代码/配置/说明文件”，每个文件给出职责简述 + 关键对象/函数 + 所属链路。

### 4.1 根目录（工程入口与全局文件）

- [.env](file:///d:/SuperAntAgent/.env)：本机运行环境变量（不应提交真实密钥；模板见 `.env.example`）。
- [.env.example](file:///d:/SuperAntAgent/.env.example)：环境变量模板（GLM/QWEN/ReMe/事件日志等）。
- [.gitignore](file:///d:/SuperAntAgent/.gitignore)：Git 忽略规则（包含虚拟环境/日志/数据等）。
- [main.py](file:///d:/SuperAntAgent/main.py)：终端模式入口；初始化 AgentScope/模型/colony/runtime；stdin 循环收发消息（链路：§3.2）。
- [requirements.txt](file:///d:/SuperAntAgent/requirements.txt)：Python 依赖列表（agentscope、Pillow、tk、python-dotenv、PyYAML、reme-ai、chromadb）。
- [README.MD](file:///d:/SuperAntAgent/README.MD)：安装与启动说明（本仓库新增文档）。
- [DOCS.MD](file:///d:/SuperAntAgent/DOCS.MD)：产品技术文档（本文件）。
- [.vscode/settings.json](file:///d:/SuperAntAgent/.vscode/settings.json)：VS Code 工作区配置（仅开发体验相关）。

### 4.2 Trae 工程规则（开发规范）

- [.trae/.ignore](file:///d:/SuperAntAgent/.trae/.ignore)：Trae IDE 忽略配置（与编辑器行为相关）。
- [.trae/rules/pr.md](file:///d:/SuperAntAgent/.trae/rules/pr.md)：仓库级开发规则（中文规范、依赖约束、严格模式等）。

### 4.3 configs/（运行配置）

- [configs/__init__.py](file:///d:/SuperAntAgent/configs/__init__.py)：配置包占位说明。
- [configs/global_config.yaml](file:///d:/SuperAntAgent/configs/global_config.yaml)：AgentScope 全局配置（如 logging_level）。
- [configs/agent_configs.yaml](file:///d:/SuperAntAgent/configs/agent_configs.yaml)：角色配置（显示名、max_iters、模型 provider、心跳参数等）。
- [configs/model_configs.yaml](file:///d:/SuperAntAgent/configs/model_configs.yaml)：模型配置（llm/embedding/multimodal_embedding 三段；api_key_env/base_url/model_name 等）。
- [configs/vector_server.yaml](file:///d:/SuperAntAgent/configs/vector_server.yaml)：向量库服务地址（`chromaserver` 的 base_url）。
- [configs/reme_config.yaml](file:///d:/SuperAntAgent/configs/reme_config.yaml)：ReMe/FlowLLM 记忆流配置（embedding/vector_store/flow pipeline 等）。
- [configs/prompts/__init__.py](file:///d:/SuperAntAgent/configs/prompts/__init__.py)：prompts 子包占位。
- [configs/prompts/system_prompts.yaml](file:///d:/SuperAntAgent/configs/prompts/system_prompts.yaml)：各 role 的 system prompt 文本（key=role_key）。

### 4.4 agents/（智能体与角色工厂）

- [agents/__init__.py](file:///d:/SuperAntAgent/agents/__init__.py)：agents 包说明。

**agents/base/**

- [agents/base/__init__.py](file:///d:/SuperAntAgent/agents/base/__init__.py)：导出 `create_react_ant_agent/load_agent_config`。
- [agents/base/ant_agent_base.py](file:///d:/SuperAntAgent/agents/base/ant_agent_base.py)：统一的 ReActAgent 工厂；加载角色配置与 system prompt；挂载短期/长期记忆；注册 hooks（日志、UI busy/idle、工具记忆沉淀）（链路：UI/终端均会走到这里）。

**agents/king/**

- [agents/king/__init__.py](file:///d:/SuperAntAgent/agents/king/__init__.py)：导出 `create_king_agent()`。
- [agents/king/agent.py](file:///d:/SuperAntAgent/agents/king/agent.py)：蚁王 agent 工厂；定义 `update(ctx)`（心跳上报 + 读取群聊增量）。
- [agents/king/tools/__init__.py](file:///d:/SuperAntAgent/agents/king/tools/__init__.py)：工具子包占位（当前无实际工具实现）。

**agents/queen/**

- [agents/queen/__init__.py](file:///d:/SuperAntAgent/agents/queen/__init__.py)：导出 `create_queen_agent()`。
- [agents/queen/agent.py](file:///d:/SuperAntAgent/agents/queen/agent.py)：蚁后 agent 工厂；定义 `update(ctx)`（心跳上报 + 读取群聊增量）；用户输入直达蚁后由 runtime 直接调用（链路：§3.1/§3.2）。
- [agents/queen/tools/__init__.py](file:///d:/SuperAntAgent/agents/queen/tools/__init__.py)：工具子包占位。

**agents/soldier/**

- [agents/soldier/__init__.py](file:///d:/SuperAntAgent/agents/soldier/__init__.py)：导出 `create_soldier_agent()`。
- [agents/soldier/agent.py](file:///d:/SuperAntAgent/agents/soldier/agent.py)：兵蚁工厂；注册技能工件写入工具（保存脚本/文档/列目录）；定义 `update(ctx)`。
- [agents/soldier/tools/__init__.py](file:///d:/SuperAntAgent/agents/soldier/tools/__init__.py)：工具子包占位。

**agents/worker/**

- [agents/worker/__init__.py](file:///d:/SuperAntAgent/agents/worker/__init__.py)：导出三类工蚁创建函数。
- [agents/worker/browser_worker.py](file:///d:/SuperAntAgent/agents/worker/browser_worker.py)：浏览器工蚁工厂；初始化时加载其个人装备库（skills/tools）；提供 reload/list 工具。
- [agents/worker/doc_worker.py](file:///d:/SuperAntAgent/agents/worker/doc_worker.py)：文档工蚁工厂；初始化时加载其个人装备库（skills/tools）；提供 reload/list 工具。
- [agents/worker/emotion_worker.py](file:///d:/SuperAntAgent/agents/worker/emotion_worker.py)：情感工蚁工厂；无专属工具，仅负责陪伴式回应。
- [agents/worker/tools/__init__.py](file:///d:/SuperAntAgent/agents/worker/tools/__init__.py)：工具子包占位。

**agents/**/<agent_home>/skills/**（agent 个人技能库，运行时可写入）

- 示例：`agents/worker/Worker_Light/skills/`、`agents/worker/Worker_Reed/skills/`（初始为空，`.gitkeep` 占位）
- 约定：每个技能一个独立目录 `agent_home/skills/<skill_key>/`，包含 `skill.py + skill.md`；`skill.py` 必须提供 `register(toolkit)`。
- agent_home 定位规则：通过 role_key 扫描 `agents/**/agent.py` 找到对应目录（见 [agent_home_locator.py](file:///d:/SuperAntAgent/utils/agent_home_locator.py#L10-L47)）。

**agents/**/<agent_home>/tools/**（agent 个人工具库/复合工具库，运行时可写入）

- 示例：`agents/worker/Worker_Light/tools/`、`agents/worker/Worker_Reed/tools/`（初始为空，`.gitkeep` 占位）
- 约定：每个工具一个独立目录 `agent_home/tools/<tool_key>/`，包含 `tool.md + tool.json`（结构见 [alltool/README.md](file:///d:/SuperAntAgent/utils/alltool/README.md#L1-L57)）。

### 4.5 services/（运行时、调度与系统服务）

- [services/__init__.py](file:///d:/SuperAntAgent/services/__init__.py)：服务层统一导出（模型加载与技能写入/加载等）。
- [services/workflow.py](file:///d:/SuperAntAgent/services/workflow.py)：colony 组装与“单轮编排执行器”；解析蚁王调度 JSON，完成 dispatch/tool_create/缺工具闭环（链路：§3.4）。
- [services/runtime.py](file:///d:/SuperAntAgent/services/runtime.py)：精简运行时；负责群聊会话生命周期、启动 update/心跳调度、用户输入直达蚁后与 UI 事件输出（链路：§3.1/§3.2）。
- [services/agent_update_scheduler.py](file:///d:/SuperAntAgent/services/agent_update_scheduler.py)：update 调度器；为每个 role 创建独立 asyncio task，按 interval 调用 `agent.update(ctx)`，并在崩溃时退避重启。
- [services/agent_heartbeat.py](file:///d:/SuperAntAgent/services/agent_heartbeat.py)：心跳中心；接收 `report_tick` 上报、判定健康变化并可向 UI 推送 `agent_health/error`。
- [services/event_logger.py](file:///d:/SuperAntAgent/services/event_logger.py)：结构化事件日志（JSONL）；提供 `log_event/log_msg`，支持裁剪与按 LOG_THINKING 控制思考日志。
- [services/skill_loader.py](file:///d:/SuperAntAgent/services/skill_loader.py)：agent 装备库（skills/tools）管理；安全写入 `agent_home/skills/` 并扫描加载到 toolkit（工具函数注册在 `register(toolkit)` 中完成）。
- [services/role_config_store.py](file:///d:/SuperAntAgent/services/role_config_store.py)：角色配置读写（读取/校验/原子写回 `agent_configs.yaml` 与 `system_prompts.yaml`），供 UI 编辑器使用。
- [services/startup_cleanup.py](file:///d:/SuperAntAgent/services/startup_cleanup.py)：启动清理；当 `ANT_RESET_ON_START=true` 时清理测试数据（memory/chatdata/docs/generated/logs 等），便于测试回归（不影响 agents/**/skills 与 agents/**/tools 装备库）。

### 4.6 ui/（桌面 UI 与桥接）

- [ui/__init__.py](file:///d:/SuperAntAgent/ui/__init__.py)：导出 `ChatDialog`。
- [ui/chat_app.py](file:///d:/SuperAntAgent/ui/chat_app.py)：UI 入口；主线程创建 UI + 周期性事件泵；后台桥接启动运行时；处理发送/刷新数据库按钮（链路：§3.1）。
- [ui/chat_dialog.py](file:///d:/SuperAntAgent/ui/chat_dialog.py)：Tk UI 组件；Notebook（主界面/数据库）；左侧列表（群聊与 agent）；右侧内容区；支持流式回复增量追加与状态卡更新。
- [ui/data_center.py](file:///d:/SuperAntAgent/ui/data_center.py)：数据调度中心；统一接收后台 print 消息与 `ui_event`；做去重/流式聚合；输出标准事件；加载历史 jsonl 回放到 UI。
- [ui/async_bridge.py](file:///d:/SuperAntAgent/ui/async_bridge.py)：UI ⇄ runtime 桥接；后台线程创建 asyncio loop 并初始化 AgentScope/colony/runtime；将 print 队列转发给 DataCenter。
- [ui/avatar_store.py](file:///d:/SuperAntAgent/ui/avatar_store.py)：头像加载与缓存（Pillow → Tk PhotoImage）。
- [ui/chroma_admin.py](file:///d:/SuperAntAgent/ui/chroma_admin.py)：向量库管理 UI；保存/检测服务地址；启动/停止 `chromaserver`；初始化/清理库；展示 `/info` 返回的 collections 计数；打开日志目录。
- [ui/role_editor.py](file:///d:/SuperAntAgent/ui/role_editor.py)：角色编辑器 UI；编辑 name/max_iters/heartbeat/system prompt；保存回 YAML（通过 `services/role_config_store`）。
- 技能/工具装配：已集成到聊天 UI 的“工具库”页签，通过 DataCenter 统一管理装配/卸载与搜索。
- [ui/components/__init__.py](file:///d:/SuperAntAgent/ui/components/__init__.py)：组件子包占位。

### 4.7 chromaserver/（向量库服务）

- [chromaserver/README.md](file:///d:/SuperAntAgent/chromaserver/README.md)：向量库服务说明（启动、目录、API 简表、管理 UI）。
- [chromaserver/__init__.py](file:///d:/SuperAntAgent/chromaserver/__init__.py)：包占位。
- [chromaserver/server.py](file:///d:/SuperAntAgent/chromaserver/server.py)：HTTP 服务入口与路由（/health /info /init /call /shutdown）（链路：§3.3）。
- [chromaserver/service.py](file:///d:/SuperAntAgent/chromaserver/service.py)：服务核心；worker loop/记忆对象缓存；init_db/get_info；构建 `ReMeUnifiedLongTermMemory`。
- [chromaserver/client.py](file:///d:/SuperAntAgent/chromaserver/client.py)：远程客户端；读取/写入 `configs/vector_server.yaml`；封装 HTTP 调用与 Msg/ToolResponse 编解码。
- [chromaserver/protocol.py](file:///d:/SuperAntAgent/chromaserver/protocol.py)：协议结构（VectorStoreSpec、RpcMethod、workspace_id → collection_name 映射）。
- [chromaserver/logger.py](file:///d:/SuperAntAgent/chromaserver/logger.py)：chromaserver 事件日志（jsonl）与第三方日志路径。

### 4.8 memory/（记忆实现与组合）

- [memory/__init__.py](file:///d:/SuperAntAgent/memory/__init__.py)：导出记忆相关能力（build_memory_bundle 等）。
- [memory/memory_manager.py](file:///d:/SuperAntAgent/memory/memory_manager.py)：记忆组合构建；为每个 agent 组装短期 InMemory 与长期 LazyReMe；配置压缩策略。
- [memory/lazy_reme_memory.py](file:///d:/SuperAntAgent/memory/lazy_reme_memory.py)：长期记忆的“远程懒加载包装”；通过 `RemoteVectorStoreClient` 调用 `chromaserver`。
- [memory/reme_unified_memory.py](file:///d:/SuperAntAgent/memory/reme_unified_memory.py)：统一长期记忆实现（ReMe 三类 + Chroma + JSONL 镜像）；实现向量 upsert/query、角色卡片写入、record_to_memory/retrieve_from_memory 等。
- [memory/jsonl_store.py](file:///d:/SuperAntAgent/memory/jsonl_store.py)：JSONL 镜像存储与关键词回退检索（抽关键词、打分排序）。
- [memory/long_term_memory.py](file:///d:/SuperAntAgent/memory/long_term_memory.py)：仅基于 JSONL 的简单长期记忆实现（当前更多用于备用/对照）。

### 4.9 message/（消息结构与群聊）

- [message/__init__.py](file:///d:/SuperAntAgent/message/__init__.py)：导出 Msg 构造与解析工具。
- [message/msg_protocol.py](file:///d:/SuperAntAgent/message/msg_protocol.py)：薄封装 `make_msg()`，统一 Msg 构造入口。
- [message/parser.py](file:///d:/SuperAntAgent/message/parser.py)：消息解析工具：`msg_to_text()`、`extract_first_json_obj()`、话题标签解析等。
- [message/group_chat.py](file:///d:/SuperAntAgent/message/group_chat.py)：群聊会话封装与落盘：GroupChat、GroupChatRegistry、MessageCenter、open_group_chat。

### 4.10 models/（模型适配与加载）

- [models/__init__.py](file:///d:/SuperAntAgent/models/__init__.py)：模型层导出（bundle 与 loader）。
- [models/model_manager.py](file:///d:/SuperAntAgent/models/model_manager.py)：读取 `configs/model_configs.yaml` 并构造可供 AgentScope 使用的模型对象；支持 `api_key`/`api_key_env` 两种来源。
- [models/glm_chat_model.py](file:///d:/SuperAntAgent/models/glm_chat_model.py)：GLM ChatModel 适配器（http chat/completions；支持流式、thinking、工具调用）。
- [models/embedding_model.py](file:///d:/SuperAntAgent/models/embedding_model.py)：DashScope `qwen3-vl-embedding` 的文本嵌入适配（内部转为多模态 embedding 调用以兼容 VL embedding）。
- [models/multimodal_embedding_model.py](file:///d:/SuperAntAgent/models/multimodal_embedding_model.py)：多模态 embedding 适配器（与向量库解耦，reme 不支持多模态，故单独提供）。

### 4.11 utils/（装备库：共享技能/工具工件库与扫描器）

- [utils/__init__.py](file:///d:/SuperAntAgent/utils/__init__.py)：utils 占位说明（建议优先复用 agentscope/标准库）。
- [utils/skill_tool_catalog.py](file:///d:/SuperAntAgent/utils/skill_tool_catalog.py)：扫描与解析技能/工具工件：
  - skills：`utils/allskill/<skill>/skill.md + skill.py`
  - tools：`utils/alltool/<tool>/tool.md + tool.json`
  - 从 `skill.md` 的“工具列表”或 `skill.py register()` 推断接口签名
- [utils/allskill/__init__.py](file:///d:/SuperAntAgent/utils/allskill/__init__.py)：占位（目录用于放技能工件）。
- [utils/allskill/doc_io/__init__.py](file:///d:/SuperAntAgent/utils/allskill/doc_io/__init__.py)：占位。
- [utils/allskill/doc_io/skill.md](file:///d:/SuperAntAgent/utils/allskill/doc_io/skill.md)：doc_io 技能说明（接口、参数、边界、加载方式）。
- [utils/allskill/doc_io/skill.py](file:///d:/SuperAntAgent/utils/allskill/doc_io/skill.py)：doc_io 技能脚本（受限仓库内读写），提供 `register(toolkit)`。
- [utils/alltool/README.md](file:///d:/SuperAntAgent/utils/alltool/README.md)：复合工具工件目录约定说明（tool.md + tool.json）。

## 5. chromaserver API（协议与字段）

### 5.1 GET /health

- 返回：`ok/base_dir/vector_store_dir/jsonl_storage_dir/reme_config_path/thread_alive/pid/build`
- 主要用途：探活、定位数据目录、排查 worker 线程是否存活

### 5.2 GET /info

- 返回：`collections=[{workspace_id, collection, count}, ...]`  
- 特性：遍历 Chroma 真实 collection 并读取 count 与 metadata.workspace_id（不是推导）<mccoremem id="03fkldd3doi58u75emhllv935" />

### 5.3 POST /init

- 入参：`{"preload_system_prompts": false}`  
- 行为：按 `configs/agent_configs.yaml` 的角色列表创建/预热 workspace，并可选把角色卡片写入到向量库（通过 `VectorStoreWorker.bootstrap_role_cards`）

### 5.4 POST /call

- 入参：
  - `method`: ensure_ready | record | retrieve | record_to_memory | retrieve_from_memory
  - `spec`: VectorStoreSpec（agent_name/base_workspace_id/default_memory_type/vector_store_dir/jsonl_storage_dir/reme_config_path）
  - `args/kwargs`: 方法参数
- 行为：路由到 `VectorStoreWorker.call()`，必要时构建并缓存 `ReMeUnifiedLongTermMemory`

### 5.5 POST /shutdown

- 行为：异步关闭 worker 并 shutdown HTTPServer

## 6. 配置与约束（研发须知）

### 6.1 模型与 API Key 读取规则

- 对话模型/嵌入模型均通过 [model_manager.py](file:///d:/SuperAntAgent/models/model_manager.py) 读取 [model_configs.yaml](file:///d:/SuperAntAgent/configs/model_configs.yaml) 构造：
  - 优先 `api_key`（直接写在 yaml，不推荐）
  - 其次读取 `api_key_env` 指向的环境变量（推荐）
- 约束：向量模型只使用 `qwen3-vl-embedding`（见 `configs/model_configs.yaml` 中 embedding/multimodal_embedding 配置）。<mccoremem id="01KH6JH9BM4VNRBXE6YN16X87S" />

### 6.2 向量库服务地址

- 主程序通过 `configs/vector_server.yaml` 读取 `url`（见 [chromaserver/client.py](file:///d:/SuperAntAgent/chromaserver/client.py#L26-L45)）
- 未配置或服务不可达时：运行时预热会跳过，并在 UI 中提示（见 [runtime.py](file:///d:/SuperAntAgent/services/runtime.py#L199-L233) 与 [data_center.py](file:///d:/SuperAntAgent/ui/data_center.py#L301-L347)）

### 6.3 严格模式与“禁止兜底”的影响点

- `ReMeUnifiedLongTermMemory.record()` 被显式禁用，要求使用 `record_to_memory()` 且必须保证向量库写入成功后才落盘（见 [reme_unified_memory.py](file:///d:/SuperAntAgent/memory/reme_unified_memory.py#L344-L346)）。
- 这会影响“旧逻辑期望 record() 自动沉淀”的调用方式：本仓库在 `ant_agent_base` 中对长期记忆写入走 `record_to_memory()`（见 [ant_agent_base.py](file:///d:/SuperAntAgent/agents/base/ant_agent_base.py#L151-L168)）。

## 7. 二次开发指南（扩展点）

### 7.1 新增角色（新 agent）

最小步骤（建议路径）：

1. 在 `configs/agent_configs.yaml` 增加 role_key 配置（name/max_iters/heartbeat）。
2. 在 `configs/prompts/system_prompts.yaml` 增加同名 role_key 的 system prompt。
3. 在 `agents/<role>/agent.py` 新建工厂 `create_xxx_agent()`，基于 `create_react_ant_agent()` 创建 ReActAgent，并（可选）挂载 `update(ctx)`。
4. 在 `services/workflow.create_colony()` 与 `services/runtime.iter_role_agents()` 中把新角色纳入 participants 与调度列表。
5. 在 UI 的 `DataCenter._load_static_agents()` 的 role_order 里加入展示顺序（如需要）。

### 7.2 新增技能（工具函数）

两种路径：

- 动态技能（推荐用于“可生成/可热加载”的工具）：
  - 通过兵蚁工具写入目标 agent 的个人装备库 `agent_home/skills/<skill_key>/skill.py | skill.md`（agent_home 由 role_key 扫描定位）
  - `skill.py` 必须提供 `register(toolkit)` 并在其中 `register_tool_function(...)`
  - 工蚁启动或调用 reload 后由 `services.skill_loader.load_utils()` 扫描加载（同时会扫描 `agent_home/tools/` 形成工具工件索引）
- 工件技能（推荐用于“可维护的稳定技能库”）：
  - 按约定写入 `utils/allskill/<skill>/skill.md + skill.py`
  - 用聊天 UI 的“工具库”页签装配到某个角色（即把共享工件安装到该角色的 `agent_home/skills` 或 `agent_home/tools`）
