# 开发与构建

## 运行环境

持续集成使用 Windows、Python 3.14 和 requirements.txt 中锁定的桌面依赖：

- flet[desktop] 1.0.4
- httpx 0.28.1
- qrcode 8.2
- .NET Framework 4.8 编译器
- Inno Setup 6.7.3
- Microsoft WebView2 Runtime 与 SDK 文件

项目的正式安装包使用嵌入式 Python、Windows 原生宿主和 WebView2。个人运行时、WebView2 二进制、构建缓存和发行包不提交到源代码仓库。

## 安装 Python 依赖

在项目根目录执行：

    py -3.14 -m pip install --requirement requirements.txt

GitHub Actions 使用相同依赖运行全部 Python 单元测试。Windows 本机构建还需要按内部构建脚本准备 WebView2 引导安装器、嵌入式 Python 和 Inno Setup 编译器。

## 测试

执行完整 Python 回归：

    py -3.14 -B -m unittest discover -s tests -v

项目内的构建环境可以使用：

    runtime\python\python.exe -B tools\run_tests_067.py
    tools\run_productivity_qa.ps1

测试覆盖离线网盘适配边界、上传下载、同步、分享链接、更新签名、故障恢复、界面工作流和安装包生命周期。自动测试不替代真实账号、真实大目录、长期断网或多设备冲突验证。

## 构建 Windows 发行包

在配置好项目本机构建依赖后执行：

    tools\build_installer.ps1
    tools\test_package.ps1

构建输出位于 H:\CodexOutputs\packages\cloud-dock\版本号。不要覆盖已发布版本的目录；每次正式发布使用新的语义化版本。

## 代码边界

- app：平台适配、传输、同步、会话、更新和数据逻辑。
- desktop：本地 HTTP 桥接、WebView 界面、Windows 托盘和宿主进程。
- installer：安装、迁移、更新和卸载规则。
- tools：构建、发行、签名和验收辅助。

贡献代码前请先阅读 [贡献说明](../CONTRIBUTING.md)，并确认不会包含个人文件、会话、令牌、私钥、数据库或构建产物。
