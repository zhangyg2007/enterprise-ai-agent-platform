# 第 1 课：在 Ubuntu 建立项目与数据库环境

本课对应 M01。目标是在你的 **Ubuntu 24.04 amd64 服务器**上，建立项目目录、独立 Python 环境，以及只接受服务器本机连接的 PostgreSQL + pgvector 数据库。完成后，你应能解释每一层负责什么，并通过实际检查确认应用可以连接数据库。

**执行位置：本文所有命令都在 Ubuntu 服务器终端中执行。** Windows 工作区只用于编写和检查代码、文档；不在 Windows 安装本课运行环境。使用具备 `sudo` 权限的普通账户登录服务器。

**发布前提：本批代码目前尚未完成发布。必须等本批内容已发布到指定 GitHub 仓库的 `main` 分支，再执行第 3 节及后续步骤。** 本文中的命令和预期结果是操作说明，不是你的服务器运行记录。仓库有首页，不代表本批文件已经发布；以发布说明及下文文件检查为准。

你提供的服务器有 128GB 内存、RTX 4090D 24GB、CUDA 13，以及现有 Ollama 和 `qwen3.8:27b` 模型。本课数据库和数据生成任务使用 CPU；现有 Ollama、模型、驱动和 CUDA 保持原状。M01/M02 不安装 PyTorch、NVIDIA Container Toolkit 或训练框架；训练课会另外建立 Python 3.11 环境并验证 GPU 兼容性。模型名称以[第 0 课](00-server-inventory.md)的真实清单为准。

## 1. 先理解本课的三个环境

| 层次 | 本课使用方式 | 保存什么 |
|---|---|---|
| Ubuntu 主机 | Git、系统 Python、Docker Engine | 项目源文件与系统服务 |
| 项目 Python 虚拟环境 `.venv` | Python 3.12，安装数据库访问依赖 | Python 包；不保存数据库数据 |
| Docker Compose 的 `db` 服务 | `pgvector/pgvector:0.8.2-pg16` | PostgreSQL 16 与 pgvector；数据放在命名卷 |

Python 程序从服务器的 `127.0.0.1:5432` 连接数据库，Docker 将该端口转发到容器内的 `5432`。`127.0.0.1` 指当前机器：在 Windows 浏览器或终端使用这个地址，访问的是 Windows 自己。后续需要远程访问时再设计入口，本课无需开放数据库公网端口。

容器可停止、重建；数据库命名卷负责保留数据。Git 管理代码，`.env` 保存本机密码，`.venv` 保存 Python 依赖，这些职责不要混淆。

## 2. 检查服务器并准备基础工具

### 2.1 确认系统与可用空间

```bash
cat /etc/os-release
dpkg --print-architecture
python3 --version
df -h .
ss -ltn
```

预期系统为 Ubuntu 24.04、架构为 `amd64`、系统 Python 为 3.12。若与这些条件不同，先记录差异，调整安装方案后继续；不要用本文命令替换系统 Python。检查当前磁盘是否有足够空间容纳仓库、容器镜像和实验数据；第 2 课先用 `tiny` 数据集，不直接生成大批数据。

从端口列表确认 `5432` 是否已被占用。如果已有 PostgreSQL 或其他服务，不停止它；第 5 节可以把本项目端口改为未占用的 `55432`。

安装基本工具：

```bash
sudo apt update
sudo apt install git ca-certificates curl python3 python3-venv
```

`python3-venv` 用于建立项目隔离环境。这里不使用 `sudo pip`，不修改 `/usr/bin/python3`，也不安装另一个 CUDA 环境。

### 2.2 判断 Docker 应该复用还是新装

先检查已安装的软件包：

```bash
dpkg-query -W -f='${binary:Package}\t${db:Status-Abbrev}\t${Version}\n' \
  docker-ce docker-ce-cli containerd.io docker-compose-plugin \
  docker.io docker-compose docker-compose-v2 docker-doc docker-buildx \
  podman-docker containerd runc
```

