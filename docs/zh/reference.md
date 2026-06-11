[← 返回 README](../README_ZH.md)

# 功能参考

## 1. Translator：OCR 安全翻译

翻译模块面向科学文档设计，采用节点式架构。

- **结构保护**：在将文本送入 LLM 之前，自动检测并"冻结"代码块、LaTeX（`$$...$$`）、HTML 表格和图片。
- **OCR 修复**：使用 `--fix-level` 合并断裂段落，将文本引用（`[1]`）转为可点击的 Markdown 脚注（`[^1]`）。可选级别：`off`、`light`、`moderate`（默认）、`aggressive`。
- **上下文感知**：失败的 chunk 支持重试，降级路径平滑。
- **多文档调度**：文档、重试、降级阶段各自跑在独立的 worker 队列中。
- **并发控制**：`--document-window`、`--initial-workers`、`--retry-workers`、`--main-concurrency` / `--retry-concurrency` / `--fallback-concurrency` / `--fallback-2-concurrency`。`--max-concurrency` 为可选的总上限。
- **配置默认值**：在 `config.toml` 的 `[translator_config]` 节中设置 `model` / `retry_model` / `fallback_model` / `fallback_model_2` 以及调度器默认参数。
- **向后兼容**：`--group-concurrency` 已废弃，映射为 `--initial-workers`。

```bash
uv run deepresearch-flow translator translate \
  --input ./papers \
  --target-lang ja \
  --fix-level aggressive \
  --document-window 8 \
  --initial-workers 4 \
  --retry-workers 2 \
  --main-concurrency 4 \
  --model claude/claude-3-5-sonnet-20240620
```

## 2. Paper Extract：结构化知识抽取

把散落的 Markdown 文件变成可查询的数据库。

- **模板**：内置 `simple`、`eight_questions`、`deep_read` 等 prompt 模板。
- **异步 + 限流**：通过 `--max-concurrency`、`--sleep-every`、`--timeout` 控制。
- **增量处理**：自动跳过已处理的文件。
- **阶段续跑**：多阶段模板会持久化各模块的输出；`--force-stage <name>` 可重跑指定模块。
- **阶段 DAG**：启用 `--stage-dag` 实现依赖感知的并行调度；`--dry-run` 打印逐阶段执行计划。
- **图表提示**：`deep_read` 可输出标注为 `[Inferred]` 的推断图表；如有需要可用 `recognize fix-mermaid` 修复。
- **阶段聚焦**：多阶段运行时高亮当前活跃模块，减少上下文过载。
- **范围过滤**：`--start-idx/--end-idx` 切片输入；在 `--retry-failed`/`--retry-failed-stages` 之前生效。
- **重试失败阶段**：`--retry-failed-stages` 仅重跑失败的阶段；缺失阶段会被强制执行。
- **模型路由**：`--model` 接受 `provider/model`、内联 JSON 池或 `@file`。回退到 `config.toml` 中的 `main_model`。

```bash
uv run deepresearch-flow paper extract \
  --input ./library \
  --output paper_data.json \
  --template-dir ./my-custom-prompts \
  --max-concurrency 10 --timeout 180

# 范围 + 重试
uv run deepresearch-flow paper extract \
  --input ./library \
  --start-idx 0 --end-idx 100 \
  --retry-failed \
  --model claude/claude-3-5-sonnet-20240620

# 重试多阶段模板中的失败阶段
uv run deepresearch-flow paper extract \
  --input ./library \
  --retry-failed-stages \
  --model claude/claude-3-5-sonnet-20240620
```

## 3. 数据库与界面：你的私人 ArXiv

`db serve` 命令在本地搭建一个研究工作站。

- **分栏视图**：左侧显示原始 PDF/Markdown，右侧显示摘要/翻译。
- **全文搜索**：`tag:fpga year:2023..2024`
- **统计图表**：可视化发表趋势和关键词频率。
- **PDF 查看器**：内置 PDF.js，避免跨域问题。

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --pdf-root ./pdfs \
  --cache-dir .cache/db
```

## 4. Paper DB Compare：覆盖率审计

对比两个数据集（A/B），找出缺失的 PDF、Markdown、翻译或 JSON 条目。

```bash
uv run deepresearch-flow paper db compare \
  --input-a ./a.json \
  --md-root-b ./md_root \
  --output-csv ./compare.csv

