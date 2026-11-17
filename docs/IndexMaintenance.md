# 公开索引维护边界

本仓库和 MOD 源码仓库各自核验来源、直接写入公开索引，不互相发送事件或调用对方工作流。入口和凭据配置见 [README](../README.md)。

## 字段所有权

固定目标是 `CH3NGYZ/Overcooked-2-HostUtilities-Stable-Releases` 与 `CH3NGYZ/Overcooked-2-HostUtilities-Beta-Releases` 的 `main/update.json`。

| 维护仓库 | 入口 | 允许修改的字段 |
| --- | --- | --- |
| MOD 源码 | AutoBuild 发布成功后调用 UpdateIndex，或手动 UpdateIndex | `mod.releases`；首次初始化时补齐客户端 envelope |
| 资源源码 | ReleaseResources 与 Sync2Gitee 均成功后调用 SyncResourceIndex，或手动 SyncResourceIndex | `resources.releases` |
| 资源源码 | 仅手动 UpdateAuxiliaryIndexes | 选择 `proxies`、`modsettingsmanager` 或两项 |

资源和辅助写入器只更新已有文件，缺文件跳过，不创建频道索引。只有 MOD 首次发布/修复负责初始化客户端必需的 schema、频道、仓库描述、平台模板和空列表；它不查询资源 Release，也不从本仓库读取代理目录。已有其他字段保持原样，MOD 不裁剪资源历史。

`proxies.json` 在本仓库 main 单独维护，不随提交自动写入索引。MOD 代码仅保留获取 raw 索引所需的八条固定启动回退；在线 API/Release 代理仍取自索引，不在 MOD 构建时拉取资源仓库。

## Gitee 上传成功门禁

自动资源发布按 `release → sync-2-gitee → update-index` 串行执行。`Sync2Gitee.yml` 通过 `workflow_call` 接收本次 Tag 和 `GITEE_TOKEN`，从该公开 GitHub Release 下载三份固定附件，再由 `nICEnnnnnnnLee/action-gitee-release@v2.0.0` 上传到 `ch3ngyz/Overcooked-2-HostUtilities-Resources`。以同步 Workflow 成功为准，不增加 Gitee 回读、大小或 SHA256 verify。

`update-index` 同时依赖 `release` 和 `sync-2-gitee` 的成功结果；任何上游失败、取消或跳过均不自动写索引。原 GitHub 发布前及索引来源校验保持不变，手动索引/dry-run 也不额外访问 Gitee。

GitHub 已公开而镜像失败时，只修复并重跑同步阶段，或手动运行 Sync2Gitee 指定原 Tag；不重新构建或移动 GitHub Tag，不自动删除已有 Gitee Release/附件。同步成功后再重跑索引或手动运行 SyncResourceIndex。独立手动 Sync2Gitee 只上传，不自动写索引；人工修复索引前应先确认相应同步 Workflow 成功。Gitee 上已存在同 Tag 的部分镜像需人工处理，不在同步中自动覆盖。

## 资源记录来源

`scripts/update_index.py` 的可选 `--resource-source-ref` 只接受完整 40 位提交 SHA；留空从远端 main 读取 `current-resource-info.json`。指定提交缺失/无声明即失败，不回退 main，不按 Tag 寻找其他提交。可选 `--tag` 必须与声明逐字相同。

- 声明提供 `version`（从 Tag 推导）、`tag`、`minModVersion`、`resourceFormat`、`changelog` 和可选 `changelogs`。
- 精确读取该 Tag 的公开、非 prerelease Release。不存在或仍为草稿即失败，不创建 Release、不覆盖 Tag/附件、不扫描其他资源版本。
- 三份固定附件为 `HostUtilities.Resources.zip`、`HostUtilities.Resources.manifest`、`release-metadata.json`。官方 API 提供大小/digest；无合法 digest 时下载计算，metadata 始终下载核验。
- metadata 的版本、Tag、最低 MOD 和格式必须与声明一致，ZIP/manifest 摘要和 ZIP 大小必须与官方资产相符。真实 `sourceCommit` 来自已发布包，不用日志修订提交 SHA 覆盖。
- `publishedAt` 来自公开 Release 的 UTC `published_at`。两个日志摘要按选定声明重新生成；修正日志不要求重发包，但旧包的兼容条件不能仅靠索引修改。

只合并 `resources.releases`：相同数字版本整体原位替换，输出使用核验的实际 Tag；新版本插首位，最多五条。保留资源节点其他属性和所有非资源字段。自动发布传本次冻结 SHA；手动同步可用 main 或指定提交，规则相同。

## 代理与 Mod Settings

