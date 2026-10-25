# Snapshot 管理

[← 返回 README](../../README_ZH.md)

## 构建生产 Snapshot

[从 PDF 到快照](pipeline.md) 的流程已生成并修复摘要 JSON 后，构建一个可用于生产环境的 snapshot
（SQLite + 静态资源）。此命令读取这些 JSON 包；不会从 PDF 或 BibTeX 直接生成它们。

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdf \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static
```

说明：

- 每增加一份已修复的摘要模板，就增加一个
  `--input ./<template>.json`。
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

## 修复已上线的元数据（只传差异）

适用场景：修复了 build 逻辑（例如 BibTeX 匹配、标题和作者的来源），需要让已上线的数据跟着更新，又不想重新上传整份静态导出。

OCR、翻译和抽取结果不用重跑。在本地重新 build，然后只上传静态文件里的差异部分，最后用 `api update` 改写线上的数据库。

这个方法成立，是因为静态文件的命名规则：

- `pdf/`、`images/`、`md/`、`md_translate/` 按内容哈希命名。文件名相同，就可以直接沿用服务器上的旧文件。PDF 在导出后另行压缩过也没关系。
- `summary/` 和 `manifest/` 按 `paper_id` 命名，内容里记录了标题等元数据。同名文件也可能有变化，需要比对内容。

在一份约 3 GB 的导出上，差异包约 2.4 MB。

下面的命令假设项目目录是 `./myproj`，布局与 [从 PDF 到快照](pipeline.md) 相同，已上线的数据库和导出目录是 `myproj/dist/myproj.db` 与 `myproj/dist/myproj-export/`。

### 1. 部署新后端

先部署新后端，再执行后面的步骤。旧后端在 `api update` 时会把 BibTeX 写成没有字段的空壳。

### 2. 本地重新 build

输入沿用原来的 JSON、BibTeX 和各个 root，新 build 输出到 `rebuild/` 目录。`--previous-snapshot-db` 指向已上线的那份数据库，用来保持 `paper_id` 不变。

```bash
P=./myproj
uv run deepresearch-flow paper db snapshot build \
  -i $P/simple.json -i $P/deep_read.json -b $P/myproj.bib \
  --md-root $P/md_simple --md-translated-root $P/md_base64_translated --pdf-root $P/pdf \
  --output-db rebuild/myproj.db --static-export-dir rebuild/static \
  --previous-snapshot-db $P/dist/myproj.db
```

### 3. 打差异包

`OLD` 是已上传的导出目录，它的内容必须和服务器一致。`NEW` 是新 build 的静态目录。差异包 `delta.tar.gz` 和文件清单 `delta.list` 写在 `rebuild/` 下。

```bash
R=$(realpath rebuild)
OLD=$(realpath $P/dist/myproj-export)
NEW=$R/static
cd "$NEW"
find . -type f | while read -r f; do
  if [ ! -e "$OLD/$f" ]; then echo "$f"                                    # 新文件
  elif [[ $f == *.json ]] && ! cmp -s "$f" "$OLD/$f"; then echo "$f"; fi   # 内容变化的 summary/manifest
done > "$R/delta.list"
cut -d/ -f2 "$R/delta.list" | sort | uniq -c                              # 按目录统计，核对数量
tar -C "$NEW" -czf "$R/delta.tar.gz" -T "$R/delta.list"
```

### 4. 在服务器上解压

进入静态目录，覆盖解压：

```bash
tar -xzf delta.tar.gz
```

不需要删除旧文件，数据库已经不再引用它们。

### 5. 改写线上元数据

先用 `--dry-run` 查看将要更新的篇数，再正式执行：

```bash
uv run deepresearch-flow paper db api update \
  --snapshot-db rebuild/myproj.db \
  --static-export-dir rebuild/static \
  --config remote.toml --dry-run
```

`api update` 只改写线上已有论文的元数据，包括标题、作者、BibTeX、facet 和搜索索引，不碰静态文件和向量。

### 6. 处理 `paper_id` 变化的论文

有的论文以前没有匹配上 BibTeX，这次匹配上了，它的 `paper_id` 会变。`api update` 会跳过这些论文，因为线上还没有它们。用下面的命令找出它们：

```bash
OLD_DB=$P/dist/myproj.db NEW_DB=rebuild/myproj.db python3 - <<'PY'
import os, sqlite3
old = {r[0] for r in sqlite3.connect(os.environ["OLD_DB"]).execute("select paper_id from paper")}
new = {r[0] for r in sqlite3.connect(os.environ["NEW_DB"]).execute("select paper_id from paper")}
print("新增:", sorted(new - old)); print("待删:", sorted(old - new))
PY
```

新增的论文用 `api push` 推送。待删的论文对每个 id 调用 admin API 的 `DELETE /papers/{paper_id}`。

### 注意

- 差异的判断以本地 `OLD` 目录为准。服务器上的文件如果单独改过、没有同步回本地，判断就不准确。
- 每个数据集分别按上面的流程处理。
