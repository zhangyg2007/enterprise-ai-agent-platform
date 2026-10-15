# 第 0 课：只读检查 Ubuntu 服务器

本课只采集信息，不安装软件、不停止 Ollama、不下载模型、不修改驱动。所有命令均在 **Ubuntu 服务器的终端** 执行，不在 Windows 执行。

目标是得到真实环境清单，为下一课选择兼容的安装方案。本文命令尚未在你的服务器执行，示例说明不是实测结果。

## 1. 系统、CPU、内存和磁盘

逐条执行：

```bash
cat /etc/os-release
uname -m
lscpu
free -h
df -hT
```

预期：系统标识为 Ubuntu 24.04，架构为 x86_64；核对 CPU 型号、内存及计划存放模型的磁盘剩余空间。硬件标称 128GB 与系统显示的 GiB 数值可能不同。

记录工作目录和模型目录所在分区，后续训练检查点、导出模型与数据库会额外占用空间。此时不扩容、不删文件。

## 2. NVIDIA 驱动和 CUDA Toolkit

```bash
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv
```

记录显卡名称、驱动版本、总显存和当前占用。现有 Ollama 模型可能占用显存，当前只观察。

若系统安装了 CUDA 编译工具，可执行：

```bash
if command -v nvcc >/dev/null 2>&1; then
  nvcc --version
else
  printf 'nvcc 未在 PATH 中；这不等于显卡或驱动不可用。\n'
fi
```

区别：

| 检查项 | 能说明什么 | 不能直接说明什么 |
|---|---|---|
| nvidia-smi | 驱动、GPU、显存和驱动支持的 CUDA 信息 | 当前 Python 装了哪个 PyTorch runtime |
| nvcc --version | 当前 PATH 对应的 CUDA Toolkit 编译器 | PyTorch 是否能使用 GPU |
| torch.version.cuda | 当前环境中 PyTorch 的 CUDA 构建版本 | 系统安装的全部 Toolkit 版本 |

如果已有 PyTorch 环境，先激活那个环境，再执行下面的只读检查；若没有，跳过，下一课再安装。

```bash
python -c "import torch; print('torch:', torch.__version__); print('runtime:', torch.version.cuda); print('available:', torch.cuda.is_available())"
```

出现 `ModuleNotFoundError` 仅说明当前 Python 环境没有 PyTorch，不应据此重装驱动。[NVIDIA 兼容说明](https://docs.nvidia.com/deploy/cuda-compatibility/why-cuda-compatibility.html)

## 3. 核实 Ollama 模型

```bash
ollama --version
ollama list
ollama ps
```

从 `ollama list` 复制真实模型名称。如果确实为你提供的名字，再执行：

```bash
ollama show 'qwen3.8:27b'
```

若提示模型不存在，以列表为准，不猜测或自动替换成另一个 Qwen 型号。记录架构、参数规模、量化级别、上下文配置和模型 ID。

默认本地地址的 API 检查：

```bash
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
```

如果你修改过监听地址，使用原来的配置地址。连接失败不代表模型丢失，先核对服务与端口，不重新下载模型。该请求只列出模型，不触发推理。[Ollama 列表 API](https://docs.ollama.com/api/tags)

## 4. 现有工具与端口

```bash
git --version
python3 --version
```

Docker 只有在已安装时才检查：

```bash
if command -v docker >/dev/null 2>&1; then
  docker --version
  docker compose version
else
  printf 'Docker 尚未安装，留给第 1 课处理。\n'
fi
```

查看计划使用端口是否已有监听者：

```bash
ss -ltn
```

重点核对 PostgreSQL 5432、应用 API 8000、Ollama 11434。被占用时记录，不停止现有服务。LLaMA-Factory WebUI 和模型 API 的端口在对应章节确定。

## 5. 本课验收与反馈模板

将下面信息反馈到聊天即可；原始输出不必提交到公开仓库，不提供 Token、私钥、完整环境变量或个人路径中的敏感内容。

```text
操作系统/架构：
CPU 型号与逻辑核数：
内存：
模型所在分区及剩余空间：
GPU/驱动版本/显存：
nvidia-smi 显示的 CUDA 版本：
nvcc 版本（没有则写未安装或不在 PATH）：
现有 PyTorch 版本/runtime（没有则写未安装）：
Ollama 版本：
ollama list 中的完整模型名称：
模型架构/参数规模/量化级别：
Docker / Compose 是否已安装：
5432、8000、11434 端口占用：
```

完成条件：上述关键项有真实输出支撑；缺失项明确写“未安装”“未检查”或具体错误。本课不需要任何训练结果。

思考题：为什么 `nvidia-smi` 显示 CUDA 13，却可能使用 CUDA 12 构建的 PyTorch？为什么能运行 27B 量化模型，不等于能用同样显存训练它？下一课会用环境和显存构成解释。

后续 GitHub 同步使用 [指定仓库](https://github.com/zhangyg2007/enterprise-ai-agent-platform)。当前本地文档尚未推送；服务器拉取步骤在首批内容发布后提供。
