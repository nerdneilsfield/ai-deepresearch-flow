[← 返回 README](../README_ZH.md)

# 高级工作流

## 一键生成整条流水线（init-run）

准备一个项目目录，里面放 `pdf/` 和恰好一个 `.bib`，其余输出都写在这个目录下：

```bash
uv run deepresearch-flow utils init-run ~/pdfs/myproj -m provider/model
export NOTIFY_URL='https://通知服务/api/<uuid>/get'   # 可选
~/pdfs/myproj/run.sh
```

`run.sh` 的流程是：`ocr → fix → fix-math → organize`，然后 `simple`、`deep_read`、`translate`
三条链（每条链里的步骤顺序执行）在 tmux 里并行（`tmux -L drflow-<项目名> attach -t run` 查看），三条都完成后执行 `build`。
`embed`、`api push`、`api update` 不在脚本里，需要手动执行。

- 每个步骤是脚本里的一个函数 `s_<步骤名>`，一行一条命令，直接改那一行就能改参数。
  `./run.sh list` 列出所有步骤名。
- 指定步骤：`./run.sh fix-simple,fix-math-simple`（逗号或空格分隔），按给定顺序一个接一个跑，
  即使该步骤已有 `.done` 也会重跑；某一步失败就停止，后面的不再执行。
