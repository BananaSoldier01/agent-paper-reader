<div align="center">

# Agent Paper Reader
### Agent 文献译读

**Read the document, its translation, and contextual explanations in one place.**

[简体中文](README.md) · English

[Demo](#see-it-in-action) · [Get started](#get-started) · [Example](examples/reading-showcase.html) · [Local library guide](references/library.md)

</div>

![Offline bilingual reader with an outline, aligned paragraphs, and contextual glossary](docs/media/bilingual-reader.png)

A **Skill** that turns an English document or article (text PDF, Markdown, plain text, local HTML, Word `.docx`, or single-file LaTeX `.tex`) into a self-contained bilingual HTML reader. Your current Agent reads the context, translates, organizes terminology, and reviews the result. **No local server is required by default, and no separate translation API key is needed.**

The current source supports **English → Simplified Chinese**. An optional local library adds multi-document management, saved annotations, and translation revisions. Skill instructions and reference guides are currently in Chinese.

## What can I read?

Research papers, reports, technical documentation, industry analysis, and ordinary English articles use the same reading workflow. Preserve the source structure; an article does not need an academic format.

All six formats use the same structure, translation, semantic alignment, second review, and HTML export workflow:

| Original document | File type | Scope and limits |
| --- | --- | --- |
| Text-based PDF | `.pdf` | Extracts text and keeps page images for verification; scanned pages are not OCRed. |
| Markdown | `.md` / `.markdown` | UTF-8 text with headings, lists, tables, and code; provide local images alongside the document. |
| Plain text | `.txt` | UTF-8 text, with blank lines separating candidate paragraphs. |
| Local web archive | `.html` / `.htm` | Parses text structure and copies relative images from the document's directory; does not fetch URLs or remote images. The Agent checks navigation and footer content during structure review. |
| Word | `.docx` | Ordinary body text, headings, tables, common equations, superscripts/subscripts, and embedded images. Complex headers/footers, text boxes, tracked changes, and general OLE objects have limited support. Some EMF images need the optional dependencies below. |
| Single-file LaTeX | `.tex` | Main-file text, headings, equations, and code; no `\input` / `\include` expansion, compilation, or external `.bib` / `.bbl` loading. Missing `\includegraphics` images remain issues requiring verification and completion. |

Save web articles as local files before giving them to the Agent. This version does not run OCR or accept EPUB or other language directions. Extracted content still requires source-based structure review and translation review by the Agent.

## See it in action

### Follow a sentence across both languages

Hover to highlight the corresponding content; click to keep it selected, then click again to clear it. Paragraph actions stay inside the “…” menu.

![Recorded interaction: hover linking, selection, and deselection](docs/media/sentence-linking.gif)

**[Watch the 42-second demo · MP4](docs/media/reader-demo.mp4)**: sentence linking → contextual term → original PDF → second paper → review notes → translation editor. Download the video if GitHub does not play it inline.

### Context for terms. A way back to the source.

| Contextual glossary | Original PDF page |
| --- | --- |
| ![MPIML definition from the paragraph tools](docs/media/context-glossary.png) | ![Original page shown within the reader](docs/media/source-location.png) |
| Inspect terms relevant to the paragraph. | Check the original layout, figures, and wording. |

### Keep notes when you need them

The optional local library separates glossary and notes into tabs and preserves translation revision history. This capture shows the second paper and an existing review note.

![HarnessProvisioning paper with a review note in the local library](docs/media/review-notes.png)

<details>
<summary>View the translation editor</summary>

![Translation editor with source text and revision history](docs/media/translation-editor.png)

</details>

These are actual browser captures from two processed papers. See [media provenance](docs/media/README.md). Their full PDFs and translations are not included in this repository.

## How it works

1. **Give your Agent a document** and a separate workspace directory.
2. **Understand and translate**: inspect extracted text and original pages, organize structure and terms, translate, and align semantic units.
3. **Review and validate**: the Agent performs a second review; deterministic scripts check coverage, versions, and data completeness. Work can resume after interruption.
4. **Export and read**: open the standalone HTML. Start the optional local library only when you need to save notes or revisions.

The Skill defines the workflow; the Agent provides understanding and translation; scripts process and validate data; HTML provides the reader. Importing alone does **not** invoke a model. Translation uses the host Agent's session and usage allowance.

## Get started

### 1. Send the link to your Agent and ask it to install

Copy this prompt into your current Agent, which must have local file access and command execution capabilities:

```text
Please install this Skill for me:
https://github.com/BananaSoldier01/agent-paper-reader

Read the repository README and SKILL.md first, then install the complete
Skill from the current main branch using a method supported by this Agent.
For multiple input formats, do not install the old v0.1.0 package.
Confirm the appropriate installation directory and check prerequisites,
including Python 3.12+. Preserve existing configuration and document data.
When finished, tell me where it is installed, whether it is ready to use,
and how to start processing an article.
```

Your Agent can fetch the repository or release package and check the installation for your environment. **You do not need to download, extract, or locate folders manually first.** Installation methods and permissions vary by host; rely on actual checks to confirm success.

Requires **Python 3.12+**. Initial setup downloads isolated dependencies. Normal use needs neither Node nor a frontend build. Processing documents also requires the Agent to inspect pages.

<details>
<summary>Optional environment for some embedded Word EMF images</summary>

Automatically displaying these vector images also requires:

- `rsvg-convert` or Inkscape on `PATH`. The Linux package is `librsvg2-bin`; macOS can use Homebrew's `librsvg`.
- `fontconfig` / `fc-match` matches for Times New Roman or a compatible family, and OpenSymbol. Linux packages include `fonts-liberation` or `fonts-croscore`, and `fonts-opensymbol`. An arbitrary fallback font does not prove the required coverage.

Run `doctor` and inspect `emf_preview.ready`, the matched font families, and `missing`. Python `setup` does not install these system tools or fonts. Missing dependencies, failed conversion, or unreliable output leave the original EMF and issue record intact; the image must not be reported as restored. These extra components are only needed for the relevant Word images, not ordinary inputs such as PDF.

</details>

<details>
<summary>Manual installation and directory layout (optional)</summary>

For the multiple input formats listed here, download the [main source ZIP](https://github.com/BananaSoldier01/agent-paper-reader/archive/refs/heads/main.zip), extract it, and place the complete source directory in your Agent's Skill directory under the name `agent-paper-reader`.

The [v0.1.0 compact package](https://github.com/BananaSoldier01/agent-paper-reader/releases/download/v0.1.0/agent-paper-reader.zip) is an older version with PDF / Markdown inputs only. Updating main does not change that release asset.

This repository is also a complete Skill, with [SKILL.md](SKILL.md) as its entry point. You can install the full repository contents; **do not copy only SKILL.md**. For a project supporting `.agents/skills`:

```text
my-project/
└── .agents/skills/agent-paper-reader/
    ├── SKILL.md
    ├── scripts/
    ├── references/
    ├── assets/
    └── …
```

Developers can run `python3 dev/package_skill.py` to generate `dist/agent-paper-reader.zip`. Discovery paths vary by host; if necessary, explicitly ask the Agent to read the installed `SKILL.md`.

</details>

### 2. Ask your Agent

```text
Use the agent-paper-reader Skill to turn /absolute/path/paper.pdf
into an offline English–Simplified Chinese reading HTML.

Use /absolute/path/paper-reader-workspace as the workspace.
Organize the full document and its key terms, translate and align it,
and complete a second review. Do not start a server by default.
Return the HTML file and any processing limitations.
```

Replace the path with your document; `.md`, `.txt`, `.html`, `.docx`, and `.tex` use the same instructions and workflow. Keep the workspace **outside the Skill installation directory**. It stores originals, progress, and reading data, so updating the Skill does not require translating again.

### 3. Open the HTML

The export includes the resources required for reading. To try a ready-made result, download the repository and open:

- **[Full feature example](examples/reading-showcase.html)**: original fictional teaching material with four outline levels, 8 terms, 3 notes, revision history, equations, and a diagram.
- **[PDF source-location sample](examples/cooling-study.html)** with its [source PDF](examples/cooling-study.pdf).

GitHub's file preview does not run these HTML files; download and open them in a browser.

## Offline HTML or optional local library?

| Capability | Offline HTML | Local library |
| --- | --- | --- |
| Bilingual text, sentence linking, outline, search | ✓ | ✓ |
| Contextual glossary and source lookup | ✓ | ✓ |
| View exported notes and revisions | ✓ | ✓ |
| Save new highlights, notes, and questions | — | ✓ |
| Edit translations and retain revision history | — | ✓ |
| Manage multiple documents and resume work | — | ✓ |

HTML is a **read-only snapshot**. Retain the workspace to continue editing; importing an HTML snapshot back into the library is not supported. Translation edits require review of the current version before re-export.

The [local library guide](references/library.md) covers startup, existing documents, annotations, revisions, recovery, and conflicts.

<details>
<summary>Command-line entry points and workspace layout</summary>

```sh
python3 /path/to/agent-paper-reader/scripts/paper_reader.py --workspace /path/to/library doctor
python3 /path/to/agent-paper-reader/scripts/paper_reader.py --workspace /path/to/library setup
python3 /path/to/agent-paper-reader/scripts/paper_reader.py --workspace /path/to/library import /path/to/paper.pdf
# After the Agent completes structure, translation, alignment, and review:
python3 /path/to/agent-paper-reader/scripts/paper_reader.py --workspace /path/to/library export DOCUMENT_ID
# Only when management and editing are needed:
python3 /path/to/agent-paper-reader/scripts/paper_reader.py --workspace /path/to/library serve --port 8765
```

Windows can use `py -3.12`, but has not been tested. The workspace contains `data/`, `exports/`, `submissions/`, `.runtime/`, and `.cache/`. After setup, exporting and reading require no network; model connectivity depends on the host Agent.

</details>

## Current limits

- Text-based PDF, UTF-8 Markdown, plain text, local HTML/HTM, Word (`.docx`), and single-file LaTeX (`.tex`); no OCR, EPUB, or URL fetching. Configurable language directions remain future work.
- Uses the host Agent's model and allowance; this does not imply free or offline inference. Long documents require batches, and complex layouts require visual inspection.
- Structural validation does not replace semantic review or validate the original author's claims. Processing limitations for figure text, equations, and references must be reported.
- Exports may include source-page images, notes, and revision history. Check contents before sharing; files can be large.
- Processing and browser workflows were verified on macOS with Python 3.12. Other operating systems, other Agents, and host installation/auto-discovery remain untested. See [validation notes](docs/validation.md).

## Development and contribution

The Skill and implementation live together: `scripts/reader/` contains the backend, `dev/web/` the frontend source, and `assets/reader/` the bundled build. To rebuild, run `npm ci` and `npm run build` in `dev/web/`, then sync the output into `assets/reader/`.

See [AGENTS.md](AGENTS.md) for development conventions and [workflow.md](references/workflow.md) for the processing contract. Reproducible issue reports are welcome; use a minimal public sample where possible.

Code is available under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md). Paper excerpts in the demo are outside the project's code license.
