# AI Workflow Archiver v1.5.0

Local backup and migration utility for Figma Weave workflows.

> Unofficial community tool. Not affiliated with or endorsed by Figma.

## What it saves

- Workflow nodes and connections
- Prompts and generation history
- Input/reference media
- Generated outputs
- Local HTML report
- Integrity metadata

Media is downloaded locally so an archive can remain usable after the source account is no longer available.

## Requirements

- Python 3.10+
- Windows or macOS
- No third-party Python packages

## Quick start

### Windows

1. Double-click `BACKUP_WEAVY.bat`.
2. Enter an optional author name.
3. Enter a project name.
4. In Figma Weave, select the workflow nodes and press `Ctrl+C`.
5. The script detects the clipboard automatically. Do not paste into the terminal.
6. The archive is created and the HTML report opens.

### macOS

1. Double-click `BACKUP_WEAVY.command`.
2. Enter an optional author name.
3. Enter a project name.
4. In Figma Weave, select the workflow nodes and press `Cmd+C`.
5. The script detects the clipboard automatically. Do not paste into Terminal.
6. The archive is created and the HTML report opens.

If macOS refuses to launch the `.command` file, run once in Terminal:

```bash
chmod +x BACKUP_WEAVY.command
```

Then open it again.

## Shortcuts while waiting

- `R` — rename project
- `M` — Multi-chunk mode

Multi-chunk mode is useful for very large pages. Copy several **overlapping** selections; duplicate nodes, edges, and generations are merged automatically.

Multi-chunk shortcuts:

- `D` — finish and archive
- `R` — rename
- `X` — cancel

## Output

```text
PROJECT_A/
├── media/
│   ├── input/
│   └── output/
├── metadata/
│   ├── clipboard_original.txt
│   ├── workflow_original.json
│   ├── workflow_normalized.json
│   ├── asset_manifest.json
│   ├── download_results.json
│   └── manifest_sha256.json
└── PROJECT_A_report.html
```

Generated outputs are grouped by date and type:

```text
media/output/YYYY-MM-DD/
├── video/
├── image/
├── upscale/
├── audio/
└── other/
```

## Report

The HTML report includes:

- Archive status
- Generated outputs with newest/oldest sorting
- Canvas reconstruction
- Output timeline
- Inputs
- Prompt history
- Models

Do not remove the source account until the report shows the archive is fully self-contained.

## Privacy

Generated project folders may contain prompts, media, URLs, and workflow metadata.

Do not commit project backup folders to a public repository.

## Repository files

Commit only:

```text
archive_weavy.py
BACKUP_WEAVY.bat
BACKUP_WEAVY.command
README.md
.gitignore
```

## License

No license file is included. Add an open-source license appropriate for your repository before publishing.
