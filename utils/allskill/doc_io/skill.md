# doc_io：文档读写技能

## 适用场景
- 需要读取/更新仓库内的文档或配置说明（如 README、docs 下的 Markdown、配置文件说明）
- 需要按行读取片段并带行号返回，便于后续精确修改

## 工具列表
- read_text_doc(path, start_line=1, max_lines=200, encoding="utf-8") -> str
- write_text_doc(path, content, overwrite=True, encoding="utf-8") -> str
- append_text_doc(path, content, encoding="utf-8") -> str

## 参数说明
- path：仓库内相对路径，例如 `README.md`、`docs/xxx.md`
- start_line：起始行号（从 1 开始）
- max_lines：最多读取行数（默认 200，上限 1000）
- encoding：文件编码（默认 utf-8）
- overwrite：覆盖写入开关；当 `overwrite=False` 且文件已存在时会报错

## 返回值
- read_text_doc：返回带行号的文本片段，每行格式为 `行号→内容`
- write_text_doc / append_text_doc：返回实际写入的绝对路径与写入后文件字节数

## 安全与边界
- 仅允许访问仓库目录内文件，禁止绝对路径与路径穿越
- 仅支持扩展名：`.md/.txt/.json/.yaml/.yml/.ini`
- 单次写入最大 200000 字符

## 如何加载（由你后续人工指定给某个 agent）
在你要赋能的 agent 初始化时（或你的专属加载器里）执行：

```python
from utils.allskill.doc_io.skill import register

register(toolkit)
```

加载后，该 agent 的 toolkit 中会新增三个工具：`read_text_doc`、`write_text_doc`、`append_text_doc`。

