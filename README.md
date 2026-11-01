# Helix 工作台 · Windows 桌面应用

## 3.3.1 云端与研究进度更新（2026-11-01）

- 原有 Windows 工作台接入团队云服务器；保留本地项目与原生双螺旋启动画面。
- 首页接力台、任务与成果页、周报及上传队列整合到同一工作台。
- 研究卡片从项目数据读取最新证据、待人工复核项和主张边界；摘要更新不伪造任务完成。
- 新增小体积 metadata-preview / metadata-apply 同步；同项目、组长权限、修订校验、事务备份与失败回滚。
- 云账号凭据在本地加密保存；GitHub 仅保存源码，项目、资料、账号和密钥不进入此仓库。
- 已有外部大文件登记功能按服务端 capability 开关显示；未实现的能力不显示成功校验或可用按钮。

测试：metadata 事务与接口 22 项（21通过、1项因Windows无符号链接权限跳过）；relay/uploads 24项、前端DOM9项、项目切换4断言通过。

最新安装包见 [Releases](../../releases/latest)：下载 **Helix-Setup-3.2.0.exe**，安装后双击桌面「Helix 工作台」。独立应用窗口，不需要终端、Python 或浏览器标签页。

账号、项目和 Notion Token 只保存在使用者的安装目录；仓库与安装包不包含个人数据。项目通过工作台导入，组员自行注册并选择分工。

## 程序更新

设置 → 软件更新 → 连接有此私有仓库权限的 GitHub 账号。接受协作者邀请后才能读取；仅有历史贡献记录并不获得私有仓库访问权。接收更新只需仓库读取权限。

更新包包含原生桌面 EXE、WebView2 组件和工作台程序，使用组长 Ed25519 签名及 SHA256 校验。新原生窗口保存在独立版本目录，后台验证成功后重启窗口；失败恢复原版本。账号、项目和配置不被覆盖。Notion 继续负责进度镜像。

3.1 旧网页版先读取 `helix-update.json` 的签名 3.1.2 过渡包；新版更新器读取 `helix-desktop-update.json` 的 3.2 桌面包。普通后续版本可继续使用单份 `helix-update.json`。

## 源码与构建

`desktop/src/Bootstrap.cs` 是窗口式引导程序；`Desktop.cs` 是 WinForms + Microsoft WebView2 原生窗口。`portable_launcher.py` 在无控制台后台维护本机服务，`shell/server/updater.py` 处理签名升级与回退。

在 Windows 10/11 x64 上，取得官方 Microsoft.Web.WebView2 NuGet SDK 后运行：

```
python desktop/src/build.py --sdk <解压后的SDK目录>
```

编译器使用系统 .NET Framework 4.8。完整安装 EXE 同时包含便携 Python、科学依赖及微软官方离线 WebView2 运行库；源代码仓库不存这些大二进制文件。

软件测试通过不等于项目科研结论通过，科学门控仍按原始证据执行。
