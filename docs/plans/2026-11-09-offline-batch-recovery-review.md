# 离线批处理流程恢复与省钱审阅结论

日期：2026-11-09
状态：审阅结论，尚未实施。本文档经过四轮只读交互审阅后收敛。审阅过程中没有运行任何模型、OCR、翻译、embedding、真实 provider 调用或上传。

## 目标

在模型更换、provider 额度限制、429/5xx、超时、进程崩溃、断电和用户中断等情况下，离线批处理流程（`utils/init_run.py` 生成的 run.sh，以及它调用的 `drflow` 命令）应满足以下要求：

- 已落盘的成功结果不重复请求。
- 部分失败不显示为完成。
- 已付费的结果不因覆盖写或断电而丢失。
- 不在用户没有选择的情况下自动付费重算。

非目标：

- 不合并 pipeline 的 SQLite lease 体系。
- 不引入跨进程全局调度或持久配额框架。
- 不承诺 provider 侧 exactly-once。

## 确认的问题

以下行号以审阅时的代码为准。

### 1. 结果快照写入不耐久，且存在并发写

- `paper/extract.py:910` 的 `write_json` 直接调用 `write_text`。`persist_output_snapshot`（第 1479–1482 行）每完成一篇论文就用它覆盖输出文件。断电后，这个文件可能为空或被截断。`load_existing`（第 731–744 行）遇到损坏的 JSON 会报错退出，所以不会静默重算。但已付费的成功结果会丢失。
- `persist_output_snapshot` 只在构造 payload 时持有 `results_lock`，释放锁之后才执行 `to_thread(write_json)`。有 `max_concurrency` 个 worker 在并发调用它（第 1653、1979、2371 行），所以多个线程可能同时写同一个文件，较旧的快照也可能覆盖较新的快照。这一点是从代码推断的，没有复现。
- `write_json_atomic`（第 915–919 行）使用固定的 `.json.tmp` 作为临时文件，也没有 fsync。在并发场景下，直接把 `write_json` 换成它会争用同一个临时文件。
- `translator/node_cache.py:43–47` 的 `NodeCache.save` 是“临时文件加 replace”，没有 fsync。
- translator 的正常输出在 `scheduler.py` 的 `finalize` 里直接 `write_text`。

### 2. 错误报告损坏时，retry 被跳过并显示为成功

`load_errors`（`extract.py:747–753`）遇到 JSON 语法错误时返回 `[]`。retry 模式（第 1204–1250 行）因此筛不出任何文件，本轮什么也不处理。按代码推断退出码为 0，run.sh 会写 `.done`，失败的论文就被永久漏掉了。

### 3. 翻译部分失败被当作成功

`translator/scheduler.py` 的 `finalize`（约第 548–560 行）把失败节点替换成原文，照常写出输出文件，并调用 `cache.discard()`。`on_done` 只把抛出异常的文件计入 `failed_files`，所以 CLI 的退出码是 0。下一次运行时，`translator/cli.py:638` 看到输出文件已存在，就跳过这个文件。

现有测试锁定了这个语义：`translator/tests/test_scheduler.py` 的 fuzz 用例里，`"fail"` 模式期望输出等于原文，并断言 `failed == []`（约第 476–483 行和第 779 行）。

### 4. translator 先用完内层重试才识别额度耗尽

额度识别机制已经存在：`paper/routing.py:582–640` 的 `mark_quota_exceeded` 按 `quota_error_tokens` 匹配错误响应，并设置 `_quota_until`。extract（`extract.py:1051–1084`）的顺序是正确的，先识别额度，再走普通重试。

translator 的 `_translate_group`（`translator/engine.py:771–856`）则不同。它在内层对 `retryable` 异常用同一个 route 退避重试，直到 `max_retries` 次才抛出。异常传到外层后，`mark_quota_exceeded` 才被调用，调用位置在 `scheduler.py:659–668` 和 `engine.py:959`。所以额度已经耗尽的 route 仍会被同一个分组重复请求。

### 5. run.sh 的状态和重试缺陷

位置：`utils/init_run.py` 中的模板。

