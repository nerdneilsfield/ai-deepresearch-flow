## ADDED Requirements

### Requirement: Recognize CLI command group
The system SHALL provide a top-level `deepresearch-flow recognize` command group.

#### Scenario: Recognize help lists subcommands
- **WHEN** the user runs `deepresearch-flow recognize --help`
- **THEN** the help output lists the `md` and `organize` subcommands

### Requirement: Markdown input discovery for recognize md commands
The system SHALL accept one or more `--input/-i` paths for `recognize md` commands.
When a directory is provided, the system SHALL process only the direct `*.md` files unless `--recursive/-r` is set.
When `--recursive/-r` is set, the system SHALL recursively discover `*.md` files under the directory.
The system SHALL write outputs into a single flat output directory, resolving name collisions by appending numeric suffixes (e.g., `_1`, `_2`).

#### Scenario: Recursive markdown discovery
- **WHEN** the user runs `deepresearch-flow recognize md embed -i ./docs -r --output ./out`
- **THEN** all `*.md` files under `./docs` are processed

#### Scenario: Flattened output naming with collision suffixes
- **WHEN** two input markdown files share the same base name
- **THEN** the output files are written to the output directory with `_1`, `_2` suffixes to avoid overwrite

### Requirement: Markdown image embedding
The system SHALL replace Markdown image links (`![](...)`) that point to local files with `data:image/...;base64,` URLs.
The system SHALL preserve existing `data:` image URLs.
The system SHALL only fetch and embed HTTP(S) image URLs when `--enable-http` is provided.
When HTTP(S) fetching is enabled, the system SHALL use a modern browser `User-Agent` string and apply a bounded request timeout (default: 60 seconds).
When an HTTP(S) image fails to fetch, the system SHALL log a warning and keep the original link unchanged.
The system SHALL support common image types including png, jpg, jpeg, webp, gif, and svg.

#### Scenario: Embed local image link
- **WHEN** a markdown image points to a local file
- **THEN** the link is replaced with a `data:` URL containing the base64-encoded content

#### Scenario: Preserve existing data URL
- **WHEN** a markdown image already uses a `data:` URL
- **THEN** the link is left unchanged

#### Scenario: Skip HTTP images without enable flag
- **WHEN** a markdown image points to an HTTP(S) URL and `--enable-http` is not provided
- **THEN** the link is left unchanged

#### Scenario: HTTP fetch failure keeps original link
- **WHEN** a markdown image points to an HTTP(S) URL and the download fails
- **THEN** the tool logs a warning and leaves the original link unchanged

### Requirement: Markdown image unpacking
The system SHALL extract `data:image/...;base64,` URLs into image files under an output `images/` directory and update markdown links to the saved files.
The system SHALL derive the output image extension from the MIME type in the `data:` URL.
When a filename can be inferred from the image alt text, the system SHALL use that name as the base filename; otherwise, it SHALL use a hash-derived filename.

#### Scenario: Unpack embedded image to file
- **WHEN** a markdown image uses a `data:` URL
- **THEN** the image is written to `images/` and the link is updated to the relative file path

#### Scenario: MIME-derived extension overrides alt text extension
- **WHEN** a markdown image uses `data:image/png;base64,...` with an alt text ending in `.jpg`
- **THEN** the extracted file uses the `.png` extension

### Requirement: Organize OCR output layout (mineru)
The system SHALL provide a `recognize organize` command that supports a `mineru` layout.
The command SHALL accept one or more `--input/-i` directories and an optional `--recursive/-r` flag to discover layout folders containing `full.md` and `images/`.
The command SHALL write flat markdown outputs named after the layout directory (including UUID) and SHALL copy referenced images to a shared `images/` directory in the output.
The command SHALL resolve image filename collisions by appending numeric suffixes and update markdown references accordingly.
The command SHALL support `--output-simple` (copy markdown + images) and `--output-base64` (embed images) with at least one required.
The command SHALL ignore non-image asset directories (for example, `tables/` or `latex/`).

#### Scenario: Organize simple output with image copying
- **WHEN** the user runs `deepresearch-flow recognize organize --layout mineru -i ./results --output-simple ./out`
- **THEN** each `full.md` is copied to `./out` with the parent directory name and image references updated to `./out/images`

#### Scenario: Organize base64 output
- **WHEN** the user runs `deepresearch-flow recognize organize --layout mineru -i ./results --output-base64 ./out`
- **THEN** each `full.md` is written to `./out` with images embedded as `data:` URLs

### Requirement: Recognize concurrency controls
The system SHALL support a `--workers` option for `recognize md embed`, `recognize md unpack`, and `recognize organize` to process multiple files concurrently.
The system SHALL default to 4 workers when `--workers` is not provided.

#### Scenario: Process with worker concurrency
- **WHEN** the user runs `deepresearch-flow recognize md embed --workers 8 -i ./docs --output ./out`
- **THEN** up to 8 markdown files are processed concurrently
