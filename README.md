# 云屿 Cloud Dock

云屿是面向 Windows 的百度网盘与夸克网盘桌面客户端。每个用户登录自己的账号；百度功能使用百度授权与网盘能力，夸克功能通过独立适配层实现。登录会话仅加密保存在当前 Windows 用户的数据目录中。

当前测试版提供文件浏览、上传与下载、分享链接识别、缩略图、回收站、文件夹与单文件同步、版本备份、托盘运行、开机启动，以及经过签名验证的软件更新。

## 目录

- `app/`：网盘适配、传输、同步、备份、更新与本地数据逻辑。
- `desktop/`：Windows 原生宿主、WebView 界面和本地回环桥接。
- `installer/`：Inno Setup 安装器。
- `tests/`：离线回归和安全边界测试。
- `tools/`：构建、验收、更新清单签名和 GitHub Release 工具。

`data/`、运行时、构建输出、登录会话和更新私钥均被排除在仓库之外。不要提交个人网盘内容、Cookie、数据库、诊断日志或下载文件。

## 本地验证

```powershell
H:\CodexOutputs\projects\cloud-dock-0.6.7-dev\runtime\python\python.exe -B -m unittest discover -s H:\CodexOutputs\projects\cloud-dock-0.6.7-dev\tests -v
```

生成 Windows 安装包：

```powershell
& H:\CodexOutputs\projects\cloud-dock-0.6.7-dev\tools\build_installer.ps1
```

发行文件输出到 `H:\CodexOutputs\packages\cloud-dock\<版本>`。发布方法见 `docs/RELEASE.md`。

## 项目状态

这是测试阶段软件。夸克上传、长期同步、大目录和多设备同时修改仍需更多真实账号验收。安装包目前没有 Windows Authenticode 代码签名，Windows 可能显示未知发布者提示。
