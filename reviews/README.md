# Reviews and Presentation Materials

## Course Reviews

| Document                                        | Topic                          | Available formats                                       |
| ----------------------------------------------- | ------------------------------ | ------------------------------------------------------- |
| [Review 1](review%201/CoWiC-MAC_Case_Study.md)  | Literature survey              | Markdown and PDF                                        |
| [Review 2](review%202/CoWiC-MAC_Case_Study.pdf) | Architecture and formulation   | PDF and ODT                                             |
| [Review 3](review%203/CoWiC-MAC_Case_Study.md)  | Software implementation review | Markdown and [PDF](review%203/CoWiC-MAC_Case_Study.pdf) |

## Project Document

- [Project document](project_document.md) (Markdown)
- [Project document PDF](CoWiC-MAC_Project_Document.pdf)

## Team Study Guide

- [Study guide](study_guide.md) (Markdown), [PDF](CoWiC-MAC_Study_Guide.pdf). One guide for the whole team: concepts, viva questions, code map.

## Presentation Study Guides (per presenter)

Ownership matches Review 2.

| Guide                                       | Presenter       | Domain               |
| ------------------------------------------- | --------------- | -------------------- |
| [Upayan's guide](study_guide_upayan.md)     | Upayan Mazumder | Central architecture |
| [Nitesh's guide](study_guide_nitesh.md)     | Nitesh Pandey   | WiFi side            |
| [Divyansh's guide](study_guide_divyansh.md) | Divyansh Saxena | ZigBee side          |

## Source Papers

- [Base paper](Base%20Paper.pdf) — Cheng, Li, Pan (_Computer Networks_, 2026)
- [Assignment brief](F1%20TF1%20Casestudy.pdf)

## Build the PDFs

The PDFs come from the Markdown sources. After you edit a source, build the PDFs again:

```bash
npm install --no-save @mermaid-js/mermaid-cli
python scripts/build_docs.py --mmdc node_modules/.bin/mmdc
```

The script needs `pandoc`, the Mermaid CLI, and Google Chrome or Chromium.
It finds Chrome or Chromium on `PATH` and uses the macOS Chrome path as a fallback.
Use `--chrome /path/to/chrome` or `--pandoc /path/to/pandoc` when a tool is outside `PATH`.
It renders each Mermaid diagram as a figure and each equation as MathML.
