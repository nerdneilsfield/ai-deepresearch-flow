# Snapshot 管理

[← 返回 README](../README_ZH.md)

## 构建生产 Snapshot

构建一个可用于生产环境的 snapshot（SQLite + 静态资源）：

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static
```

说明：

- 构建主机需要能够读取原始的 PDF/Markdown 目录。
- CDN 服务器只需要导出后的目录（比如 `/data/paper-static`）。
- `--output-embed-db` 可以在同一次构建中生成 LanceDB 索引。

## 补充缺失的模板

如果已有的论文缺少某些模板（例如 `deep_read`），可以跳过重建，直接补充：

```bash
# 原地补充
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

说明：

- `--md-root` 和 `--md-translated-root` 是可选的——仅在需要从本地目录解析 markdown 时才需要。
- 也接受 `--bibtex` 和 `--pdf-root`（均为可选）。

## 补充缺失的翻译

导出缺少翻译的论文，完成翻译后重新构建或补充：

```bash
# 1) 导出缺少中文翻译的论文
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type translation --lang zh \
  --static-export-dir ./dist/paper-static \
  --output-paths ./to_translate_paths.txt

# 2) 翻译
uv run deepresearch-flow translator translate \
  --input ./docs --target-lang zh \
  --model openai/gpt-4o-mini \
  --input-list ./to_translate_paths.txt \
  --output-dir ./docs_translated

# 3) 重新构建或补充 snapshot
uv run deepresearch-flow paper db snapshot build ...
```

常用的导出类型：`--type source_md`、`--type pdf`、`--type translation --lang zh`

## 添加新论文（Update）

如果需要向 snapshot 中添加新论文：

```bash
# 原地添加新论文
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs_translated \
  --pdf-root ./pdfs \
  --in-place

# 或者输出到新位置
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --output-db ./dist/paper_snapshot_updated.db \
  --output-static-dir ./dist/paper-static-updated
```

### supplement 与 update 的区别

| 命令 | 作用范围 | 行为 |
|---------|-------|----------|
| **supplement** | 仅限于已有论文 | 为 snapshot 中已存在的论文补充缺失的模板/翻译 |
| **update** | 仅限于新论文 | 添加 snapshot 中尚不存在的论文 |

## Snapshot 迁移（Legacy → DOI/BibTeX）

### 推荐方式：原地迁移 Schema（不丢失数据）

如果你的 snapshot 是在 DOI/BibTeX 支持之前构建的：

```bash
# 原地迁移，附带带时间戳的备份
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --in-place

# 或者复制到新位置
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --output-db ./dist/paper_snapshot_v2.db

# 仅迁移 Schema（不填充 BibTeX 数据）
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --in-place
```

migrate 命令会依次执行以下步骤：

1. 创建带时间戳的备份（除非指定 `--no-backup`）
2. 向 `paper` 表中添加 `doi` 列（如果不存在）
3. 创建 `paper_bibtex` 表（如果不存在）
4. 将论文与 BibTeX 条目匹配，填入 DOI/BibTeX 数据
5. 更新静态导出索引的元数据

特性：

- **不丢失数据**：通过 `ALTER TABLE` 升级 schema
- **带时间戳的备份**：采用 `.bak_YYYYMMDD_HHMMSS` 格式
- **BibTeX 数据补充**：将论文与 BibTeX 匹配，提取 DOI 元数据
- **更新静态导出**：同步更新 `paper_index.json`

### 替代方案：基于旧 Snapshot 重建

如果需要从头重建但保持身份连续性：

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --output-db ./dist/paper_snapshot_v2.db \
  --static-export-dir ./dist/paper-static-v2 \
  --previous-snapshot-db ./dist/paper_snapshot.db
```

说明：

- `--md-root`、`--md-translated-root`、`--pdf-root` 是可选的。
- 当前输入中带有 DOI/BibTeX 的数据优先；否则从 `--previous-snapshot-db` 继承。
- **注意**：只会包含输入 JSON 中的论文——请确保所有论文都已涵盖。