部分软件包不存在时，命令会报告未找到，并可能以非零状态结束；这是盘点结果。状态以 `ii` 开头表示已安装。另检查已有 apt 源，防止重复添加：

```bash
grep -R -n 'download.docker.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null
```

没有匹配项时，`grep` 无输出且返回非零状态。如果 Docker 命令存在，再执行：

```bash
if command -v docker >/dev/null 2>&1; then
  sudo docker version
  sudo docker compose version
  sudo docker ps -a
  sudo docker volume ls
else
  printf '未发现 Docker 命令。\n'
fi
```

根据盘点结果选择路径：

| 检查结果 | 下一步 |
|---|---|
| 已有可用 Engine 和 Compose 插件 | 复用现有安装，跳到第 3 节 |
| 已有 Docker，但服务未运行、插件缺失或版本不支持本课命令 | 先确认安装来源和已有业务；按原来源修复，不直接覆盖 |
| 已有 `docker.io`、`podman-docker`、`containerd`、`runc` 等可能冲突的软件包 | 暂停新装路径，确认这些组件被哪些服务使用，再制定迁移方案 |
| 没有 Docker、冲突软件包、既有 Docker apt 源或相关工作负载 | 继续下一节的新装步骤 |

官方安装说明列出了冲突软件包及其移除要求。本文先做盘点，不提供自动批量卸载命令，因为现有容器及其他容器平台可能依赖它们。[Docker Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/)

### 2.3 仅在满足新装条件时安装 Docker

以下使用 Docker 官方 apt 源。若上节发现已有安装或源配置，跳过此节并先处理盘点发现的问题。

添加密钥：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

添加源，整段复制执行；最后一行 `EOF` 必须独占一行：

```bash
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
```

安装并验证：

