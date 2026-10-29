[← 返回 README](../../README_ZH.md)

# 从 PDF 到快照：用 run.sh 处理一个项目

本文说明如何把一批 PDF 和一个 BibTeX 文件处理成可部署的快照数据库。做法是让 `utils init-run` 生成一个 `run.sh`，再运行它。

完成后，项目目录里有 OCR 结果、两份摘要 JSON、译文，以及 `dist/` 下的快照数据库和静态资源。

## 开始之前

你需要：

- 源码仓库的检出目录。`run.sh` 通过 `uv run --project <仓库根目录> deepresearch-flow` 调用命令，仓库根目录在生成时写入脚本。
- `config.toml`：LLM provider 与 `main_model`，格式见 [README](../../README_ZH.md) 的“配置”一节。
- `ocr.toml`：OCR 后端配置，从 `ocr.example.toml` 复制，并设置其中引用的 token。
- `tmux`：完整流程用它并行运行三条链。
- `mmdc`（Mermaid 命令行）：`fix-mermaid-deep_read` 步骤用它校验图表。在仓库根目录执行 `npm install` 安装；`run.sh` 会把 `<仓库根目录>/node_modules/.bin` 加进 `PATH`。

## 准备项目目录

手动创建一个目录，放入 `pdf/` 和恰好一个 `.bib` 文件：

```text
myproj/
├── pdf/          # 全部 PDF
└── myproj.bib    # BibTeX，目录里只能有一个 .bib
```

之后所有输出都写在这个目录里，路径全部是相对路径，所以可以整个目录打包搬走。

## 生成并运行

在仓库根目录执行：

```bash
uv run deepresearch-flow utils init-run ./myproj
```

命令生成 `myproj/run.sh`。`init-run` 不接收模型参数：脚本里每个调用 LLM 的命令都带一个占位符 `--model PROVIDER/MODEL`，需要你逐条改成真实的 `provider/model`。各步骤可以使用不同的模型。

用编辑器打开 `run.sh`，把每处 `PROVIDER/MODEL` 替换为 `config.toml` 中已声明的模型，例如 `openai/gpt-4o-mini`。共有 8 处，分布在 `fix-math`、`simple`、`fix-math-simple`、`deep_read`、`fix-math-deep_read`、`fix-mermaid-deep_read`、`translate`、`fix-math-translated` 这些步骤里。

对已存在 `run.sh` 的目录再次执行 `init-run`，会直接覆盖该文件，你改过的模型和参数随之丢失。需要保留修改时，先备份 `run.sh`。

要运行的步骤里仍有占位符时，`run.sh` 在执行任何命令之前停止，并指出是哪个步骤。

然后运行：

```bash
./myproj/run.sh
```

### init-run 的选项

| 选项 | 默认值 | 作用 |
|---|---|---|
| `PROJECT` | 无，必填 | 项目目录。必须已存在，且含 `pdf/` 和恰好一个 `.bib` |
| `--target-lang` | `zh` | 译文的目标语言 |
| `--config` | `config.toml` | LLM 配置。按当前目录解析，以绝对路径写入脚本 |
| `--ocr-config` | `ocr.toml` | OCR 配置，处理方式同 `--config` |
| `--rounds` | `3` | 每个步骤对失败条目的最多重试轮数 |

配置文件路径以绝对路径写入脚本。把项目搬到另一台机器后，修改 `run.sh` 开头的 `CONFIG`、`OCR_CONFIG` 和 `REPO` 三个变量。

## 流程

完整流程分三段：

1. `ocr` 链依次执行 `ocr`、`fix`、`fix-math`、`organize`。
2. 三条链在 tmux 里并行：`simple` 链、`deep_read` 链、`translate` 链。每条链内部按顺序执行。
3. 三条链都完成后，执行 `build`。

`embed`、`api push`、`api update` 不在脚本里，需要手动执行。

### 检查点、中断与恢复

模型请求可能因超时、provider 额度、进程崩溃或机器断电而中断。流程按任务边界保存检查点，重新运行时只处理尚未确认成功的任务：

