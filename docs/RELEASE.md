# 发布流程

源码仓库建议保持私有，另建公开的 `cloud-dock-releases` 仓库供所有用户读取安装包和 `channels` 更新清单。客户端不携带 GitHub 令牌。

1. 修改 `version.json`，同步发布说明并完成全部测试。
2. 运行 `tools\build_installer.ps1`，生成安装包、便携 ZIP 和 `CHECKSUMS.sha256`。
3. 运行 `tools\New-UpdateManifest.ps1 -Repository 所有者/cloud-dock-releases -Channel beta`，使用仓库外私钥生成签名清单。
4. 运行 `tools\Publish-GitHubRelease.ps1` 创建草稿 Release。检查附件、哈希、全新安装和覆盖升级后再手动发布。
5. Release 发布成功后，最后更新公开仓库 `channels/beta.json` 与 `channels/beta.sig`。客户端只有此时才会发现新版。
6. 正式版使用 `stable` 通道。已发布的版本、标签与附件保持不可变；修复问题时发布新版本。

0.6.1 本身没有更新客户端，因此用户需要手动安装一次 0.6.2。此后的安装版可以从软件内更新。便携版只检查和下载，不自动替换程序。

更新签名私钥默认保存于 `H:\CodexOutputs\secrets\cloud-dock\update-signing-private.xml`，仅当前 Windows 用户有权读取。备份私钥时使用离线加密介质，不要复制到项目目录。