`scripts/update_auxiliary_indexes.py` 是统一入口。`--target` 可选 `proxies`、`modsettingsmanager`、`both`。所有选定来源先通过校验，再预检两份目标索引；选择两项时，每份索引一次 PUT 合并两字段。只替换所选根字段，不扫描或改写 MOD/资源历史。历史根 `configurationmanager` 和其他未选字段原样保留；旧组件字段不再由本入口维护。

代理目录仅允许 `id/name/platform/purposes/template/enabled` 字段，校验唯一 ID、用途和 HTTPS 模板。仓库路径模板只允许 GitHub raw，用于 Release 的模板不能混入 raw 占位符；模板语义与客户端 `UpdateProxyTemplate` 保持一致。

Mod Settings 链接由环境变量 `RELEASE_FILE_LINK` 传入，由 `scripts/modsettingsmanager_index.py` 解析 owner/repo、原始 Tag 和 ZIP 名称，精确查询公开 Release 和分页资产列表：

- 链接须为标准 GitHub Release HTTPS ZIP 下载地址；`tag` 必须为小写 `v` 加二至四段数字，ZIP 文件名只接受英文字母、数字、下划线、点和连字符。`tag`、`url` 保留链接内容，仅移除外围空白，不用 API URL 改写输入。
- `size` 和 `sha256` 必须来自 API 的有效资产大小与 SHA256 digest，无 digest 直接失败；下载内容再次核对两者。不填写占位 URL、大小或摘要来生成可部署记录。
- `changelog` 保留完整 Release body，没有说明则为空字符串。
- ZIP 内必须恰有一个 `OC2ModSettings.dll`，程序集名为 `OC2ModSettings`。`version` 从该 DLL 的 `BepInEx.BepInPlugin` 特性读取，GUID 必须为 `com.ch3ngyz.plugin.overcooked2.modsettings`，二至四段数字版本保留原文；不从 Tag、文件名或 AssemblyVersion 推导。
- DLL 仅接受 ZIP 根、`BepInEx/plugins/OC2ModSettings/`、`BepInEx/plugins/`、`OC2ModSettings/` 四种目录布局，拼写和大小写须一致。附带文档或资源必须在同一插件目录内，不能另有 DLL；不接受任意版本外层目录。ZIP 只支持单卷普通 stored/deflate，拒绝加密、Zip64、路径越界及文件/目录或大小写冲突，以免生成客户端无法安装的记录。
- 实际 `BepInPlugin` 版本与 Tag 的数字部分补齐为四段后必须相等，例如 `v1.0.0` 与 `1.0` 相等，`v1.0.0` 与 `1.0.1` 拒绝。程序集版本不能替代插件特性校验。

`scripts/PluginVersionReader` 使用静态 PE 元数据，不加载/执行插件，也不需要 BepInEx 依赖。ZIP 最大 32 MiB、DLL 最大 16 MiB，只提取内存中的目标 DLL 到临时文件，不按 ZIP 路径解压。无有效插件特性、错误 GUID/程序集身份、重复 DLL、Tag 与插件版本不符或错误摘要等均失败。

根 `modsettingsmanager` 是单对象，没有 `releases` 数组。客户端安装了 Configuration Manager 或 Mod Settings 任一插件时保留现状；两者均缺失时读取此字段补装 Mod Settings。旧根 `configurationmanager` 不再驱动新客户端安装，保留该字段供历史客户端使用。已安装插件不会因维护索引被升级或替换；旧 Mod Settings 的动作方法兼容性由消费者探测实际公开 API，不能仅凭此索引版本号推定。

## 并发、失败与恢复

本仓库资源/辅助 jobs 共用 `public-resource-index-writer`；MOD 使用自身的串行组。GitHub concurrency 只在仓库内有效，跨仓库没有共享锁，因此双方都使用 Contents SHA 乐观并发控制，冲突后重新读取最新索引再合并自己拥有的字段。

先预检两个目标，再逐个写入；已有文件必须是 JSON 对象，损坏的 JSON 或 `null` 不是缺文件。仅 404 按缺失处理，权限、限流和服务错误失败。资源/辅助 PUT 的 409 最多尝试五次，冲突后文件被删除则跳过，不重建。内容相同不提交；写后只核验本次负责字段，允许另一个独立写入器推进其他字段。

这不是跨仓库原子事务：一边成功、另一边失败时，已完成提交仍有效。修复凭据/来源后重跑同一索引入口可补齐；资源 Release 已公开时只修索引，不重新发包或移动 Tag。`--dry-run` 仍读取并核验真实来源及拟议合并，不执行 PUT；仅检查令牌配置不等于远程权限已验证。

API 凭据仅由 `GH_TOKEN` 环境变量提供，认证 API 不跟随重定向；公共资产下载不附带写入令牌。工作流将输入作为环境变量/参数数据处理，不拼入 shell 源码。公开源码不依赖相邻私有 checkout、本地测试或私有通知服务。
