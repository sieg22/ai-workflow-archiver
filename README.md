# Weavy / Figma Weave Workflow Archiver v1.4.4

A small Windows utility for backing up Weavy / Figma Weave workflows from the clipboard, including workflow structure, prompts, generation history, inputs, outputs, and local media copies.

> Unofficial community utility. Not affiliated with or endorsed by Figma.

## Requirements

- Windows
- Python 3.10+
- Chrome/your browser with the workflow open

No third-party Python packages are required.

## Quick start

1. Download or clone this repository.
2. Double-click `BACKUP_WEAVY.bat`.
3. Enter an optional author name.
4. Enter a project name, for example `PROJECT_A`.
5. The terminal will say it is waiting for clipboard content.
6. In Weavy / Figma Weave, select the workflow nodes and press `Ctrl+C`.
7. The script automatically detects the copied workflow. Do **not** paste into the terminal and do **not** press Enter again.
8. When finished, the HTML report opens automatically.

For large workflows, clipboard creation and parsing can take longer.

## Shortcuts while waiting

- `R` — rename the project
- `M` — enter Multi-chunk mode

Multi-chunk mode is useful when a large page cannot be copied at once. Copy several **overlapping** selections; duplicates are automatically merged.

In Multi-chunk mode:

- `D` — finish and archive
- `R` — rename
- `X` — cancel

## Output

Each project is saved beside the script:

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

Generated outputs are grouped by date and type, for example:

```text
media/output/
└── YYYY-MM-DD/
    ├── video/
    ├── image/
    ├── upscale/
    ├── audio/
    └── other/
```

## HTML report

The report includes:

- Overview and archive status
- Generated outputs with newest/oldest sorting
- Canvas reconstruction
- Output timeline
- Inputs
- Prompt history
- Models

Media is local-first. If a local file is missing, the report can fall back to the original remote URL while it still exists.

Do not close/delete the source account until the report shows the archive is fully self-contained.

## Git / privacy

Commit only the tool source files. Do not commit generated project backups, media, prompts, or metadata.

The included `.gitignore` excludes common generated archive content.

## Optional CLI

Single file:

```bat
python archive_weavy.py workflow.json --name "PROJECT_A"
```

Custom output folder:

```bat
python archive_weavy.py --interactive --out "C:\PATH\TO\BACKUPS"
```

Create an additional ZIP:

```bat
python archive_weavy.py --interactive --zip
```

## License

No license file is included. Add the license that is appropriate for your repository before publishing as open source.