- `paper extract` 会保存已成功处理的论文结果。`deep_read` 会把 stage 状态写入 `paper_stage_outputs/`。
- 抽取结果和 stage 状态使用临时文件、文件 `fsync`、原子替换和父目录 `fsync` 写入。该保证依赖本地文件系统和硬件正确实现 `fsync`。
- 翻译成功的 node 写入 `.translate_cache/`。只有所有 node 通过校验并安全发布完整译文后，程序才删除 cache。
- 翻译仍有失败 node 时，不发布正常译文，不覆盖已有正常输出，并返回部分失败。下次运行会保留并使用成功 node 的 cache。
- provider 不支持 idempotency key 时，客户端无法消除“请求可能已被 provider 接收、但结果尚未落盘”窗口中的重复请求或重复计费风险。已落盘的成功任务不会因为外层流程中断而自动重跑。

额度检查和等待由单步程序负责。`run.sh` 不提供跨步骤、跨进程的全局 quota 调度；重启后，内存中的额度状态也不会保留。

`run.sh` 只负责步骤顺序、日志、通知和 tmux 链。步骤内部的重试、fallback、额度等待、缓存和检查点由对应程序负责。`logs/<步骤>.done` 只表示该步骤最近一次返回成功，不能证明输入、配置内容或 prompt 文件没有变化。

脚本只在 `ocr` 链完成之后才启动 tmux。查看并行链的进度：

```bash
tmux -L drflow-myproj attach -t run
```

其中 `myproj` 是项目目录名。每个项目使用独立的 tmux 服务，所以 shell 里的 API key 和 `NOTIFY_URL` 会传给各条链。

## 只运行指定步骤

在命令行列出步骤名，用逗号或空格分隔：

```bash
./myproj/run.sh fix-simple,fix-math-simple
```

指定步骤时：

- 按给定顺序一个接一个运行，不使用 tmux。
- 即使 `logs/<步骤>.done` 已存在，也重新运行。
- 某一步失败就停止，后面的步骤不再运行。
- 步骤名拼错时，在运行任何步骤之前报错并列出全部步骤名。

`./myproj/run.sh list` 打印全部步骤名。

从另一个终端结束整个流程：

```bash
./myproj/run.sh stop
```

它只停止本项目专用 tmux 会话中的并行链及其子进程。OCR 和 `build` 在前台运行，不能从另一个终端通过该命令停止。用 `kill`（SIGTERM）结束主流程也可以；不要用 `kill -INT`，bash 在等前台命令时会忽略 SIGINT。

## 停止运行

Ctrl-C、Ctrl-Z 或 `kill` 都会停止整个流程，包括 tmux 里的三条链。脚本会：

- 停止 tmux 会话中的链进程，以及当前步骤启动的进程组；
- 把当前步骤标记为失败（`logs/<步骤>.failed`），所以重跑会从这一步继续；
- 发一条 interrupted 通知。

停止后修好问题，重新运行 `./myproj/run.sh` 即可。已完成的步骤会被跳过。

## 打印执行的命令

每条实际执行的命令都以 `+` 开头打印，可以直接复制到终端里单独重跑：

```text
+ drflow recognize fix-math --input ocr -r --in-place --config /path/config.toml --model openai/gpt-4o-mini --workers 4 --batch-size 10 --report logs/fix-math-ocr.json
```

命令也写进 `logs/<步骤>.log`。

## 需要按经验调整的参数

脚本开头的设置块里有四个可调项：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `WORKERS` | 4 | `fix-math` 和 `fix-mermaid` 的并发数 |
| `BATCH_SIZE` | 10 | 每个请求处理的公式或图表数 |
| `MAX_CONCURRENCY` | 4 | `paper extract` 的并发请求数 |
| `OCR_WORKERS` | 4 | 同时做 OCR 的文件数 |
| `EXTRACT_TIMEOUT` | 360 | `paper extract` 每个请求的超时秒数 |

改这几个值就能调并发和批大小。模型不在这里：每个步骤的命令行里有自己的 `--model`，单独改那一行。

`EXTRACT_TIMEOUT` 要留够。一篇 10 万 token 的文档在 `doubao-seed-2.1-pro` 上要 90 秒左右，多阶段模板更久。超时太短时请求会被断开，错误报告里写明超时秒数。

## 失败后怎么办

每个步骤的结果记录在 `logs/` 下：

| 文件 | 含义 |
|---|---|
| `logs/<步骤>.log` | 该步骤的完整输出 |
| `logs/<步骤>.done` | 该步骤已成功 |
| `logs/<步骤>.failed` | 该步骤失败 |