- `_again` 在重试循环里不重置 `rc`。某一轮重试成功后，后续轮次仍会继续执行，最多到 `ROUNDS` 轮。这些轮次可能产生重复请求，也可能是无意义的空跑，取决于错误报告是否已经更新。
- `once` 和 `_again` 遇到 PARTIAL（退出码 3）时 `return 0`，`run_step` 随后写 `.done`，部分失败因此显示为完成。
- `s_deep_read` 使用 `--retry-failed`。由 `extract.py:1738` 可知，这会让失败论文的所有 stage 都重跑，已经成功的 stage 会再付一次费。应改用 `--retry-failed-stages`。
- `tmux new-session` 和 `new-window` 的错误都被吞掉了。窗口没有起来时，主循环会无限等待。
- `stop` 只执行 `kill-session`。chain 是通过 shell 启动的，`kill-session` 之后可能留下孤儿进程，继续请求 provider。
- 两个 pid 文件的生命周期：`logs/chain-<name>.pid` 只靠 `trap EXIT` 删除。chain 被 KILL 或机器断电时，这个文件会残留。`logs/parent.pid` 写入后从不删除。直接读残留的 pid 去执行 `kill_tree`，可能误杀复用了该 pid 的无关进程。
- `s_deep_read` 的注释声称“复用 simple.json 的 module_a”，这与代码不符。审阅时没有找到对应实现，这条注释是错误的。

### 6. simple 路径的复用只看 source_hash

`extract.py:1528–1532` 在非 stage 路径上只比较 `source_hash`。修改语言或模板之后，旧结果仍会被直接复用。stage 路径（deep_read）已经用 `_compute_prompt_hash` 比较语言、模板文本和 stage 字段，hash 不一致就重跑。两条路径的语义不一致。

### 7. 错误过滤会删掉旧的成功结果

`filter_results_with_errors`（`extract.py:793–809`）在最终输出时（第 2416 行），把 errors 中出现过的 `source_path` 整体剔除，`existing` 里旧的成功结果也会被一并删掉。例如，某篇论文的源文件改了，重算失败后，它原有的成功结果就从输出中消失了。这是已有行为，不在本轮修复范围内，但任何新增的“报告类”条目都不能进入 errors（见修复项 6）。

## 最小修复项

1. **耐久写**：`write_json_atomic` 改为依次执行：写入临时文件，对文件 fsync，`os.replace`，再对父目录 fsync。目录 fsync 可复用 `paper/snapshot/auth.py:51` 的 `_fsync_directory` 写法。临时文件名要唯一，避免并发争用。`persist_output_snapshot` 改为在同一把锁内完成构造 payload 和写盘，并改调用 `write_json_atomic`。`NodeCache.save` 和翻译的正常输出也改用同样的写法。`--split` 导出（第 2437 行）是可以重建的派生结果，不改。
2. **`load_errors`**：JSON 语法损坏，或者顶层不是 list 时，报错退出。文件不存在时仍返回 `[]`。
3. **翻译部分失败**：存在失败节点时，不写正常输出，也不覆盖旧输出；保留已成功节点的 cache；把该文件计入 `failed_files`，让 CLI 返回 3。没有 cache 或首个节点就失败时，由于没有输出文件，下次运行会自然重新处理。同步修改 fuzz 测试中 `"fail"` 模式的期望。本轮不新增“可读的部分译文”输出格式。
4. **translator 的额度识别**：给 `_translate_group` 增加一个可选参数 `quota_check`，类型是 async callable，返回 bool。两个调用者（`scheduler.py:642` 和 `engine.py:941`）各自传入绑定了自身 pool 的 `mark_quota_exceeded`。内层捕获到 `ProviderError` 后先调用 `quota_check`：命中就直接抛出，交给外层重新选路；未命中就保持原有的退避重试。普通网络错误的重试行为不变。
5. **run.sh**：模板和已部署的 calibration run.sh 打同一个精确补丁，不用 `init-run --force` 重新生成。各步骤里用户填写的模型和并发保持原样。
   - `_again` 每轮重置 `rc`。
   - PARTIAL 不写 `.done`。
   - `s_deep_read` 改用 `--retry-failed-stages`。
   - `new-session` 或 `new-window` 失败时立即退出。
   - `stop` 和 `on_signal` 在 `kill-session` 之前，先用 `$T list-panes -s -t "$S" -F '#{pane_pid}'` 获取实时的 pane pid，再执行 `kill_tree`，不再读取残留的 pid 文件。
   - 删除“复用 simple.json”的错误注释。
