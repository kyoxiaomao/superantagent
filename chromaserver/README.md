# chromaserver（向量数据库服务）

本目录提供一个独立进程的“向量库服务”，用于托管 Chroma（持久化目录）+ ReMe/FlowLLM 记忆栈。

主程序（终端模式 / UI 对话）不再在本进程内创建或预热向量库，而是通过 HTTP 调用本服务完成记忆写入与检索。

## 1. 配置

主程序与管理 UI 共用同一份配置文件：

- 路径：`configs/vector_server.yaml`
- 内容示例：

```yaml
url: http://127.0.0.1:8765
```

## 2. 启动服务

在项目根目录执行：

```bash
.\.venv\Scripts\Activate.ps1
python -m chromaserver.server
```

默认监听：

- `ANT_VECTOR_SERVER_HOST`（默认 `127.0.0.1`）
- `ANT_VECTOR_SERVER_PORT`（默认 `8765`）

日志位置：

- `chromaserver/_logs/chromaserver_YYYYMMDD.log`

数据位置（默认，可用环境变量覆盖）：

- Chroma：`chromaserver/data/chroma_vector_store`
- JSONL 回退：`chromaserver/data/jsonl_storage`

## 3. 管理 UI

管理 UI 用于：

- 显示/修改服务地址（并保存到 `configs/vector_server.yaml`）
- 启动/停止服务
- 初始化向量库（可选写入 `system_prompts`）
- 清理数据库（删除向量库与 JSONL 数据目录）
- 展示 workspace 列表与条目数

启动方式：

```bash
.\.venv\Scripts\Activate.ps1
python -m ui.chroma_admin
```

## 4. 服务 API（简要）

- `GET /health`：健康检查与路径信息
- `GET /info`：workspace/collection 列表与 count
- `POST /init`：初始化数据库（创建 workspace；可选写入 system_prompts）
- `POST /call`：主程序调用入口（record/retrieve/record_to_memory/retrieve_from_memory）
- `POST /shutdown`：请求服务退出

## 5. 目录说明

- `protocol.py`：VectorStoreSpec 与 RPC 方法定义
- `service.py`：向量库服务核心（构建 ReMeUnifiedLongTermMemory、缓存实例、管理清理/信息）
- `client.py`：主程序与 UI 共用的远程客户端与配置读写
- `server.py`：HTTP 服务入口