完整流程中，某一步失败会终止所在的链，且不会执行 `build`。修复原因后重新运行 `./myproj/run.sh`：已有 `.done` 的步骤被跳过，流程从失败的那一步继续。要重做某个已完成的步骤，删除它的 `.done` 文件，或者在命令行指定它。

### 退出码

批处理命令（`extract`、`translate`、`ocr`、`fix-math`、`fix-mermaid`）使用同一套退出码：

| 退出码 | 含义 | `run.sh` 的处理 |
|---|---|---|
| 0 | 全部成功 | 步骤成功 |
| 1 | 命令自身出错，重试没有意义 | 步骤失败 |
| 3 | 输出和错误报告已写出，但部分条目失败 | 步骤失败，不写 `.done`；修复原因后重新运行 |

其他非零退出码按失败处理。

重试方式有四种，写在每个步骤命令的前面。它们对应各命令真正支持的能力：

- `retry`：再运行同一条命令，并加上 `--retry-failed`，只处理失败的条目。只有 `paper extract`、`recognize fix-math` 和 `recognize fix-mermaid` 接受这个选项。
- `retry_stages`：再运行 `deep_read`，并加上 `--retry-failed-stages`，只处理失败的 stage。
- `rerun`：再运行完全相同的命令。`recognize ocr` 和 `translator translate` 没有 `--retry-failed`，但它们本来就会跳过已完成的文件，所以直接重跑即可。
- `once`：只打印警告，不重试。`recognize fix` 和 `recognize organize` 没有重试能力。

`run.sh` 不会把部分失败转换为成功，也不会为该步骤写入 `.done`。`paper extract`、`fix-math` 和 `fix-mermaid` 使用错误报告定位失败条目；`deep_read` 使用 `--retry-failed-stages`，只重试失败 stage。翻译失败时，程序保留 node cache 并返回部分失败，外层重新运行同一命令即可继续。

## 通知

设置环境变量 `NOTIFY_URL` 后，每条链结束、任一步骤失败、`build` 完成时，脚本发送一条通知：

```bash
export NOTIFY_URL='https://<通知服务>/api/<uuid>/get'
```

