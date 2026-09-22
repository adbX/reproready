#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate the generated ReproReady documentation artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

CANONICAL_ORIGIN = "https://reproready.com"
EXPECTED_PAGES = {
    "https://reproready.com/": "index.html",
    "https://reproready.com/api/": "api/index.html",
    "https://reproready.com/checker/": "checker/index.html",
    "https://reproready.com/checker-ruleset-v1/": "checker-ruleset-v1/index.html",
}
EXPECTED_ASSETS = {
    "assets/fonts/IBM-Plex-Sans-OFL-1.1.txt",
    "assets/fonts/JetBrains-Mono-OFL-1.1.txt",
    "assets/fonts/NOTICE.txt",
    "assets/fonts/ibm-plex-sans-latin-400-italic.woff2",
    "assets/fonts/ibm-plex-sans-latin-400-normal.woff2",
    "assets/fonts/ibm-plex-sans-latin-600-italic.woff2",
    "assets/fonts/ibm-plex-sans-latin-600-normal.woff2",
    "assets/fonts/jetbrains-mono-latin-400-italic.woff2",
    "assets/fonts/jetbrains-mono-latin-400-normal.woff2",
    "assets/fonts/jetbrains-mono-latin-600-italic.woff2",
    "assets/fonts/jetbrains-mono-latin-600-normal.woff2",
    "javascripts/reproready.js",
    "stylesheets/reproready.css",
}
EXPECTED_API_HEADINGS = {"check_path", "checkreport", "checkmember", "checkinputerror"}
EXPECTED_SEARCH_PAGES = {"", "api/", "checker/", "checker-ruleset-v1/"}
FORBIDDEN_TEXT = {
    "checker-browser-v1",
    "Saved-report browser interaction protocol",
    "ArtifactReport",
    "score_path",
}
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)")


class PageParser(HTMLParser):
    """Collect identifiers and resource references from one generated page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.identifiers: set[str] = set()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            self.identifiers.add(identifier)
        for name in ("href", "src"):
            value = attributes.get(name)
            if value:
                self.references.append(value)


def _local_target(
    root: Path, source_url: str, reference: str
) -> tuple[Path, str] | None:
    if reference.startswith(("data:", "javascript:", "mailto:", "tel:")):
        return None
    resolved = urlsplit(urljoin(source_url, reference))
    if resolved.scheme not in {"", "http", "https"}:
        return None
    if resolved.netloc and f"{resolved.scheme}://{resolved.netloc}" != CANONICAL_ORIGIN:
        return None

    relative = unquote(resolved.path).lstrip("/")
    if not relative or resolved.path.endswith("/"):
        relative = f"{relative}index.html"
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"reference escapes the site root: {reference}") from None
    return target, unquote(resolved.fragment)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    root = root.resolve()

    sitemap = root / "sitemap.xml"
    if not sitemap.is_file():
        return ["missing sitemap.xml"]
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = {
        element.text
        for element in ElementTree.parse(sitemap).findall("s:url/s:loc", namespace)
        if element.text
    }
    if locations != set(EXPECTED_PAGES):
        errors.append(
            f"sitemap page inventory differs: expected {sorted(EXPECTED_PAGES)}, got {sorted(locations)}"
        )

    page_parsers: dict[Path, PageParser] = {}
    page_urls: dict[Path, str] = {}
    for url, relative in EXPECTED_PAGES.items():
        page = (root / relative).resolve()
        if not page.is_file():
            errors.append(f"missing page: {relative}")
            continue
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        page_parsers[page] = parser
        page_urls[page] = url

    for page, parser in page_parsers.items():
        source_url = page_urls[page]
        for reference in parser.references:
            try:
                resolved = _local_target(root, source_url, reference)
            except ValueError as error:
                errors.append(f"{page.relative_to(root)}: {error}")
                continue
            if resolved is None:
                continue
            target, fragment = resolved
            if not target.exists():
                errors.append(
                    f"{page.relative_to(root)}: missing local target for {reference}: "
                    f"{target.relative_to(root)}"
                )
                continue
            if fragment and target.suffix == ".html":
                target_parser = page_parsers.get(target)
                if target_parser is None:
                    target_parser = PageParser()
                    target_parser.feed(target.read_text(encoding="utf-8"))
                    page_parsers[target] = target_parser
                if fragment not in target_parser.identifiers:
                    errors.append(
                        f"{page.relative_to(root)}: missing fragment #{fragment} in "
                        f"{target.relative_to(root)}"
                    )

    for relative in sorted(EXPECTED_ASSETS):
        if not (root / relative).is_file():
            errors.append(f"missing website asset: {relative}")

    stylesheet = root / "stylesheets/reproready.css"
    if stylesheet.is_file():
        css = stylesheet.read_text(encoding="utf-8")
        for _, reference in CSS_URL.findall(css):
            if reference.startswith("data:"):
                continue
            target = (stylesheet.parent / unquote(urlsplit(reference).path)).resolve()
            try:
                relative = target.relative_to(root)
            except ValueError:
                errors.append(
                    f"stylesheet reference escapes the site root: {reference}"
                )
                continue
            if not target.is_file():
                errors.append(f"missing stylesheet asset: {relative}")
        if "fonts.googleapis.com" in css or "fonts.gstatic.com" in css:
            errors.append("stylesheet loads a remote font")

    search_path = root / "search.json"
    if not search_path.is_file():
        errors.append("missing search.json")
    else:
        search = json.loads(search_path.read_text(encoding="utf-8"))
        search_pages = {
            item["location"].split("#", 1)[0]
            for item in search.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("location"), str)
        }
        if search_pages != EXPECTED_SEARCH_PAGES:
            errors.append(
                f"search page inventory differs: expected {sorted(EXPECTED_SEARCH_PAGES)}, "
                f"got {sorted(search_pages)}"
            )
        search_text = search_path.read_text(encoding="utf-8")
        for forbidden in sorted(FORBIDDEN_TEXT):
            if forbidden in search_text:
                errors.append(f"search index includes excluded content: {forbidden}")

    api_page = root / "api/index.html"
    if api_page.is_file():
        parser = page_parsers.get(api_page.resolve())
        if parser is None:
            parser = PageParser()
            parser.feed(api_page.read_text(encoding="utf-8"))
        missing = EXPECTED_API_HEADINGS - parser.identifiers
        if missing:
            errors.append(f"Python API is missing selected headings: {sorted(missing)}")
        api_text = api_page.read_text(encoding="utf-8")
        for forbidden in sorted(FORBIDDEN_TEXT & {"ArtifactReport", "score_path"}):
            if forbidden in api_text:
                errors.append(f"Python API includes excluded export: {forbidden}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", nargs="?", type=Path, default=Path("site"))
    args = parser.parse_args()

    errors = validate(args.site)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        "Validated four documentation pages, local assets, links, and search inventory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
