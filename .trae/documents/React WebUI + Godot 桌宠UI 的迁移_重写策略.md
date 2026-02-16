## 你问的结论
- 不是“React/Godot 先写一大堆再想怎么接 runtime”，而是“先定协议/边界，再让 UI 先行”。
- agentruntime 放在更后面做没问题；但“ant-core（runtime 网关）”应尽早做出最小可用版本，否则 UI 很容易写成不可接入的形态。

## 为什么 UI 不能完全脱离 runtime 先做
- 你的 UI 本质上是“事件流消费者 + 命令发送者”。如果不先固定事件/命令协议，UI 很容易把状态、去重、流式聚合等逻辑写死在前端，后面接入 runtime 时会出现：
  - 事件字段对不上、流式粒度不一致
  - 同一条消息被重复渲染/丢消息
  - Godot/React 各写一套解析与状态机，维护成本翻倍

## 让“自己写的东西”也能顺利接入的原则（关键）
- UI 保持“薄”：只做渲染与交互，不做业务编排。
- 业务编排只放在 1 个地方：ant-core（运行时网关）或 core 层。
- 共享契约放在仓库的 contracts/（JSON Schema + 示例数据 + 版本号）。
- 为两端各提供一个很薄的 Client SDK：
  - React：一个事件缓冲器/游标消费器 + 类型定义
  - Godot：一个 Autoload 单例（HTTP 拉取 events/snapshot + 信号分发）
  这样你“自己写的东西”主要是 UI 层组件/动画，不会和 runtime 逻辑缠在一起。

## 推荐落地顺序（既能先做 UI，又不怕接不进来）
### 0）先把协议钉死（1 次性工作）
- 从现有 DataCenter/DataEvent 与 runtime.ui_event 归纳出稳定协议：
  - commands：submit_user_text、refresh_vector_db（可选）…
  - events：user_message、user_reply_stream、user_reply、group_message、agent_status、heartbeat、toolkit_snapshot、memory_warmup、error…
- 输出：contracts/v1/{commands.json, events.json, examples/*.json}

### 1）UI 先行：用“协议驱动”的 Mock Server
- 写一个极简 mock（甚至静态 JSON + 轮询也行），严格按 contracts/v1 输出。
- React/Godot 先把：布局、列表、输入、动画状态机、断线提示 做出来。
- 注意：UI 只依赖 contracts，不依赖 runtime 内部细节。

### 2）尽早做 ant-core 最小网关（真正可接入 runtime 的那一步）
- 不引入新依赖，先用标准库 HTTP 方式提供：/health、/info、/submit、/events、/snapshot。
- 网关内部复用现有 ColonyRuntime + DataCenter，把它们变成“服务端事件流”。
- 关键是做到：UI 不改/少改即可从 mock 切换到真实 ant-core（只换 baseURL）。

### 3）React/Godot 接真实 ant-core，消除差异
- 把 mock 与真实差异收敛回 ant-core（而不是让 UI 端打补丁）。
- 这一步完成后，你再继续做 UI 体验（富文本、头像、更多面板）才不容易返工。

### 4）最后接入 agentruntime 做编排
- agentruntime 负责起停：chromaserver + ant-core（以及未来其他服务）。
- 保持“脱离 agentruntime 也能单独运行”的能力，降低耦合与调试成本。

## 验收标准（保证不会白做）
- React/Godot 在 mock 阶段就能完整跑通“发送→事件流→渲染”。
- 切到真实 ant-core 时：UI 侧只改 baseURL（或极少量配置），不重写状态机。
- ant-core 对外协议版本化（v1/v2），未来扩展不会把 UI 全打碎。