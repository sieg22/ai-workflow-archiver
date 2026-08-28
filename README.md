# AI Workflow Archiver v1.5.1

A local backup and migration utility for Figma Weave workflows.

> Unofficial community project. Not affiliated with or endorsed by Figma.

## What it saves

- Workflow nodes and connections
- Prompts and generation history
- Input and reference media
- Generated outputs
- Local HTML report
- Integrity metadata

The goal is to preserve a workflow locally so it can remain usable even after the source account is no longer available.

## Requirements

- Python 3.10+
- Windows or macOS
- No third-party Python packages

## Windows

1. Double-click `BACKUP_WEAVY.bat`.
2. Enter an optional author name.
3. Enter a project name.
4. In Figma Weave, select the workflow nodes and press `Ctrl+C`.
5. The script detects the clipboard automatically.
6. Do not paste the workflow into the terminal.
7. The archive is created and the HTML report opens automatically.

## macOS

### Recommended method

Open Terminal in the project folder and run:

```bash
python3 archive_weavy.py --interactive
```

Then:

1. Enter an optional author name.
2. Enter a project name.
3. In Figma Weave, select the workflow nodes and press `Cmd+C`.
4. The script detects the clipboard automatically.
5. Do not paste the workflow into Terminal.
6. The archive is created and the HTML report opens automatically.

### Optional launcher

`BACKUP_WEAVY.command` is included as a convenience launcher.

macOS may block downloaded `.command` files with Gatekeeper because the file is not Apple-signed or notarized.

If that happens, either use the recommended Terminal command above, or allow the launcher manually in:

`System Settings → Privacy & Security → Open Anyway`

Do not disable Gatekeeper system-wide.

If the launcher only lacks executable permission, run:

```bash
chmod +x BACKUP_WEAVY.command
```

## Shortcuts while waiting

- `R` — rename the project
- `M` — enter Multi-chunk mode

## Multi-chunk mode

Use Multi-chunk mode for very large workflows that are difficult to copy in one selection.

Copy several overlapping areas of the same workflow. Duplicate nodes, edges, and generations are detected and merged automatically.

Shortcuts:

- `D` — finish collection and create the archive
- `R` — rename the project
- `X` — cancel the current project

Overlapping chunks are recommended because they help preserve cross-chunk connections.

## Output structure

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

## HTML report

The report includes:

- Archive status
- Generated outputs with newest/oldest sorting
- Canvas reconstruction
- Output timeline
- Inputs
- Prompt history
- Models

Do not remove the source account until the report shows that the archive is fully self-contained.

## Privacy

Generated project folders may contain private prompts, media, URLs, and workflow metadata.

Do not commit generated backup folders to a public repository.

## Files to publish on GitHub

```text
archive_weavy.py
BACKUP_WEAVY.bat
BACKUP_WEAVY.command
README.md
.gitignore
```

Do not publish generated project archives, media, metadata, test files, or local logs.

## License

No license file is included. Add an open-source license appropriate for your repository before publishing.
