# Seedance 2.5 Skills

本仓库保留四个 Skill 的完整源码目录；`.skill` 和 `.zip` 安装包统一在 [Releases](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest) 中下载和更新。

| Skill | 完整目录 | Skill 包 | ZIP 安装包 |
| --- | --- | --- | --- |
| Seedance2.5导演 | [seedance-overseas-director-25](seedance-overseas-director-25/) | [下载 .skill](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/seedance-director-2.5.skill) | [下载 .zip](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/seedance-director-2.5.zip) |
| 剧梦自动化流程 | [jumeng-video-operator](jumeng-video-operator/) | [下载 .skill](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/jumeng-video-operator.skill) | [下载 .zip](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/jumeng-video-operator.zip) |
| 资产自动化流程 | [overseas-live-action-asset-pipeline](overseas-live-action-asset-pipeline/) | [下载 .skill](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/overseas-live-action-asset-pipeline.skill) | [下载 .zip](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/overseas-live-action-asset-pipeline.zip) |
| 海外封面制作 | [overseas-cover-production](overseas-cover-production/) | [下载 .skill](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/overseas-cover-production.skill) | [下载 .zip](https://github.com/xingchenLY/Seedance2.5-skill/releases/latest/download/overseas-cover-production.zip) |

## 安装

- 支持导入 `.skill` 的客户端：下载对应 `.skill` 文件，使用客户端的 Skill 导入功能。
- 使用目录安装的客户端：下载对应 `.zip` 并解压，将其中的整个 Skill 文件夹放入客户端支持的 skills 目录。请保留 `SKILL.md`、`references/`、`scripts/` 等全部配套文件，不要只复制 `SKILL.md`。
- 也可从本仓库复制对应的完整源码目录，安装位置以目标客户端文档为准。

每个 `.zip` 与同名 `.skill` 的内容完全相同，只是扩展名不同；它们是 Skill 资源包，并非独立可执行安装程序。具体使用要求请查看各目录中的 `SKILL.md`。

## 安装包更新约定

`.skill` 和 `.zip` 文件仅作为 Release 附件维护，不提交到源码仓库。更新时覆盖当前 Release 中对应的同名附件，保持下载文件名和链接不变。

资产自动化流程的完整目录更新推送到 `main` 后，GitHub Actions 会自动打包、逐文件校验并覆盖 `v1.0.0` 的对应安装附件。也可在 Actions 的 “Publish asset automation skill” 工作流中手动运行。安装包不提交到仓库。

海外封面制作也通过 GitHub Actions 自动打包并覆盖同名 Release 附件，可在 “Publish overseas cover skill” 工作流中手动运行。