```bash
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl status docker --no-pager
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

预期 Docker 服务为 `active (running)`；`docker version` 能显示客户端和服务端；Compose 有版本信息；`hello-world` 输出测试容器的成功说明并退出。这里只是 Docker 自检，不代表项目数据库已通过验收。若新装后服务未运行，先检查状态输出；确认没有其他配置问题后，可用 `sudo systemctl start docker` 启动。

本教程的 Docker 命令始终使用 `sudo`，不把普通账户加入 `docker` 组。网络下载失败时先检查服务器网络、DNS 或批准使用的代理，不换用不明安装脚本。

## 3. 等待发布后，取得指定版本的项目

**只有确认本批内容已发布到 `main`，才能继续本节。** 指定仓库为 [enterprise-ai-agent-platform](https://github.com/zhangyg2007/enterprise-ai-agent-platform)。不要自行创建同名空目录来替代缺失的项目代码。

首次取得项目：

```bash
mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone --branch main --single-branch https://github.com/zhangyg2007/enterprise-ai-agent-platform.git
cd enterprise-ai-agent-platform
git status --short --branch
git log -1 --format='%h %s'
ls compose.yaml pyproject.toml scripts/create_env.py
```

记下提交号，后续反馈时可以准确对应版本。预期三个文件都存在，工作区没有意外修改；仓库结构也应有 `src/enterprise_ai/` 和 `infra/postgres/init/`。若 `main` 尚无这些文件，停止后续步骤，等待发布。

如果同名目录已经存在，不再次克隆、不覆盖。先进入原目录，核对 `git remote -v`、`git status --short --branch` 和当前提交；需要更新时按第 8 节处理。私有仓库的访问失败应检查账号权限，不把访问令牌写进克隆 URL、脚本或反馈文本。

后续命令默认在仓库根目录执行。每次重新登录服务器，可以先执行：

```bash
cd "$HOME/projects/enterprise-ai-agent-platform"
```

## 4. 建立 Python 虚拟环境

```bash
python3 -m venv .venv
. .venv/bin/activate
python --version
python -m pip install -e '.[db]'
python -m pip check
python -c "import sys, psycopg; print(sys.executable); print('psycopg', psycopg.__version__)"
```

逐行理解：

1. `venv` 在当前项目创建 `.venv`；依赖装入这个目录。
2. `. .venv/bin/activate` 让当前终端的 `python` 指向项目环境；新终端需要再次激活。
3. `-e` 使用可编辑安装，读取项目源码；`[db]` 安装数据库可选依赖。
4. `pip check` 检查已安装 Python 包之间的依赖要求；导入命令确认当前解释器和数据库驱动。

预期解释器路径位于本项目 `.venv/bin/python`，Python 为 3.12，`pip check` 没有依赖冲突。项目允许 Python `>=3.11,<3.14`，本课选择 Ubuntu 默认的 3.12；训练环境以后单独创建。虚拟环境的隔离和激活机制见 [Python 3.12 venv 文档](https://docs.python.org/3.12/library/venv.html)。

本批 `pyproject.toml` 将数据库依赖固定为 `psycopg[binary]==3.2.10`，该版本可在 [PyPI 发布页](https://pypi.org/project/psycopg/3.2.10/)核对。通过项目声明安装，不额外手工升级到另一版本；本课没有 PyTorch 依赖。

如果出现 `externally-managed-environment`，先检查解释器路径和激活状态，回到虚拟环境再安装，不使用绕过系统保护的参数。如果提示缺少 `ensurepip`，确认已安装 `python3-venv`；如果下载依赖失败，保留错误摘要并检查网络。

## 5. 创建本机配置与随机密码

首次启动数据库前，在仓库根目录执行：

```bash
python3 scripts/create_env.py
stat -c '%a %n' .env
git check-ignore .env
```

脚本创建 `.env`，分别生成管理员和两个读者角色的随机密码，并设置仅文件所有者可读写的权限。预期权限为 `600`，Git 检查显示 `.env` 被忽略。脚本只打印创建状态，不打印密码。不要把示例文件直接复制成真实密码配置，也不要在聊天里粘贴 `.env`。

配置字段如下：

| 字段 | 用途 |
|---|---|
| `POSTGRES_DB` | 实验数据库名，默认 `enterprise_ai` |
| `POSTGRES_USER` | 初始化管理员，默认 `lab_admin` |
| `POSTGRES_PASSWORD` | 管理员随机密码 |
| `POSTGRES_PORT` | 主机端口，默认 `5432` |
| `LAB_A_PASSWORD` | `lab_a_reader` 的随机密码 |
| `LAB_B_PASSWORD` | `lab_b_reader` 的随机密码 |

生成器再次运行时会拒绝覆盖已有 `.env`，以非零状态退出，并说明文件未变。这是保护机制，不是应该删除文件重试的错误。已有文件时核对来源与权限后继续；不要为了“修复”连接问题重新生成密码。

如 `5432` 被占用，只编辑 `.env` 中的端口，例如将 `POSTGRES_PORT=5432` 改为 `POSTGRES_PORT=55432`；保留其他字段。使用服务器上的文本编辑器操作，避免把完整文件内容复制到反馈中。本文后续命令自动读取配置，容器内端口仍为 `5432`。

在日常使用中，项目按 `.env` 读取连接信息。不要在终端另行导出同名 `POSTGRES_*`、`LAB_*` 变量；环境变量可能覆盖文件配置，且 `sudo` 的环境保留规则可能使 Python 与 Compose 使用不同值。若此前做过其他实验，使用干净的新终端进入项目并激活 `.venv`。

## 6. 启动 PostgreSQL 与 pgvector

### 6.1 校验配置，再启动

```bash
sudo docker compose config --quiet
sudo docker compose up -d --wait
sudo docker compose ps
sudo docker compose logs --tail=80 db
```

每条成功后再执行下一条。`config --quiet` 只校验，不展开打印含密码的配置；正常时无输出。不要把不带 `--quiet` 的配置输出或容器环境检查作为反馈材料。[Compose 配置校验](https://docs.docker.com/reference/cli/docker/compose/config/)

`up -d --wait` 拉取所需镜像、创建或复用资源，并等待健康状态。首次拉取速度取决于网络；命令失败后查看状态与日志，不重复更改密码。`ps` 应显示 `db` 正常运行且健康，端口映射应类似 `127.0.0.1:5432->5432/tcp`；选择了其他端口时对应变化。[Compose 启动与等待行为](https://docs.docker.com/reference/cli/docker/compose/up/)

日志用于在服务器上检查初始化和启动错误。**不要公开原始日志**；反馈只摘录与问题有关的短片段，并去除凭证、个人路径、业务数据等敏感内容。

Compose 已将主机绑定地址固定为 `127.0.0.1`，不要改成 `0.0.0.0`。这一步也不需要更改防火墙或开放 `5432` 公网访问。

### 6.2 了解首次初始化

空数据卷首次启动时，PostgreSQL 镜像会执行 `infra/postgres/init/` 中的初始化内容，创建实验所需的角色、扩展和表结构。已有数据目录时不会重新运行这些初始化脚本；修改 `.env` 也不会自动修改数据库内部已有的角色密码。这是 PostgreSQL 容器初始化的基本行为。[PostgreSQL 镜像初始化说明](https://hub.docker.com/_/postgres)

因此，本课应当先生成 `.env`，再首次启动数据库。后续更新 SQL 文件不等于完成数据库迁移；如果发布说明涉及结构升级，应按对应迁移说明执行，不能靠重启期待自动补齐。

若首次初始化途中失败，不使用删除卷作为通用修复。保留容器、数据卷和错误摘要，先判断哪些步骤已经成功；初始化脚本不一定会在下次启动时从头补跑。涉及重建的处理要先确认数据价值和备份。

### 6.3 从项目 Python 环境检查连接

```bash
python -m enterprise_ai.db health
```

预期命令成功返回、退出码为 0，并报告数据库连接健康；实际字段以本批发布的 CLI 输出为准。紧接着可用 `echo $?` 查看上一条命令的退出码。

容器健康检查和这一条命令的意义不同：容器健康检查确认数据库服务可响应；Python 检查还需要当前虚拟环境、依赖、主机端口和连接凭证都正确。两项都满足才能继续。

本课不写入业务样例数据。第 2 课会生成 `tiny` 数据集、检查数据质量、导入数据库，并验证 `lab_a_reader`、`lab_b_reader` 的权限边界。[继续第 2 课：企业样例数据](02-enterprise-data.md)

## 7. 常见问题与保留数据的恢复顺序

先记录失败的具体步骤和错误摘要，再只处理对应层，不一次更改多个条件。

| 现象 | 检查与恢复 |
|---|---|
| `permission denied` 访问 Docker socket | 确认命令带 `sudo`，不要修改 socket 权限或加入 Docker 组 |
| 找不到 `compose` 或不支持 `--wait` | 核对 `sudo docker compose version` 与安装来源；按原来源升级插件前先检查已有业务 |
| Docker 服务连接失败 | 查看 `sudo systemctl status docker --no-pager`；已有服务先查原因，避免直接重装 |
| 镜像拉取超时、域名解析失败 | 检查网络和获准使用的代理；不要更换未知镜像，也不要重装驱动 |
| `port is already allocated` | 用 `ss -ltn` 确认占用；选择空闲端口修改 `.env`，再校验并执行 `up -d --wait` |
| `config --quiet` 报缺少变量 | 确认位于仓库根目录，且已成功生成 `.env`；只检查字段名，不公开密码 |
| `.env already exists` | 生成器保护已有配置；继续使用原文件，不删除重建 |
| 数据库健康，但 Python 报认证失败 | 核对是否用了旧卷、新密码或终端覆盖变量；恢复与该卷匹配的原配置，再检查连接 |
| Python 报 `No module named enterprise_ai` 或 `psycopg` | 激活本项目 `.venv`，核对解释器路径，重新执行项目依赖安装 |
| 数据库运行，但缺少表或角色 | 检查首次初始化是否中断，或是否复用了其他版本的数据卷；按发布说明处理迁移 |
| `up --wait` 超时 | 查看 `ps` 和 `logs --tail=80 db`，区分初始化慢、磁盘不足和明确错误；不要靠删卷重试 |

普通停止与重新启动使用：

```bash
sudo docker compose stop db
sudo docker compose start db
sudo docker compose ps
python -m enterprise_ai.db health
```

`start` 启动已有容器，不应用新的 Compose 配置。它也不负责等待应用健康；如果立即执行 Python 检查时数据库尚在启动，查看状态，待就绪后重试。配置已改变时应在检查、备份后使用 `up -d --wait`。

不要将 `docker compose down -v` 当作重启方式；它会删除本项目的命名卷及其中数据。本课不需要删除数据库卷、清空 Docker 存储或停止 Ollama。

## 8. 日常使用、备份与代码更新

### 8.1 每次重新登录

```bash
cd "$HOME/projects/enterprise-ai-agent-platform"
. .venv/bin/activate
git status --short --branch
sudo docker compose ps
python -m enterprise_ai.db health
```

如果只是上次主动停止了数据库，按第 7 节 `start` 并等待健康。不要每次重新创建 `.env` 或重新安装全部环境。

### 8.2 更新前做可检查的备份

以下命令在数据库健康时执行，将备份放在仓库之外的用户私有目录。备份包括数据库、角色定义和本机配置；角色备份可能包含密码散列，按凭证文件保护。先停止本实验的写入任务，再执行：

```bash
umask 077
LAB_BACKUP_DIR="$HOME/enterprise-ai-backups/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$LAB_BACKUP_DIR"
sudo docker compose exec -T db pg_dump -U lab_admin -d enterprise_ai -Fc > "$LAB_BACKUP_DIR/enterprise_ai.dump"
```

必须确认 `pg_dump` 成功后再继续；失败可能留下不完整文件，不能把“文件存在”当作备份完成。使用默认数据库和管理员名时，上述命令可直接执行；如果你主动改过名称，需要对应调整。

```bash
sudo docker compose exec -T db pg_dumpall -U lab_admin --globals-only > "$LAB_BACKUP_DIR/globals.sql"
cp -p .env "$LAB_BACKUP_DIR/config.env"
git rev-parse HEAD > "$LAB_BACKUP_DIR/code-commit.txt"
sudo docker compose exec -T db pg_restore --list < "$LAB_BACKUP_DIR/enterprise_ai.dump" > "$LAB_BACKUP_DIR/archive-list.txt"
ls -lh "$LAB_BACKUP_DIR"
```

每条命令都应成功；检查数据库备份大小及目录清单。`pg_restore --list` 只能证明归档目录可读取，不能证明完整恢复成功。正式依赖备份前，需要在单独数据库或隔离环境演练恢复，并核对表、行数、角色和权限；不要把恢复命令直接指向当前实验数据库。[PostgreSQL 16 pg_dump 说明](https://www.postgresql.org/docs/16/app-pgdump.html)

同一磁盘上的备份不能防止磁盘损坏；有重要实验成果后，将备份复制到受控的另一存储位置。本课不向公共仓库提交备份、`.env` 或真实数据。

### 8.3 更新已发布代码

先确认位于 `main`、工作区没有未处理改动，并已完成上述备份：

```bash
git status --short --branch
git branch --show-current
git fetch origin
git log --oneline HEAD..origin/main
git diff --stat HEAD..origin/main
```

查看发布说明，特别留意数据库结构、镜像版本和配置格式变化。若有本地修改或当前不在 `main`，先保留工作并处理分支关系，不使用强制重置覆盖。

确认能够快进更新后：

```bash
git pull --ff-only origin main
python -m pip install -e '.[db]'
python -m pip check
sudo docker compose config --quiet
sudo docker compose up -d --wait
python -m enterprise_ai.db health
```

`--ff-only` 在分支发生分叉时拒绝合并，方便先查明差异。不要自行把 PostgreSQL 16 改成另一个大版本；数据库大版本升级需要专门的迁移步骤。代码回退也不自动回退数据库结构，遇到升级失败时保留备份和日志，按对应版本的恢复方案处理。

## 9. 本课验收：填写真实结果

下表中的“预期”是验收标准，不是已经执行的结果。

| 验收项 | 预期 | 证据 |
|---|---|---|
| 服务器基础环境 | Ubuntu 24.04 / amd64，系统 Python 3.12 | 版本检查输出摘要 |
| 项目代码 | 本批内容已发布到 `main`，关键文件存在 | 实际提交号 |
| Python 隔离 | 解释器来自项目 `.venv`，依赖无冲突 | 解释器路径与 `pip check` 结果 |
| 配置保护 | `.env` 权限 `600`，已被 Git 忽略 | 权限和忽略检查；不提供文件内容 |
| Compose 配置 | 静默校验成功 | 命令退出码 |
| 数据库服务 | `db` 运行且健康，主机绑定 `127.0.0.1` | `compose ps` 的脱敏摘要 |
| 项目连接 | `python -m enterprise_ai.db health` 成功 | 真实输出摘要与退出码 |

反馈模板：

```text
执行日期：
服务器 Ubuntu / 架构 / Python：
本批是否已经发布到 main：
项目提交号：
Docker Engine / Compose 版本：
Docker 是复用还是新装：
虚拟环境解释器路径（可隐去用户名）：
psycopg 版本 / pip check 结果：
.env 权限 / Git 是否忽略（不贴内容）：
数据库主机绑定地址及端口：
compose config --quiet 退出码：
db 状态是否 healthy：
Python health 输出摘要 / 退出码：
尚未执行或失败的步骤及错误摘要：
```

只有获得这些真实证据，才能把 M01 标记为已验收。如果尚在等待发布、服务器无法联网、镜像无法拉取或命令未执行，就明确记录该状态。Windows 上的文档检查不能替代 Ubuntu 上的数据库验收。

## 10. 练习与参考答案

**练习 1：** 为什么已安装 CUDA 13 和 Ollama，本课仍无需安装 PyTorch？

参考答案：本课 Python 负责读取配置和访问数据库，PostgreSQL 在 CPU 上运行。CUDA、Ollama 和 PyTorch 是不同组件；后续训练才需要验证驱动、PyTorch runtime 和训练依赖的组合，届时使用独立环境。

**练习 2：** 在新终端运行 Python，出现找不到 `enterprise_ai`，第一步应该检查什么？

参考答案：确认进入正确项目并激活 `.venv`，检查 `sys.executable`。新终端不会继承之前的激活状态；不应先在系统 Python 里重复安装。

**练习 3：** 数据库已运行一周，修改 `.env` 密码后认证失败，为什么？

参考答案：初始化变量只在空数据目录首次初始化时设置数据库角色。已有数据卷中的真实密码没有随 `.env` 改变。先恢复匹配的旧配置；确需轮换时，应单独安排数据库内密码修改和客户端配置同步。

**练习 4：** `db` 显示健康，是否能说明两个读者角色的数据隔离已经正确？

参考答案：不能。健康检查只覆盖服务是否就绪；读者角色能看到哪些行、是否能写入，需要第 2 课使用对应身份执行权限验证。

**练习 5：** 服务器已有服务占用 `5432`，怎样让实验继续且保留原服务？

参考答案：找到空闲端口，如 `55432`，修改本项目 `.env` 的 `POSTGRES_PORT`，重新校验并应用 Compose 配置。主机绑定仍为 `127.0.0.1`，容器内端口保持 `5432`。

**练习 6：** 备份文件存在、`pg_restore --list` 成功，能否宣布灾难恢复已经通过？

参考答案：不能。还需在隔离环境实际恢复并验证数据与权限；归档目录检查只是备份检查的一部分。
