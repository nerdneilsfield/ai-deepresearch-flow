## 1. Implementation
- [x] 1.1 Add `--start-idx` (default 0) and `--end-idx` (default -1) CLI flags for extract
- [x] 1.2 Apply range slicing immediately after discover (start inclusive, end exclusive; `-1` means end)
    - [x] Handle `end_idx = -1` by setting slice end to `None` (include last element)
    - [x] Log the number of files before and after slicing
    - [x] Warn if range yields zero files
- [x] 1.3 Apply retry-failed filtering after range slicing
- [x] 1.4 Update README and README_ZH with examples showing interaction with `--retry-failed`
