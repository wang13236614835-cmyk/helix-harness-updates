# Helix Harness 3.0 通用云端部署

本目录提供通用服务源码和Docker构建配置；Windows的runtime不能直接作为Linux运行环境。

有cloud/image.tar.gz时，可先运行 `gzip -dc cloud/image.tar.gz | docker load` 加载同版镜像；否则在软件包根目录执行 `docker compose -f cloud/compose.yaml build`。镜像为 `helix-harness:3.0`。

准备cloud/state/data和cloud/state/secrets，并只给容器用户1000对应目录写入权限。首次使用 `python cloud/init.py --help` 查看初始化参数；在容器内运行init.py产生独立密钥和一次性初始化码，再配置自己的HELIX_PUBLIC_ORIGIN为有效HTTPS入口。

`docker compose -f cloud/compose.yaml up -d` 使用配置的来源启动。入口只绑定127.0.0.1:18773，由自己的HTTPS反向代理提供远程访问。组长在专用入口初始化自己的账号；组员自行注册或使用组长的加入链接。Notion和模型Token由使用者自行连接。

升级保留已有data与secrets，不重新初始化或更换密钥。模板和源材料需由组长主动导入，空白云端不固定任何研究项目。账号、口令、邀请码与个人电脑上的项目未随镜像提供。Linux镜像不声明已安装Windows科学工具。

同包的Linux镜像提供已验证运行环境。加载后，在本包根目录运行 `docker build -f cloud/Dockerfile.offline -t helix-harness:3.0 .`，将同包最终3.0源码覆盖到已加载运行环境；此步骤无需下载基础镜像或依赖。然后按上述步骤初始化或保留原有数据升级。
