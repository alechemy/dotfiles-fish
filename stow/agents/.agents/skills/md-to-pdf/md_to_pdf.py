#!/usr/bin/env python3
"""Convert Markdown to annotation-friendly PDFs with Pandoc and Typst."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
READER = "markdown+gfm_auto_identifiers+autolink_bare_uris+task_lists+lists_without_preceding_blankline-citations"
FONT_PREFIXES = ("SourceSerif4", "SourceSans3", "SourceCodePro")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
METADATA = {"title", "subtitle", "author", "date"}


class ConversionError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")


def run(command, *, data=None, cwd=None):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("TYPST_") and key != "SOURCE_DATE_EPOCH"}
    env["NO_COLOR"] = "1"
    try:
        result = subprocess.run(command, input=data, capture_output=True,
                                cwd=cwd, env=env, timeout=120)
    except FileNotFoundError as exc:
        raise ConversionError(f"Required command is missing: {command[0]}. See the skill's setup instructions.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"{command[0]} exceeded the two-minute limit.") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-3000:]
        raise ConversionError(f"{command[0]} failed: {detail}")
    if result.stderr.strip():
        raise ConversionError(f"{command[0]} reported diagnostics; output was not replaced:\n"
                              + result.stderr.decode("utf-8", errors="replace")[-3000:])
    return result.stdout


def tool_versions():
    versions = {}
    for tool, minimum in (("pandoc", (3, 11)), ("typst", (0, 15))):
        version = run([tool, "--version"]).decode().splitlines()[0]
        match = re.search(r"\b(\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < minimum:
            raise ConversionError(f"{tool} {minimum[0]}.{minimum[1]} or newer is required.")
        versions[tool] = version
    return versions


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ConversionError(f"Expected a regular file: {path}")
        return stream.read()


def font_snapshot(directories, prefixes=FONT_PREFIXES, required_files=()):
    fonts = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if (path.suffix.lower() not in {".otf", ".ttf"}
                    or not (path.name in required_files or path.name.startswith(prefixes))):
                continue
            data = read_regular(path)
            if path.name in fonts and fonts[path.name] != data:
                raise ConversionError(f"Conflicting font files named {path.name}; select one directory with --font-dir.")
            fonts[path.name] = data
    missing = [prefix for prefix in prefixes if not any(name.startswith(prefix) for name in fonts)]
    missing += [name for name in required_files if name not in fonts]
    if missing:
        raise ConversionError("Required font files are missing: " + ", ".join(missing)
                              + ". See the skill's setup instructions.")
    return fonts


def walk(value):
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def prepare_document(source, source_bytes):
    document = json.loads(run(["pandoc", f"--from={READER}", "--to=json"], data=source_bytes))
    warnings = []
    omitted = set(document["meta"]) - METADATA
    if omitted:
        warnings.append("Non-display frontmatter was ignored; only title, subtitle, author, and date are rendered.")
    document["meta"] = {key: value for key, value in document["meta"].items() if key in METADATA}
    images = {}
    root = source.parent.resolve()
    for node in walk(document):
        kind = node["t"]
        content = node.get("c")
        if kind == "MetaMap":
            raise ConversionError("Display frontmatter must contain text or lists of text, not nested mappings.")
        if kind in {"RawBlock", "RawInline"}:
            raise ConversionError(f"Raw {content[0]} is unsupported. Use native Markdown or a local image instead.")
        if kind == "CodeBlock":
            languages = {name.lower() for name in content[0][1]}
            if languages & {"dot", "graphviz", "plantuml"}:
                raise ConversionError("This diagram language is unsupported. Export it to a local SVG or PNG first.")
            if "mermaid" in languages:
                if "%%{" in content[1] or content[1].lstrip().startswith("---"):
                    raise ConversionError("Mermaid configuration directives and frontmatter are unsupported; the reading profile owns rendering settings.")
                if len(content[1]) > 50000:
                    raise ConversionError("A Mermaid diagram exceeds the 50,000-character limit.")
            else:
                content[1] = content[1].expandtabs(4)
        if kind == "Table":
            columns = len(content[2])
            tokens = [n["c"] for n in walk(node) if n["t"] in {"Str", "Code"}]
            if columns > 4 or any(len(token if isinstance(token, str) else token[1]) > 24 for token in tokens):
                warnings.append(f"A {columns}-column table may be too wide; inspect it before transferring the PDF.")
        if kind in {"Div", "Span"} and (content[0][1] or content[0][2]):
            warnings.append("Custom Div/Span styling is not reproduced; its text is retained.")
        if kind == "Link":
            target = content[2][0]
            parsed = urlsplit(target)
            if parsed.scheme.lower() not in {"", "http", "https", "mailto"} or target.startswith("//"):
                raise ConversionError("An unsupported link scheme was found; use https, mailto, or document links.")
            if not parsed.scheme and not target.startswith("#"):
                warnings.append("Relative document links may not resolve on the tablet.")
        if kind != "Image":
            continue
        target = content[2][0]
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ConversionError("Images must be local relative paths. Download remote images explicitly before conversion.")
        relative = Path(unquote(parsed.path))
        if relative.is_absolute():
            raise ConversionError("Image paths must be relative to the Markdown document.")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ConversionError("Images must stay within the Markdown document's directory tree.")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ConversionError("Unsupported image format. Use PNG, JPEG, SVG, or WebP.")
        data = read_regular(path)
        staged = "images/" + digest(data) + path.suffix.lower()
        images[staged] = data
        content[2][0] = staged
        if content[0][2]:
            warnings.append("Image dimensions and custom attributes were reset to fit the text column.")
        content[0][2] = []
    return document, images, sorted(set(warnings))


def render_typst(document, profile, side, template, workspace):
    variables = dict(profile)
    if document.get("meta"):
        variables["title-block"] = "true"
    variables["left-margin"] = profile["annotation-margin" if side == "left" else "text-margin"]
    variables["right-margin"] = profile["annotation-margin" if side == "right" else "text-margin"]
    template_path = workspace / "reader.typst"
    template_path.write_bytes(template)
    command = ["pandoc", "--from=json", "--to=typst", "--standalone",
               "--syntax-highlighting=none", "--wrap=none", f"--template={template_path}"]
    for key, value in variables.items():
        command.append(f"--variable={key}:{value}")
    return run(command, data=json_bytes(document), cwd=workspace)


def fingerprint(source_bytes, images, fonts, profile, side, template, versions):
    return digest(json_bytes({
        "source": digest(source_bytes),
        "images": {name: digest(data) for name, data in images.items()},
        "fonts": {name: digest(data) for name, data in fonts.items()},
        "profile": profile,
        "annotation_side": side,
        "template": digest(template),
        "converter": digest(Path(__file__).resolve().read_bytes()),
        "reader": READER,
        "tools": versions,
    }))


def output_path(source, requested, edition, build_id):
    path = requested or source.with_suffix(".boox.pdf")
    path = path.expanduser().absolute()
    if path.suffix.lower() != ".pdf":
        raise ConversionError("The output filename must end in .pdf.")
    if edition:
        path = path.with_name(f"{path.stem}.{build_id[:12]}.pdf")
    return path.parent.resolve() / path.name


def checked_state(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ConversionError(f"Refusing a symlink or non-regular output: {path}")
    return digest(read_regular(path))


def inspect_output(output, record, build_id, edition):
    pdf_hash = checked_state(output)
    checked_state(record)
    if pdf_hash is None:
        return None, False
    try:
        manifest = json.loads(record.read_bytes())
    except (FileNotFoundError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("pdf_sha256") != pdf_hash:
        raise ConversionError("The existing PDF is untracked or has changed, possibly through annotation. "
                              "Choose a new output path; it will not be overwritten.")
    if manifest.get("fingerprint") == build_id:
        return pdf_hash, True
    if edition:
        raise ConversionError("This edition path already belongs to another build. Choose a new output path.")
    return pdf_hash, False


@contextlib.contextmanager
def output_lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ConversionError("The output lock must be a regular file.")
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def mermaid_nodes(document):
    return [node for node in walk(document) if node["t"] == "CodeBlock"
            and "mermaid" in {name.lower() for name in node["c"][0][1]}]


def mermaid_environment(browser):
    root = run(["mise", "where", "npm:@mermaid-js/mermaid-cli"]).decode().strip()
    script = (HERE / "render_mermaid.mjs").read_bytes()
    config = json.loads((ASSETS / "mermaid.json").read_bytes())
    identity = json.loads(run(["node", str(HERE / "render_mermaid.mjs"), root, "--describe"]))
    browser = browser.expanduser().resolve(strict=True)
    identity.update(browser=run([str(browser), "--version"]).decode().strip(),
                    renderer=digest(script), config=digest(json_bytes(config)))
    return {"root": root, "browser": str(browser), "script": script,
            "config": config, "identity": identity}


def diagram_size(data, profile):
    try:
        svg = ET.fromstring(data)
        if svg.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("not SVG")
        x, y, width, height = map(float, svg.attrib["viewBox"].split())
        if not all(math.isfinite(value) for value in (x, y, width, height)) or min(width, height) <= 0:
            raise ValueError("invalid dimensions")
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise ConversionError("Mermaid returned an SVG without valid dimensions.") from exc
    for element in svg.iter():
        if element.tag.rsplit("}", 1)[-1] in {"foreignObject", "script", "image"}:
            raise ConversionError("Mermaid returned unsupported HTML, scripts, or embedded images.")
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                raise ConversionError("Mermaid returned an external SVG reference.")
        for value in [*element.attrib.values(), element.text or ""]:
            for target in re.findall(r"url\((.*?)\)", value, re.IGNORECASE | re.DOTALL):
                if not target.strip().strip("\"'").startswith("#"):
                    raise ConversionError("Mermaid returned an external CSS reference.")
    available_width = profile["page-width"] - profile["text-margin"] - profile["annotation-margin"]
    available_height = profile["page-height"] - profile["top-margin"] - profile["bottom-margin"] - 20
    scale = min(25.4 / 96, available_width / width, available_height / height)
    return width * scale, 16 * scale * 72 / 25.4


def render_diagrams(document, profile, workspace, runtime):
    nodes = mermaid_nodes(document)
    unique = {digest(node["c"][1].encode()): node["c"][1] for node in nodes}
    script = workspace / "render_mermaid.mjs"
    script.write_bytes(runtime["script"])
    candidates = sorted(path for path in (workspace / "fonts").iterdir()
                        if path.name.startswith("SourceSans3") and "Italic" not in path.name
                        and ("[" in path.name or "Regular" in path.name))
    if not candidates:
        raise ConversionError("Mermaid requires a regular or variable Source Sans 3 font file.")
    payload = {"browser": runtime["browser"], "font": str(candidates[0]),
               "output": str(workspace), "config": runtime["config"],
               "diagrams": [{"id": key, "source": value} for key, value in unique.items()]}
    result = json.loads(run(["node", str(script), runtime["root"], "--render"], data=json_bytes(payload), cwd=workspace))
    if (not isinstance(result, list) or len(result) != len(unique)
            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                   or any(item.get(key) is not None and not isinstance(item[key], str)
                          for key in ("title", "description")) for item in result)
            or {item["id"] for item in result} != set(unique)):
        raise ConversionError("Mermaid returned an unexpected set of diagrams.")
    rendered = {}
    warnings = []
    for index, item in enumerate(result, start=1):
        path = workspace / (item["id"] + ".svg")
        data = path.read_bytes()
        width, label_size = diagram_size(data, profile)
        rendered[item["id"]] = (path.name, width, item.get("description") or item.get("title") or "Mermaid diagram")
        if label_size < 9:
            warnings.append(f"Mermaid diagram {index} has small labels when fitted, approximately {label_size:.1f} pt; inspect it on the tablet.")
    for node in nodes:
        filename, width, alt = rendered[digest(node["c"][1].encode())]
        identifier = node["c"][0][0]
        node.clear()
        node.update(t="Para", c=[{"t": "Image", "c": [
            [identifier, [], [["width", f"{width:.3f}mm"]]],
            [{"t": "Str", "c": alt}], [filename, ""]]}])
    return warnings


def compile_pdf(document, images, fonts, profile, side, template, workspace, mermaid=None):
    for name, data in {**images, **{"fonts/" + name: data for name, data in fonts.items()}}.items():
        path = workspace / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    font_args = ["--ignore-system-fonts", "--font-path", str(workspace / "fonts")]
    discovered = set(run(["typst", "fonts", *font_args], cwd=workspace).decode().splitlines())
    for family in (profile["prose-font"], profile["heading-font"], profile["code-font"]):
        if family not in discovered:
            raise ConversionError(f"Typst cannot find the required font family: {family}.")
    if mermaid:
        mermaid["warnings"] = render_diagrams(document, profile, workspace, mermaid)
    (workspace / "document.typ").write_bytes(render_typst(document, profile, side, template, workspace))
    pdf = workspace / "document.pdf"
    run(["typst", "compile", "--root", str(workspace), *font_args,
         "--creation-timestamp", "0", "--diagnostic-format", "short",
         str(workspace / "document.typ"), str(pdf)], cwd=workspace)
    if not pdf.read_bytes().startswith(b"%PDF-"):
        raise ConversionError("Typst did not produce a PDF.")
    return pdf


def convert(args):
    source = args.source.expanduser().resolve(strict=True)
    if source.suffix.lower() not in {".md", ".markdown", ".mdown"}:
        raise ConversionError("The source must be a Markdown file.")
    source_bytes = read_regular(source)
    source_bytes.decode("utf-8-sig")
    versions = tool_versions()
    profile = json.loads((ASSETS / f"{args.profile}.json").read_bytes())
    template = (ASSETS / "reader.typst").read_bytes()
    document, images, warnings = prepare_document(source, source_bytes)
    has_diagrams = bool(mermaid_nodes(document))
    prefixes, required_files = FONT_PREFIXES, ()
    if args.fonts != "source":
        pairing = json.loads((ASSETS / f"fonts-{args.fonts}.json").read_bytes())
        required_files = pairing.pop("font-files")
        profile.update(pairing)
        prefixes = ("SourceSans3",) if has_diagrams else ()
    directories = args.font_dir or [Path.home() / "Library/Fonts", Path("/Library/Fonts")]
    fonts = font_snapshot([path.expanduser() for path in directories], prefixes, required_files)
    mermaid = mermaid_environment(args.browser) if has_diagrams else None
    if mermaid:
        versions = {**versions, "mermaid": mermaid["identity"]}
    build_id = fingerprint(source_bytes, images, fonts, profile, args.annotation_side, template, versions)
    output = output_path(source, args.output, args.edition, build_id)
    record = output.with_suffix(".pdf.build.json")
    if not output.parent.is_dir():
        raise ConversionError("The output directory does not exist; create it first.")
    with output_lock(output.with_suffix(".pdf.lock")):
        previous, unchanged = inspect_output(output, record, build_id, args.edition)
        if unchanged:
            saved = json.loads(record.read_bytes()).get("warnings", [])
            if not isinstance(saved, list) or not all(isinstance(item, str) for item in saved):
                raise ConversionError("The build record has invalid warnings; choose a new output path.")
            return output, "unchanged", sorted(set(warnings + saved))
        with tempfile.TemporaryDirectory(prefix=".md-to-pdf-", dir=output.parent) as temporary:
            workspace = Path(temporary)
            options = {"mermaid": mermaid} if mermaid else {}
            pdf = compile_pdf(document, images, fonts, profile, args.annotation_side, template, workspace, **options)
            warnings = sorted(set(warnings + (mermaid or {}).get("warnings", [])))
            manifest = workspace / "build.json"
            manifest.write_bytes(json_bytes({"schema": 1, "fingerprint": build_id,
                                             "pdf_sha256": digest(pdf.read_bytes()), "warnings": warnings}) + b"\n")
            if checked_state(output) != previous:
                raise ConversionError("The output changed during conversion; it was not overwritten.")
            checked_state(record)
            pdf.chmod(0o600)
            manifest.chmod(0o600)
            if previous is None:
                os.link(pdf, output)
            else:
                os.replace(pdf, output)
            os.replace(manifest, record)
    return output, "created" if previous is None else "updated", warnings


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("source", type=Path)
    result.add_argument("-o", "--output", type=Path, help="Output PDF path; its directory must already exist.")
    result.add_argument("--profile", choices=["boox-note-max"], default="boox-note-max")
    result.add_argument("--fonts", choices=["source", "tiempos-berkeley"], default="tiempos-berkeley",
                        help="Font pairing, default: tiempos-berkeley; selected fonts must be installed locally.")
    result.add_argument("--annotation-side", choices=["left", "right"], default="right")
    result.add_argument("--edition", action="store_true", help="Add a build fingerprint to the PDF filename for annotation.")
    result.add_argument("--browser", type=Path, default=Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                        help="Chromium executable used only for Mermaid diagrams.")
    result.add_argument("--font-dir", type=Path, action="append", help="Search this directory instead of macOS font directories; repeatable.")
    return result


def main():
    try:
        output, status, warnings = convert(parser().parse_args())
    except (ConversionError, OSError, UnicodeError, ValueError) as exc:
        print(f"md-to-pdf: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"md-to-pdf: warning: {warning}", file=sys.stderr)
    print(f"{status}: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