# 按语言对比已翻译的 Markdown
uv run deepresearch-flow paper db compare \
  --md-translated-root-a ./translated_a \
  --md-translated-root-b ./translated_b \
  --lang zh
```

## 5. Paper DB Extract：匹配导出

在覆盖率对比之后，导出匹配的 JSON 条目或已翻译的 Markdown。

```bash
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-bibtex ./refs.bib \
  --pdf-root ./pdfs \
  --output-json ./matched.json --output-csv ./extract.csv

# 使用 JSON 引用列表过滤目标 JSON
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-json ./reference.json \
  --pdf-root ./pdfs \
  --output-json ./matched.json --output-csv ./extract.csv

# 按语言提取已翻译的 Markdown
uv run deepresearch-flow paper db extract \
  --md-root ./md_root \
  --md-translated-root ./translated \
  --lang zh \
  --output-md-translated-root ./translated_matched \
  --output-csv ./extract.csv
```

## 6. Recognize：OCR 后处理

用于清理 MinerU 等 OCR 引擎的原始输出。

- **Embed Images**：将本地图片链接转为 Base64，生成可移植的单文件 Markdown。
- **Extract Images**：将 Base64 图片还原为本地文件。
- **Organize**：展平嵌套的 OCR 输出目录结构。
- **Fix**：应用 OCR 修复和 rumdl 格式化。
- **Fix JSON**：对 paper JSON 中的 Markdown 字段执行同样的修复。
- **Fix Math**：校验并修复 LaTeX 公式，可选 LLM 辅助。
- **Fix Mermaid**：校验并修复 Mermaid 图表（需要 mermaid-cli 提供的 `mmdc`）。
- **推荐顺序**：`fix` → `fix-math` → `fix-mermaid` → `fix`。

```bash
uv run deepresearch-flow recognize md embed --input ./raw_ocr --output ./clean_md
```

```bash
# 整理 MinerU 输出并应用 OCR 修复
uv run deepresearch-flow recognize organize \
  --input ./mineru_outputs \
  --output-simple ./ocr_md --fix

# 修复并格式化已有 Markdown
uv run deepresearch-flow recognize fix \
  --input ./ocr_md --output ./ocr_md_fixed

# 原地修复（也支持 --json 标志）
uv run deepresearch-flow recognize fix \
  --input ./ocr_md --in-place

# 修复 LaTeX 公式
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --in-place

# 修复 JSON 输出中的 Mermaid 图表
uv run deepresearch-flow recognize fix-mermaid \
  --json --input ./paper_outputs \
  --model openai/gpt-4o-mini --in-place

# 仅重试失败的公式/图表
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model claude/claude-3-5-sonnet-20240620 \
  --report ./fix-math-errors.json --retry-failed
```

## 配置参考

config.toml 支持：
- 多 provider（OpenAI、Claude、Gemini、DashScope、Ollama、Azure OpenAI）
- 通过 `main_model` 实现加权模型路由，通过 `providers[].base[]` 实现 URL 路由，通过 `providers[].base[].key[]` 实现 key 路由
- 运行时路由池：每次请求按 `model -> base -> key` 选择
- `--model` 接受单个 `provider/model`、内联 JSON 池或 `@file` JSON 池
- `env:VAR_NAME` 用于安全的 key 注入

模型路由示例：

```bash
# config.toml 中的 main_model
uv run deepresearch-flow paper extract --input ./docs

# 固定模型
uv run deepresearch-flow paper extract --input ./docs --model openai/gpt-4o-mini

# 内联加权池
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model '[{"model":"openai/gpt-4o-mini","weight":4},{"model":"claude/claude-sonnet-4-5-20250929","weight":1}]'

# 文件池
uv run deepresearch-flow paper extract \
  --input ./docs --model @main_model.json
```

模式探测：

```bash
uv run deepresearch-flow utils test-mode \
  --config ./config.toml --model openai/gpt-4o-mini

# 将探测结果写回 config
uv run deepresearch-flow utils test-mode \
  --config ./config.toml --model openai/gpt-4o-mini --write-back
```
