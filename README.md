## 独立资源包发布

本仓库负责资源 ZIP、文件清单和独立版本；适配后的 MOD 先检查自身更新，再检查资源。

根 `current-resource-info.json` 维护当前资源的 Tag、兼容条件，以及自上一次资源 Release 以来、本次待发布版本的完整更新日志。资源版本只从 `vX.Y.Z` Tag 推导，不再维护 `versionCode`，发布历史保存在各版本 Release 和元数据中。

GitHub Actions 比较提交前后的 current-resource-info.json Tag；仅 Tag 变化自动构建发布，Tag 不变时资源或日志提交不会发布。从旧文件名迁移时会读取上一提交的声明，仅改名仍跳过发布。

已发布版本的资源不可修改，已有附件只核对、不覆盖。

## 资源目录与打包

所有需要发布的资源统一放在 `Resources/` 中，目前包含 `Audio/` 和 `TextChatNotifySound/`。构建脚本递归打包其中的全部文件和目录，新增资源无需修改打包目录列表。

ZIP 直接以 `Resources/` 内的内容为顶层，不添加外层目录：

```text
Audio/
TextChatNotifySound/
HostUtilities.Resources.info.json
HostUtilities.Resources.manifest
```

本地构建并校验（Windows PowerShell 5.1 或更新版本、Python 3.11 或更新版本）：

```powershell
./scripts/Build-ResourcePackage.ps1
python scripts/validate_resource_package.py
```

产物输出到 `artifacts/resources/`，该输出目录需要为空。重复构建时通过 `-OutputDirectory` 指定新的空目录，并在校验命令中用 `--directory` 指向同一个目录。发布由 GitHub Actions 完成。

本仓库提供资源、构建脚本、Actions 工作流及版本声明。自动构建使用资源包校验脚本，先发布到 GitHub，再由 Actions 上传到 Gitee。

音效制作说明见 [添加新语音包教程](Resources/Audio/添加新语音包教程.md)。

## GitHub 自动发布

`main` 分支的 `Resources/**` 或 `current-resource-info.json` 更新时先比较声明的 tag，只有 Tag 变化才构建发布。手动运行明确尝试当前声明，仍会拒绝重复 Tag。版本取自 `current-resource-info.json` 的 `tag`，重复 Tag 或 Release（包括草稿）会停止发布。

工作流使用 `gh` 创建非预发布的草稿并上传 ZIP、manifest 和 metadata，逐个下载核对大小与 SHA256 后，由固定提交版本的 `eregon/publish-release` 将该草稿公开。创建草稿或附件校验失败会停止公开发布。

GitHub Release 创建、上传和公开步骤使用运行时提供的 `GITHUB_TOKEN`。确认公开成功后，调用本仓库的 `Sync2Gitee.yml`，将本次 Tag 的三份附件上传到 `ch3ngyz/Overcooked-2-HostUtilities-Resources`。同步使用 `nICEnnnnnnnLee/action-gitee-release@v2.0.0`，以该 Workflow 成功为准，不额外下载或校验 Gitee 镜像。

自动发布顺序为 `release → sync-2-gitee → update-index`。只有 GitHub 发布和 Gitee 同步均成功，索引 job 才调用本仓库的 `SyncResourceIndex.yml`，传入本次冻结的源码 SHA 和 Tag；同步失败、取消或跳过时不运行自动索引，不调用 MOD 源码仓库。

## 多语言更新日志与索引同步

current-resource-info.json 示例（不手写 version，版本从 tag 推导）：

```json
{
  "tag": "v1.0.0",
  "minModVersion": "1.8.7.02",
  "resourceFormat": 1,
  "changelog": "兼容旧 MOD 的中文日志。",
  "changelogs": {
    "zh-cn": "完整中文日志。",
    "en-us": "Complete English release notes.",
    "ko-kr": "전체 한국어 업데이트 내역입니다."
  }
}
```

`minModVersion` 是包含该版本的最低 MOD 版本，实际值以当前声明为准。客户端同时校验 MOD 自身的最低资源版本要求和资源格式。

构建和发布保留三种语言的完整日志。适配后的 MOD 使用当前 MOD 语言选择日志，缺译回退英文或旧 changelog，语言不影响下载平台。

发布前配置 Actions Secret `GITEE_TOKEN`，允许在上述 Gitee 资源仓库创建 Release、上传附件；另配置 `UPDATE_INDEX_TOKEN`，授予两个公开 Release 仓库的 Contents 读写权限，并允许读取本资源仓库的声明和公开 Release。运行时 `GITHUB_TOKEN` 负责本仓库 Release 和同步时读取 GitHub 附件，不能替代跨仓库写入凭据。不再需要 `UPDATE_INDEX_REPOSITORY`、私有仓库权限或 repository_dispatch。令牌仅通过环境变量传入，不写入源码、命令参数或产物；创建 Tag 前会检查两个 Secret 已配置，但这不代表已验证其远程权限。

