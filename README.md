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

本仓库提供资源、构建脚本、Actions 工作流及版本声明。自动构建使用资源包校验脚本，发布平台为 GitHub。

音效制作说明见 [添加新语音包教程](Resources/Audio/添加新语音包教程.md)。

## GitHub 自动发布

`main` 分支的 `Resources/**` 或 `current-resource-info.json` 更新时先比较声明的 tag，只有 Tag 变化才构建发布。手动运行明确尝试当前声明，仍会拒绝重复 Tag。版本取自 `current-resource-info.json` 的 `tag`，重复 Tag 或 Release（包括草稿）会停止发布。

工作流使用 `gh` 创建非预发布的草稿并上传 ZIP、manifest 和 metadata，逐个下载核对大小与 SHA256 后，由固定提交版本的 `eregon/publish-release` 将该草稿公开。创建草稿或附件校验失败会停止公开发布。

GitHub Release 创建、上传和公开步骤使用运行时提供的 `GITHUB_TOKEN`。发布后索引通知使用配置在 Actions 中的 `UPDATE_INDEX_TOKEN` Secret 和 `UPDATE_INDEX_REPOSITORY` Variable。凭据仅通过环境变量传入，不写入源码、命令参数或产物；公开发布确认成功后才运行索引通知。

## 多语言更新日志与索引通知

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

`minModVersion` 是包含该版本的最低 MOD 版本；当前声明允许 MOD 1.8.7.02 及以上版本使用。客户端同时校验 MOD 自身的最低资源版本要求和资源格式。

构建和发布保留三种语言的完整日志。适配后的 MOD 使用当前 MOD 语言选择日志，缺译回退英文或旧 changelog，语言不影响下载平台。

发布前配置 Actions Secret UPDATE_INDEX_TOKEN（能向私有源码仓库发送 repository_dispatch）和 Variable UPDATE_INDEX_REPOSITORY（私有源码仓库 owner/repo）。本仓库 Release 使用自动提供的 GITHUB_TOKEN；私有写入器自行核验已发布的 metadata 和附件，再检查 Stable/Beta 的 update.json。文件存在时只合并 resources.releases，保留 MOD、代理及其他字段；文件不存在时跳过该频道，由该频道的 MOD 首次发布负责创建索引。API 权限或服务错误会停止合并。

新资源协议需要 MOD 1.8.7.02；先发布该 MOD，再发布资源。旧 v1.8.7.01 DLL 不支持新的资源 Tag 和省略 versionCode 的记录；资源加入索引后，仍在使用该旧版的用户需要手动升级。

Release 已公开后通知失败，保留现有 Tag 和资产，只补通知或在私有仓库运行 Update public indexes，不重跑发布已有 Tag。手动补通知只从环境变量读取上述配置：

```powershell
python scripts/notify_update_index.py --tag v1.0.0
```

本地构建和校验不要求发布或通知凭据；Actions 在创建 Tag 前检查通知配置。

## 手动同步当前资源信息

在 Actions 中运行 `Sync current resource indexes`（`SyncResourceIndex.yml`）。工作流读取 main 的 `current-resource-info.json`，向私有统一写入器发送 `resources-sync` 通知。私有任务核验该 Tag 已公开的 metadata 与附件，并确认声明与实际包一致后同步两个仓库已有的 `update.json`。

同 Tag 记录原位替换；没有同 Tag 时插到 `resources.releases` 最前面；列表最多保留 5 条，MOD、代理和其他字段保留。目标文件不存在则跳过。自动资源合并和 MOD 索引刷新也遵守资源最多 5 条；不会删除 GitHub Releases、Tag 或附件。

手动同步使用与发布通知相同的 Secret/Variable；工作流显示通知已受理后，可在私有仓库 `Update public indexes` 查看写入结果。重复运行可以修复索引，不重建资源包。修改资源、最低 MOD 或日志后，应换新 Tag 发布新包，不能把未发布的声明写入旧包索引。

## 手动同步 ConfigurationManager

在 Actions 中运行 `Sync ConfigurationManager indexes`（`SyncConfigurationManager.yml`），将 GitHub Release 的 BepInEx5 ZIP 文件链接填入 `release_file_link`。这里只需输入文件链接，不需要手写 JSON 或修改资源版本声明。

工作流沿用 `UPDATE_INDEX_TOKEN` 和 `UPDATE_INDEX_REPOSITORY`，发送 `configurationmanager-sync` 通知。通知已受理后，在私有仓库 `Write ConfigurationManager indexes` 查看最终结果；两个仓库的工作流都需要先部署到 main。私有任务从链接解析 owner/repo 和 Release Tag，通过官方 API 读取资产大小、SHA256 digest 和完整 Release 更新说明；下载 ZIP 核验后，静态读取 `ConfigurationManager.dll` 的 `BepInPlugin` 版本。API 未提供 SHA256、附件校验失败或 DLL 插件声明缺失时停止同步。

两个已有 `update.json` 的根 `configurationmanager` 会整体替换为单个对象，包括 `tag`、`version`、`url`、`size`、`sha256` 和 `changelog`，没有 `releases` 列表。`url` 保留输入的链接（去掉外围空白），`version` 保留 DLL 插件声明版本，例如官方 v19.0 包的插件版本为 `19.0`。MOD、资源、代理和其他字段保留；缺少 `update.json` 的频道跳过，权限或服务错误会停止。重复运行相同链接不产生重复条目。
