# alltool：复合工具工件目录

本目录用于存放“工具（复合多技能流程）”的工件。工具的定义是：由多个技能（单一能力域）组合形成的流程，最终产出一个完整结果（例如产出一条视频）。

## 目录结构约定

每个工具一个独立目录：

```text
utils/alltool/<工具名>/
  tool.md
  tool.json
```

## tool.md（说明文档）

建议包含（但不强制）以下章节：

- 工具目标与适用场景
- 输入/输出
- 使用到的技能与接口（接口指技能包里注册到 Toolkit 的函数）
- 安全与边界
- 示例流程说明

## tool.json（结构化流程规范）

最小格式：

```json
{
  "name": "video_maker",
  "title": "多模态视频生成工具",
  "steps": [
    {
      "skill": "doc_io",
      "interface": "read_text_doc",
      "note": "读取脚本/字幕/配置",
      "params": {
        "path": "docs/script.md"
      }
    }
  ]
}
```

字段说明：

- name：工具标识（可选，推荐与目录名一致）
- title：工具标题（可选）
- steps：步骤列表（必填）
  - skill：技能标识（推荐与 `utils/allskill/<技能名>/` 目录名一致）
  - interface：接口名（工具函数名）
  - note：该步说明（可选）
  - params：该步参数（可选，对象）

说明：当前版本的 UI 仅用于展示 tool.md 与 tool.json 的 steps 摘要；后续若要“执行工具”，也可以复用 tool.json 的结构作为编排输入。

