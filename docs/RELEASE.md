# 正式发布流程

云屿使用两个 GitHub 仓库：

| 仓库 | 用途 |
| --- | --- |
| mouse0901/cloud-dock | 公开源代码、文档、Issue 与变更记录。 |
| mouse0901/cloud-dock-releases | 公开安装包、便携 ZIP、校验文件和经过签名的更新通道清单。 |

客户端不包含 GitHub 令牌或更新签名私钥。

## 发布前

1. 确认版本号采用新的语义化版本，并在 version.json、CHANGELOG.md、安装说明、发行说明和软件内更新记录中同步。
2. 运行 Python 回归、桌面工作流检查、安装包构建和安装包生命周期检查。
3. 检查 Git 差异，确认没有 data、downloads、sessions、SQLite、日志、安装包、私钥、令牌、Cookie 或个人文件路径。
4. 生成 CloudDock-版本号-Setup-x64.exe、CloudDock-版本号-Windows-x64.zip 和 CHECKSUMS.sha256。

## 创建正式 Release

1. 先把源代码、文档和版本标签推送到 mouse0901/cloud-dock。
2. 使用更新签名私钥生成 stable 清单：

       tools\New-UpdateManifest.ps1 -Repository mouse0901/cloud-dock-releases -Channel stable

3. 在 mouse0901/cloud-dock-releases 创建 v版本号 草稿 Release，上传安装程序、便携 ZIP 和 CHECKSUMS.sha256。
4. 在干净 Windows 用户环境中验收安装、首次启动、升级、卸载和便携版启动。
5. 将 Release 发布为正式版，不标记 prerelease。
6. 最后把生成的 channels/stable.json 和 channels/stable.sig 推送到 cloud-dock-releases 的 main 分支。客户端只有在这一刻才会发现更新。

已发布标签、Release 附件、校验文件和更新清单不得就地覆盖。需要修复时发布新的版本。

## 更新签名材料

更新签名私钥只保存在仓库外的受限位置。公开源码与安装包只保存 RSA 公钥。若私钥、GitHub 令牌或代码签名证书疑似泄露：

1. 停止发布并撤销或轮换相应凭据。
2. 重新生成签名材料并更新客户端公钥。
3. 使用新的正式版本恢复更新通道。
4. 不要把敏感材料粘贴到 Issue、聊天记录或仓库。

## 发布后检查

- Release 页面能下载三个附件，附件 SHA-256 与 CHECKSUMS.sha256 一致。
- channels/stable.json 的版本、文件 URL、大小和哈希与 Release 一致。
- channels/stable.sig 能通过客户端内置公钥验证。
- 新安装的正式版默认选择稳定通道。
- 现有安装版能检查到更新、下载、校验并在空闲时安装。
- 仓库 README、更新记录和 Release 说明链接都可访问。