- 不带参数是完整流程。重跑时已完成的步骤（`logs/<步骤>.done`）会被跳过，从失败的那一步继续。
- 某一步失败会写 `logs/<步骤>.failed`，脚本不会执行 `build`。日志在 `logs/<步骤>.log`。
- 脚本是普通 bash，路径全是相对于项目目录的。
- `NOTIFY_URL` 指向 [simple-telegram-notification-bot](https://github.com/nerdneilsfield/simple-telegram-notification-bot)
  的 `/api/<uuid>/get`；不设置则不通知。

### 退出码约定

批处理命令（`extract`、`translate`、`ocr`、`fix-math`、`fix-mermaid`）使用统一的退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 全部成功 |
| 1 | 命令自身出错，重试没有意义 |
| 3 | 输出和错误报告已写出，但部分条目失败，值得带 `--retry-failed`（`translate` 直接重跑）再试 |

`run.sh` 对退出码 3 最多重试 3 轮（`--rounds` 修改），其他非零退出码直接终止该链。

## 增量构建 PDF 文献库

如果你的 PDF 库在持续增长，不用每次都全量重建。下面的流程会找出新增的 PDF，只处理它们，然后合并回已有库中。

> 前提：已有处理完成的 JSON 文献库（例如 `paper_infos.json`）及其关联资源。
> 若起点只有 PDF 和可选 BibTeX，请先走 README 的从零流程。

```bash
# 1) 对比已处理的 JSON 和新 PDF 库，找出缺失的 PDF
uv run deepresearch-flow paper db compare \
  --input-a ./paper_infos.json \
  --pdf-root-b ./pdfs_new \
  --output-only-in-b ./pdfs_todo.txt

# 2) 把缺失的 PDF 单独拷贝出来，准备 OCR
uv run deepresearch-flow paper db transfer-pdfs \
  --input-list ./pdfs_todo.txt \
  --output-dir ./pdfs_todo \
  --copy

# 也可以直接移动文件，不用 --copy：
# uv run deepresearch-flow paper db transfer-pdfs --input-list ./pdfs_todo.txt --output-dir ./pdfs_todo --move

# 3) 对缺失的 PDF 做 OCR（用你习惯的 OCR 工具，把 markdown 写入 ./md_todo）

# 4) 匹配并重新定位资源文件，与新 PDF 库对齐
uv run deepresearch-flow paper db extract \
  --input-json ./paper_infos.json \
  --pdf-root ./pdfs_new \
  --output-json ./paper_infos_matched.json

uv run deepresearch-flow paper db extract \
  --md-source-root ./mds \
  --output-md-root ./mds_matched \
  --pdf-root ./pdfs_new

uv run deepresearch-flow paper db extract \
  --md-translated-root ./translated \
  --output-md-translated-root ./translated_matched \
  --pdf-root ./pdfs_new \
  --lang zh

# 5) 翻译新 OCR 的 markdown，并提取摘要
uv run deepresearch-flow translator translate \
  --input ./md_todo \
  --target-lang zh \
  --model openai/gpt-4o-mini

uv run deepresearch-flow paper extract \
  --input ./md_todo \
  --model openai/gpt-4o-mini

# 6) 合并并启动服务（支持多输入源）
uv run deepresearch-flow paper db serve \
  --input ./paper_infos_matched.json \
  --input ./paper_infos_new.json \
  --md-root ./mds_matched \
  --md-root ./md_todo \
  --md-translated-root ./translated_matched \
  --md-translated-root ./md_todo \
  --pdf-root ./pdfs_new
```

## 合并论文数据

### 合并论文 JSON

把多个 paper info JSON 文件或模板合在一起。

```bash
# 合并多个库（使用同一套模板）
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# 合并同一个库的多个模板（共享字段以第一个输入为准）
uv run deepresearch-flow paper db merge templates \
  --inputs ./simple.json \
  --inputs ./deep_read.json \
  --output ./paper_infos_templates.json
```

注意：`paper db merge` 现在拆成了 `merge library` 和 `merge templates` 两个子命令。

### 合并多个数据库（PDF + Markdown + BibTeX）

当你需要把完整的数据库——包括 PDF、markdown、JSON 元数据和 BibTeX 引用——合并到一起时，按以下步骤来：

```bash
# 1) 把 PDF 拷贝到同一个文件夹
rsync -av ./pdfs_a/ ./pdfs_merged/
rsync -av ./pdfs_b/ ./pdfs_merged/

# 2) 把 Markdown 文件夹也拷到一起
rsync -av ./md_a/ ./md_merged/
rsync -av ./md_b/ ./md_merged/

# 3) 合并 JSON 库
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# 4) 合并 BibTeX 文件
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

### 合并 BibTeX 文件

也可以单独合并 BibTeX 文件。

```bash
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

遇到重复的 key 时，保留字段更多的那个条目；字段数一样多则按输入顺序，先到先得。

### 推荐流程：先合并模板，再用 BibTeX 过滤

同一个库用了多套模板处理？先把所有结果合并，再拿 BibTeX 做一次过滤。

```bash
# 1) 合并同一库的多个模板
uv run deepresearch-flow paper db merge templates \
  --inputs ./deep_read.json \
  --inputs ./simple.json \
  --output ./all.json

# 2) 用 BibTeX 过滤合并后的结果
uv run deepresearch-flow paper db extract \
  --input-bibtex ./library.bib \
  --json ./all.json \
  --output-json ./library_filtered.json \
  --output-csv ./library_filtered.csv
```

## 补充缺失的模板和翻译

处理大型文献库时，有些论文可能缺少某个模板（比如 deep read 结果），或者缺了翻译。下面两种方法可以帮你找出并补齐这些缺口 —— 可以从源 Markdown 重新提取，也可以直接在现有快照上原地补充。

### 方法一：定位缺口 → 提取 → 重建

```bash
# 1) 查看快照中缺少哪些模板
uv run deepresearch-flow paper db snapshot show-missing \
  --snapshot-db ./dist/paper_snapshot.db

# 2) 导出缺少指定模板的论文（附带文件路径，方便后续提取）
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type template \
  --template deep_read \
  --static-export-dir ./dist/paper-static \
  --output ./missing_deep_read.json \
  --txt-output ./missing_ids.txt \
  --output-paths ./extractable_paths.txt

# 3) 提取缺失的模板（仅针对有源 markdown 的论文）
uv run deepresearch-flow paper extract \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --input-list ./extractable_paths.txt \
  --output ./deep_read_supplement.json

# 4) 合并到已有的 paper_infos.json
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos.json \
  --inputs ./deep_read_supplement.json \
  --output ./paper_infos_complete.json

# 5) 用完整数据重建快照
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot_complete.db \
  --static-export-dir ./dist/paper-static-complete
```

### 方法二：原地补充，不重建

不想全量重建？直接在已有快照上更新。

```bash
# 原地补充已有论文的缺失模板
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --in-place

# 或者输出到新位置
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --output-db ./dist/paper_snapshot_supplemented.db \
  --output-static-dir ./dist/paper-static-supplemented
```

关于快照的构建和服务，详见[快照管理](snapshot-management.md)。

### 补充缺失的翻译

找出缺少翻译的论文，翻译后更新快照。

```bash
# 1) 导出缺少中文翻译的论文（附带文件路径）
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type translation \
  --lang zh \
  --static-export-dir ./dist/paper-static \
  --output-paths ./to_translate_paths.txt

# 2) 翻译缺失的论文
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --input-list ./to_translate_paths.txt \
  --output-dir ./docs_translated

# 3) 重建或补充快照，把新翻译加进去
uv run deepresearch-flow paper db snapshot build ...
# 如果只是加翻译，也可以用 snapshot supplement
```

翻译中断后重新运行同一条命令即可续译。每译完一个节点（Node），翻译结果就写入输出目录下的 `.translate_cache/<输出文件名>.json`；再次运行时，已缓存的节点不再请求模型。文档的译文写出后，这份缓存自动删除。缓存按「原文 + 源语言 + 目标语言」匹配，换语言不会误用。加 `--force` 会忽略缓存，整篇重译。

`paper db snapshot export-missing` 支持的导出类型：

| 类型 | 说明 |
|------|------|
| `--type source_md` | 缺少源 markdown 的论文 |
| `--type pdf` | 缺少 PDF 的论文 |
| `--type translation --lang zh` | 缺少中文翻译的论文 |

## 相关文档

- [部署指南](deployment.md) — 通过 HTTP 对外提供文献库浏览服务
- [快照管理](snapshot-management.md) – 构建、查询和维护论文快照
