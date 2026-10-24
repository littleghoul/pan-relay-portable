# Pan Relay Portable · NAS 独立部署版

基于 [ucmao/pan-relay](https://github.com/ucmao/pan-relay) 的个人 NAS 部署衍生版，增加腾讯文档同步、本地 Excel 批次导出和可自定义服务器地址的 Android 客户端。保留上游 MIT 许可证与作者署名；非上游官方发行。

## 功能

- 百度、夸克批量转存并生成自己的分享链接。
- 多文件夹逐项处理，超数量上限时尝试按子文件夹拆分。
- 腾讯普通在线表格：选择文档、Sheet、起始单元格，追加写入可点击的链接。
- 同步受限后挂起并于次日重试；导出未同步记录，迁移未同步任务。
- 本地 Excel 模式：每批一个文件，不需要腾讯文档 Token。
- 可选接入自己的 PanSou 服务。
- Android 8.0+：首次填写自己的服务器地址，支持主要地址与外网地址切换、Excel 保存。

## 快速开始

需要 Linux NAS/服务器、Docker Engine 和 Docker Compose V2。

```sh
git clone https://github.com/littleghoul/pan-relay-portable.git
cd pan-relay-portable
sh init.sh
# 打开 .env 查看本次生成的管理员密码，按需修改 HTTP_PORT。
docker compose up -d --build
```

浏览器访问 `http://你的NAS地址:5004`，使用自己的 `.env` 凭据登录，然后配置自己的网盘账号。默认无网盘账号、无资源记录、无目标腾讯文档。每个部署实例独立使用，不提供同一实例内的多租户隔离。

**[完整安装说明](安装说明.md)** · **[下载发行包和 APK](https://github.com/littleghoul/pan-relay-portable/releases/latest)**

首次构建需联网下载官方 Python 镜像与依赖。腾讯文档同步是可选功能；不配置时可以使用本地 Excel 模式。外网地址与 HTTPS 由部署者自行配置，APK 不提供公网穿透。

## 隐私与发行

- 不附带个人账号密码、Cookie、Token、数据库、历史记录、固定服务器地址或 APK 签名私钥。
- `init.sh` 在部署者本机生成独立管理员密码和会话密钥。
- 自动清理转存网盘文件默认关闭。
- 运行后的 `.env`、`secrets/`、`data/`、`sync-state/` 不应提交或再次分享。
- 迅雷适配器不附带共享应用密钥，需自行配置 `XUNLEI_CLIENT_SECRET`；百度/夸克主工作流不依赖它。

发行检查范围与限制见 [隐私检查说明](PRIVACY-REVIEW.md)。上游说明见 [README-upstream.md](README-upstream.md)，本发行版部署步骤以本仓库安装说明为准。

## 目录

- `server/`：服务端、转存工作流、腾讯文档同步服务。
- `android/source/`：Android Java 源码及本地 SDK 构建脚本。
- `compose.yaml`、`Dockerfile`、`init.sh`：容器部署与初始化。
- Releases：完整部署 ZIP、已签名 APK、校验文件。

源码构建 Android 客户端需要 JDK 17、Android SDK 36 和 Build Tools 35.0.0。自建签名不能覆盖官方发行包，详见 Android 源码目录的 README。

## 许可证

[MIT](LICENSE)，保留上游 Copyright (c) 2025 ucmao。请只转存和分享自己有权处理的内容，并遵守相关服务的使用规则。
