"""Build the review PDFs from their Markdown sources (Issue #27).

The script does three steps for each document:

1. Render each Mermaid block to a PNG figure with the Mermaid CLI.
2. Convert the Markdown to HTML with pandoc. Equations become MathML.
3. Print the HTML to PDF with headless Google Chrome.

External tools (not Python packages):

* ``pandoc``
* the Mermaid CLI ``mmdc`` (``npm install --no-save @mermaid-js/mermaid-cli``)
* Google Chrome or Chromium

Usage::

    python scripts/build_docs.py
    npm install --no-save @mermaid-js/mermaid-cli
    python scripts/build_docs.py --mmdc node_modules/.bin/mmdc
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (Markdown source, PDF output), relative to the repository root.
DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("reviews/project_document.md", "reviews/CoWiC-MAC_Project_Document.pdf"),
    (
        "reviews/review 3/CoWiC-MAC_Case_Study.md",
        "reviews/review 3/CoWiC-MAC_Case_Study.pdf",
    ),
    ("reviews/study_guide.md", "reviews/CoWiC-MAC_Study_Guide.pdf"),
)

DEFAULT_CHROME = next(
    (
        path
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "chrome.exe",
        )
        if (path := shutil.which(name)) is not None
    ),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

FENCE = re.compile(r"^```(mermaid|math)[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       font-size: 10pt; line-height: 1.4; color: #111; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 1.4em; border-bottom: 1px solid #bbb;
     break-after: avoid; }
h3 { font-size: 12.5pt; break-after: avoid; }
table { border-collapse: collapse; margin: 0.8em 0; width: 100%;
        font-size: 9.5pt; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
th, td { border: 1px solid #999; padding: 4px 7px; text-align: left;
         vertical-align: top; }
th { background: #eee; }
code { font-family: Menlo, Consolas, monospace; font-size: 9pt;
       background: #f3f3f3; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f6f6; padding: 8px; border-radius: 4px;
      white-space: pre-wrap; break-inside: avoid; }
figure, p > img { break-inside: avoid; }
img { display: block; max-width: 100%; max-height: 120mm; margin: 0.6em auto; }
math[display="block"] { margin: 0.8em 0; font-size: 11.5pt; }
a { color: #1a4fa0; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccc; }
.eq { text-align: center; background: #f6f8fb; border: 1px solid #d5dde8;
       padding: 6px 8px; margin: 0.6em 0;
       font-family: Cambria, "Times New Roman", serif; }
svg { max-height: 70mm; margin: 0.4em 0; }
em { color: #333; }
"""


class BuildError(RuntimeError):
    """Raised when an external tool fails or is missing."""


def render_mermaid(
    source: str, out_png: Path, mmdc: Sequence[str], chrome: str
) -> None:
    """Render one Mermaid diagram to a PNG file."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        diagram = tmp_dir / "diagram.mmd"
        diagram.write_text(source, encoding="utf-8")
        config = tmp_dir / "puppeteer.json"
        config.write_text(
            json.dumps({"executablePath": chrome, "args": ["--no-sandbox"]}),
            encoding="utf-8",
        )
        _run(
            [
                *mmdc,
                "-p",
                str(config),
                "-i",
                str(diagram),
                "-o",
                str(out_png),
                "-b",
                "white",
                "-s",
                "3",
                "-q",
            ]
        )


def prepare_markdown(
    text: str, asset_dir: Path, mmdc: Sequence[str], chrome: str
) -> str:
    """Return Markdown with Mermaid blocks as images and math blocks as $$."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        kind, body = match.group(1), match.group(2)
        if kind == "math":
            # One line, so Markdown cannot read a leading "+" as a list item.
            return "$$" + " ".join(body.split()) + "$$"
        count += 1
        png = asset_dir / f"figure_{count}.png"
        render_mermaid(body, png, mmdc, chrome)
        return f"![]({png.name})"

    return FENCE.sub(replace, text)


def build_pdf(
    source: Path, output: Path, mmdc: Sequence[str], chrome: str, pandoc: str
) -> Path:
    """Build one PDF from one Markdown file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        work = Path(tmp)
        prepared = work / "doc.md"
        prepared.write_text(
            prepare_markdown(source.read_text(encoding="utf-8"), work, mmdc, chrome),
            encoding="utf-8",
        )
        css = work / "style.css"
        css.write_text(CSS, encoding="utf-8")
        html = work / "doc.html"
        _run(
            [
                pandoc,
                str(prepared),
                "--from",
                "gfm+tex_math_dollars",
                "--to",
                "html5",
                "--standalone",
                "--mathml",
                "--css",
                css.name,
                "--metadata",
                f"pagetitle={source.stem}",
                "--output",
                str(html),
            ],
            cwd=work,
        )
        generated = work / "output.pdf"
        _run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-pdf-header-footer",
                f"--user-data-dir={work / 'chrome-profile'}",
                f"--print-to-pdf={generated}",
                html.as_uri(),
            ]
        )
        if not generated.is_file() or generated.stat().st_size == 0:
            raise BuildError(f"Chrome did not write {generated}")
        with generated.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                raise BuildError(f"Chrome wrote an invalid PDF: {generated}")
        generated.replace(output)
    return output


def _run(command: Sequence[str], cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, check=True, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise BuildError(f"Tool not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(
            f"{Path(command[0]).name} failed:\n{exc.stderr.strip()[-2000:]}"
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mmdc",
        default="mmdc",
        help="Mermaid CLI executable (default: mmdc on PATH).",
    )
    parser.add_argument("--chrome", default=DEFAULT_CHROME, help="Chrome binary.")
    parser.add_argument("--pandoc", default="pandoc", help="pandoc binary.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build every document in DOCUMENTS."""
    args = parse_args(argv)
    mmdc = [args.mmdc]
    for tool in (*mmdc, args.pandoc, args.chrome):
        if shutil.which(tool) is None and not Path(tool).exists():
            print(f"Missing tool: {tool}", file=sys.stderr)
            return 1
    for source, output in DOCUMENTS:
        path = build_pdf(
            REPO_ROOT / source, REPO_ROOT / output, mmdc, args.chrome, args.pandoc
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