6. **simple 路径的兼容判断**：新结果写入 `_compute_prompt_hash` 得到的 hash。复用旧结果时：
   - 有 hash：严格比较。
   - 没有 hash：只用已有的 `output_language` 和 `prompt_template` 字段判断是否“已知不兼容”。字段缺失时视为身份未知，照常复用，不补写 hash。
   - 已知不兼容：旧结果仍然保留在输出里，不发请求，也不进入 errors，以免被 `filter_results_with_errors` 删除。这些条目单独汇总报告，运行结束时返回 3。
   - 本轮不新增“只重算不兼容条目”的开关。`--force` 会重算全部条目（第 1528 行）。
   - 不以实际使用的 provider/model 判断失效，因为 fallback 和路由池本来就会让实际使用的模型不同。

明确不做：

- 后置的 `recognize fix` 不删除。`translator/engine.py:486–498` 只在翻译前执行 `fix_markdown`，`finalize_document`（第 538–553 行）不再修复，所以后置的 fix 处理的是新产生的译文。
- 不给 `.done` 加指纹。
- 不自动清理旧的 `.done`，也不自动重跑。
- 模板中 embed 的模型、并发、RPM 和 TPM 是否改用 config 或 CLI 的默认值，要先核对 `paper embed` 的现有默认值再决定，不要机械地替换成空变量。

## 保证边界与保留限制

- **exactly-once**：provider 不支持 idempotency key 时，“已扣费但结果尚未落盘”这个窗口无法消除。能保证的只有：已落盘的成功结果不会再被请求。
- **耐久性**：依赖本地文件系统和磁盘正确实现 fsync，不覆盖网络文件系统，也不覆盖缓存会说谎的硬件。本次运行中新建的目录，其目录项要等上一级目录 fsync 后才持久，所以在 `mkdir` 新目录时要对它的上一级目录 fsync 一次。
- **`.done`**：只说明某一步曾经以退出码 0 结束，不能证明当时的命令参数、config 文件内容或 prompt 与当前一致。修改这些之后，需要用户用 `./run.sh <step>` 显式重跑。`.done` 能否恢复完整的配置语义，仍然没有解决。
- **额度状态**：只保存在内存中。进程重启后会丢失已经识别出的额度状态，需要重新从 provider 响应中获知。重新识别之前会多出几次失败请求，次数取决于并发数、在途请求和调用路径。没有配置 `quota_error_tokens`，或者没有配置 `reset_time`/`quota_duration` 时，额度错误只按普通的 retryable 错误处理。
- **stop**：只能停止本项目 tmux 会话中的 chain 及其子进程。OCR 阶段和 build 阶段在主进程前台运行，只能在运行它们的终端按 Ctrl-C 停止。用 pane pid 代替 pid 文件后，在“获取 pid”和“kill”之间仍有短暂的复用竞态。`kill_tree` 会连带 kill 整个进程组。专属 socket 由 `NAME` 生成，两个同名项目仍会冲突。
- **历史译文**：旧版本写出的、混有原文的“成功”译文，不会被检测、删除或重译。
- **simple 路径**：只比较元数据时，检测不到模板文件内容或自定义 prompt 的变化。没有 hash 的旧条目，身份保持未知。

## 待决问题

1. 已知不兼容的条目以退出码 3 结束时，run.sh 的 `retry` 会再执行一次 `--retry-failed`。这次运行不会发出请求，但会白跑一遍。是否要给“不兼容”单独分配一个退出码？这会改变 CLI 的接口。
2. `filter_results_with_errors` 在重算失败时会删掉旧的成功结果，这个问题是否另立任务修复？
3. `.done` 的完整配置语义，是否值得在后续单独设计？

## 建议的实施顺序

1. run.sh 的确认缺陷（修复项 5），只改 shell 模板和已部署的 run.sh。
2. 耐久写与快照串行化（修复项 1），以及 `load_errors`（修复项 2）。
3. 翻译部分失败的语义（修复项 3），连同测试期望一起修改。
4. translator 的额度识别（修复项 4）。
5. simple 路径的兼容判断（修复项 6）。

每一步完成后，运行对应的窄测试，例如 `translator/tests/test_scheduler.py` 和 `paper/tests` 中 extract 相关的测试。不要运行仓库级的 formal、fuzz 或 deep verification。
