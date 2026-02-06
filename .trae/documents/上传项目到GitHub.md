## 目标

* 把 d:\antagent 这个本地项目推送到你自己的 GitHub 仓库，并确保敏感文件不被提交。

## 上传前检查（安全与规范）

* 确认仓库里不要提交密钥：`.env` 已在 [.gitignore](file:///d:/antagent/.gitignore) 中忽略；建议只提交 `.env.example` 作为配置模板。

* 确认 `logs/` 等运行产物不提交：已在 `.gitignore` 忽略。

## GitHub 侧准备

* 在 GitHub 网站新建一个仓库（Repository）：

  * 建议先选 Private（私有）

  * 不要勾选“Initialize this repository with a README

