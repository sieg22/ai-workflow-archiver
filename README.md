# AI Workflow Archiver v1.5.4

A local backup and migration utility for Figma Weave workflows.

> Unofficial community project. Not affiliated with or endorsed by Figma.

## What it saves

- Workflow nodes and connections
- Prompts and generation history
- Input and reference media
- Generated outputs
- Local HTML report
- Integrity metadata

## Requirements

- Python 3.10+
- Windows or macOS
- No third-party Python packages

## Windows

1. Double-click `BACKUP_WEAVY.bat`.
2. Enter an optional author name and a project name.
3. In Figma Weave, select the workflow nodes and press `Ctrl+C`.
4. The script detects the clipboard automatically.
5. Do not paste the workflow into the terminal.
6. The archive is created and the HTML report opens automatically.

## macOS

Recommended method:

```bash
python3 archive_weavy.py --interactive
```

Then enter an optional author name and project name, select the workflow in Figma Weave, and press `Cmd+C`.

`BACKUP_WEAVY.command` is also included as an optional convenience launcher. macOS Gatekeeper may block downloaded `.command` files because they are not Apple-signed or notarized. If that happens, use the Terminal command above, or allow the launcher once in:

`System Settings → Privacy & Security → Open Anyway`

Do not disable Gatekeeper system-wide.

## Shortcuts while waiting

- `R` — rename the project
- `M` — enter Multi-chunk mode

## Clipboard detection

After `Ctrl+C` or `Cmd+C`, the script now keeps the clipboard event pending until the copied workflow can actually be read.

For large workflows you may see:

```text
Clipboard activity detected.
Waiting for Figma Weave to finish preparing the copied workflow...
Workflow received.
```

`Clipboard activity detected` means the operating system has registered the copy action.

`Workflow received` means the complete workflow payload has been read successfully.

Large workflows may still take longer for Figma Weave to prepare and for the script to parse, but a temporarily locked or delayed clipboard should no longer cause the copy event to be silently missed.

## Multi-chunk mode

Use Multi-chunk mode for very large workflows. Copy several overlapping selections; duplicate nodes, edges, and generations are detected and merged automatically.

- `D` — finish and create the archive
- `R` — rename
- `X` — cancel

Overlapping chunks are recommended because they help preserve cross-chunk connections.

Repeated `Ctrl+C` / `Cmd+C` copies of the same selection are collapsed automatically. They do not create additional chunk numbers or duplicate workflow data. A compact status line shows that the repeated copy was ignored while the script continues waiting for a different chunk.

The same delayed-clipboard retry logic is used for every chunk.
## Updating an existing archive

If a folder with the same project name already exists, the script asks what to do after the new workflow has been copied and parsed:

- `U` — update the existing archive
- `N` — create a new numbered copy such as `PROJECT_A_2`
- `R` — rename the project without copying the workflow again
- `C` — cancel

Update mode rebuilds the report and metadata in the same folder. Existing media is reused by URL whenever possible. If a deterministic filename changed, the local file is moved to the new path rather than downloaded again.

Only obsolete media files recorded in the previous `asset_manifest.json` are removed. Untracked user files are never deleted.

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