同步目标固定为 `CH3NGYZ/Overcooked-2-HostUtilities-Beta-Releases` 和 `CH3NGYZ/Overcooked-2-HostUtilities-Stable-Releases` 的 main 根 `update.json`。哪个仓库存在该文件就更新哪个；两边都有就都更新，两边都没有就都跳过，不创建索引。只合并 `resources.releases`，同数字版本整体替换，保留 MOD、代理及其他字段。API 权限或服务错误会停止合并，不能当作缺文件。

新资源协议需要 MOD 1.8.7.02；先发布该 MOD，再发布资源。旧 v1.8.7.01 DLL 不支持新的资源 Tag 和省略 versionCode 的记录；资源加入索引后，仍在使用该旧版的用户需要手动升级。

GitHub Release 已公开后，Gitee 同步失败时保留现有 GitHub Tag 和资产，仅修复并重跑同步 job，或手动运行 `Sync2Gitee.yml` 并填写原 Tag。同步不会自动删除已有 Gitee Release 或覆盖附件；若已留下同 Tag 的部分镜像，需先人工处理其状态。Gitee 同步成功后若只有索引失败，只重跑索引 job 或手动运行 `SyncResourceIndex.yml`；不要重跑已有 Tag 的发布。本地构建和包校验不要求同步或索引凭据。

## 手动同步当前资源信息

在本仓库 Actions 中运行 `Sync current resource indexes`（`SyncResourceIndex.yml`）。可选 `resource_source_ref` 填资源仓库的完整 40 位提交 SHA；留空时读取远端 main 的 `current-resource-info.json`，填写时只读取该提交的 JSON，不读取 MOD 仓库，也不按 Tag 自动回溯源码。可选 `tag` 仅用于核对，必须与所选声明相同；留空则从声明取 Tag。首次修复可勾选 `dry_run`，先校验而不写入。

本仓库写入器只读取该 Tag 的公开 Release 和 `HostUtilities.Resources.zip`、`HostUtilities.Resources.manifest`、`release-metadata.json`。Release 不存在、仍是草稿或附件校验失败时，在写入任何索引之前报错，不构建或重发资源包，直接使用已发布附件。

版本、Tag、兼容条件和三语日志来自所选声明；附件大小、SHA256、发布时间和真实包的 `sourceCommit` 来自 Release/metadata。`minModVersion`、`resourceFormat` 必须与包内 metadata 一致，否则客户端会拒绝安装，因此不允许用索引修改旧包的兼容条件。修正日志不要求重建包；同步时重新计算 `changelogSha256` 和 `changelogsSha256`。

同数字版本记录整体原位替换，使用本次核验的实际 Tag；新版本插到 `resources.releases` 最前面，最多保留 5 条。MOD、代理、ConfigurationManager 和其他字段保留。只有资源写入器裁剪资源历史，MOD 索引任务不修改它；不会删除 GitHub Releases、Tag 或附件。

自动和手动索引均使用本仓库 `UPDATE_INDEX_TOKEN`；在本次资源仓库运行中查看最终结果，不需等待另一个仓库任务。重复运行可以修复索引，内容不变不再提交。两个目标先预检再分别写入，不是跨仓库原子事务；若第二个失败，第一个已核验提交仍有效，修复后可重跑补齐。

也可在本地通过 `GH_TOKEN` 环境变量传入同等权限凭据进行只读预检：

```powershell
# Use the resource repository main declaration.
python scripts/update_index.py --dry-run
# Or select a historical resource declaration by its full commit SHA.
python scripts/update_index.py --resource-source-ref <资源源码完整提交SHA> --tag v1.0.1 --dry-run
```

## 手动同步代理与 ConfigurationManager

在本仓库 Actions 中运行 `Update proxies and ConfigurationManager`（`UpdateAuxiliaryIndexes.yml`）。该流程只能手动触发，不随代理提交、资源发布或 MOD 发布自动运行：

- `target=proxies`：从本仓库 main 的 `proxies.json` 读取并校验在线代理目录，只替换根 `proxies`，不需要文件链接或 .NET 读取器。
- `target=configurationmanager`：将 GitHub Release 的 BepInEx5 ZIP 链接填入 `release_file_link`，只替换根 `configurationmanager` 单对象。
- `target=both`：先核验两种来源，再对每份索引一次合并两字段；任何来源校验失败都不写入。
- `dry_run=true`：校验来源及拟议索引，不写入远端。正式写入使用同一 `UPDATE_INDEX_TOKEN`，两个目标缺文件都跳过，其他字段保持不变。

ConfigurationManager 的 Tag 来自链接，大小、SHA256 digest 和更新说明来自官方 Release API；下载 ZIP 核验后，静态读取唯一 `ConfigurationManager.dll` 的 `BepInPlugin` 版本，不执行插件。API 无合法 SHA256、附件不符或插件声明缺失时失败。输出包含 `tag`、`version`、`url`、`size`、`sha256` 和 `changelog`，没有 `releases`；URL 保留去掉外围空白的输入，插件版本保留原文（例如 `19.0`）。详细字段、安全边界和恢复规则见 [索引维护](docs/IndexMaintenance.md)。