通知服务是 [simple-telegram-notification-bot](https://github.com/nerdneilsfield/simple-telegram-notification-bot)。URL 中的 `<uuid>` 相当于密码，所以脚本只从环境变量读取它，不写进文件。未设置 `NOTIFY_URL` 时不发送通知。发送失败不影响流程。

## 步骤参考

下表的“输入”和“输出”都是相对于项目目录的路径。标有“LLM”的步骤需要把占位符替换为真实模型。

| 步骤 | 命令 | LLM | 输入 | 输出 | 重试方式 |
|---|---|---|---|---|---|
| `ocr` | `recognize ocr` | 否 | `pdf/` | `ocr/` | `rerun` |
| `fix` | `recognize fix` | 否 | `ocr/` | 原地修改 | `once` |
| `fix-math` | `recognize fix-math` | 是 | `ocr/` | 原地修改，报告 `logs/fix-math-ocr.json` | `retry` |
| `organize` | `recognize organize` | 否 | `ocr/` | `md_simple/`、`md_base64/` | `once` |
| `simple` | `paper extract` | 是 | `md_simple/` | `simple.json`，错误 `logs/simple-errors.json` | `retry` |
| `fix-simple` | `recognize fix` | 否 | `simple.json` | 原地修改 | `once` |
| `fix-math-simple` | `recognize fix-math` | 是 | `simple.json` | 原地修改，报告 `logs/fix-math-simple.json` | `retry` |
| `deep_read` | `paper extract` | 是 | `md_simple/` | `deep_read.json`，错误 `logs/deep_read-errors.json` | `retry_stages` |
| `fix-deep_read` | `recognize fix` | 否 | `deep_read.json` | 原地修改 | `once` |
| `fix-math-deep_read` | `recognize fix-math` | 是 | `deep_read.json` | 原地修改，报告 `logs/fix-math-deep_read.json` | `retry` |
| `fix-mermaid-deep_read` | `recognize fix-mermaid` | 是 | `deep_read.json` | 原地修改，报告 `logs/fix-mermaid-deep_read.json` | `retry` |
| `translate` | `translator translate` | 是 | `md_base64/` | `md_base64_translated/` | `rerun` |
| `fix-translated` | `recognize fix` | 否 | `md_base64_translated/` | 原地修改 | `once` |
| `fix-math-translated` | `recognize fix-math` | 是 | `md_base64_translated/` | 原地修改，报告 `logs/fix-math-translated.json` | `retry` |
| `build` | `paper db snapshot build` | 否 | 上面的 JSON、Markdown、`pdf/`、`.bib` | `dist/<项目名>.db`、`dist/<项目名>-export/` | 无 |
| `embed` | `paper embed` | 是 | `build` 生成的 snapshot 和静态导出目录 | `dist/<项目名>_embed/` | 需要 embedding 配置和 provider |
| `push-api` | `paper db api push --only-api` | 是 | snapshot、静态导出目录、`remote.toml` | 远端 API | 只推送 API 数据 |
| `push-storage` | `paper db api push --only-storage --storage-bundles` | 否 | 静态导出目录、`remote.toml`、`7z` | 本地 `dist/bundles/<batch-id>/` 和远端 `bundles/<batch-id>/` | 远端手动执行 `apply.sh` |

### 分卷上传静态文件

`push-storage` 不逐个上传静态文件。它使用 7-Zip 创建一个高压缩率的 solid archive，并以 50 MB 分卷上传到 WebDAV。压缩命令使用 LZMA2、最高压缩级别和多线程：

```bash
bash run.sh push-storage
```

生成的本地目录类似：

```text
dist/bundles/<batch-id>/
├── bundle.7z.001
├── bundle.7z.002
├── apply.sh
└── files.txt
```

远程目录位于 `remote.toml` 中配置的 WebDAV 根目录下：

```text
bundles/<batch-id>/
├── bundle.7z.001
├── bundle.7z.002
└── apply.sh
```

在 WebDAV 服务器上安装 `7z` 后，进入远程批次目录并执行：

```bash
bash apply.sh /absolute/path/to/static-export
```

脚本只在解压成功后删除远程 `.7z.*` 分卷，并保留 `apply.sh`。本地分卷不会被删除。解压失败时，远程分卷仍然保留，可以重新执行脚本。

创建远程目录、上传分卷或上传 `apply.sh` 时，如果发生网络异常或可重试 HTTP 错误，客户端冷却 60 秒后重试，最多重试 2 次。重试只处理当前操作，不重新压缩或重传已成功的分卷。认证失败直接退出；冷却期间可按 Ctrl-C 停止。

补充说明：

- `fix-mermaid` 只作用于 `deep_read.json`。
- `simple` 和 `deep_read` 在 `logs/simple/`、`logs/deep_read/` 目录下运行，因为 `paper extract` 把中间结果写到当前目录的 `paper_stage_outputs/`。
- `build` 在 `dist/<项目名>.db` 已存在时，加上 `--previous-snapshot-db`，使重建后的 `paper_id` 保持不变。
- `translate` 把调试信息写到 `logs/translator/`。

## 手动逐步运行

`run.sh` 里的每个步骤只是一条普通命令。需要脱离脚本时，直接复制对应的行。这里以 `deep_read` 链为例，命令在项目目录下执行：

```bash
mkdir -p logs/deep_read
(cd logs/deep_read && uv run deepresearch-flow paper extract \
  --input ../../md_simple --config <config.toml 的路径> \
  --model PROVIDER/MODEL --prompt-template deep_read \
  --output ../../deep_read.json --errors ../../logs/deep_read-errors.json)

uv run deepresearch-flow recognize fix --input deep_read.json --json --in-place

uv run deepresearch-flow recognize fix-math --input deep_read.json --json --in-place \
  --config <config.toml 的路径> --model PROVIDER/MODEL --report logs/fix-math-deep_read.json
```

`<config.toml 的路径>` 和 `PROVIDER/MODEL` 是占位符，运行前替换。命令返回 3 时，在同一条命令后加 `--retry-failed` 再运行一次。

## 后续步骤

`run.sh` 结束于 `build`。之后：

- 构建向量索引：见 [功能参考](reference.md) 中的语义搜索。
- 部署快照与静态资源：见 [部署指南](deployment.md)。
- 把快照推送到远端或更新已有论文：见 [API 与 MCP](api-and-mcp.md)。
- 对已有数据做增量构建、合并或补充：见 [高级工作流](workflow.md)。
