"""Behavioral tests for the saved-report terminal browser."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from io import StringIO
from pathlib import Path
from shutil import copyfile

import pytest
from rich.console import Console
from rich.text import Text
from textual.widgets import Input, OptionList, Static, Tab, Tabs

from reproready import checker_tui
from reproready.checker_tui import ReportBrowserApp
from reproready.checker_view import (
    SavedReportEntry,
    SavedReportError,
    load_saved_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "checker-report-v1"


def _entries(*names: str) -> list[SavedReportEntry]:
    return [SavedReportEntry(FIXTURES / name) for name in names]


async def _wait_for_load(app: ReportBrowserApp) -> None:
    await asyncio.wait_for(app.load_idle.wait(), timeout=3)


def _render_text(renderable) -> str:
    output = StringIO()
    Console(
        file=output,
        color_system=None,
        force_terminal=False,
        no_color=True,
        width=80,
    ).print(renderable)
    return output.getvalue()


def _static_text(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, Text)
    return content.plain


def _rendered_line(widget: Static) -> str:
    return widget.render_line(0).text.rstrip()


def _surface_top_line(screen, widget) -> str:
    return screen._compositor.render_strips()[widget.region.y].text


def _footer(screen) -> checker_tui._ShortcutFooter:
    return screen.query_one(checker_tui._ShortcutFooter)


def _footer_text(screen) -> str:
    content = _footer(screen).content
    assert isinstance(content, Text)
    return content.plain


async def _open_selected(app: ReportBrowserApp, pilot) -> None:
    app.load_idle.clear()
    await pilot.press("enter")
    await _wait_for_load(app)
    await pilot.pause()


def test_artifact_list_keeps_search_input_keys_scoped() -> None:
    async def exercise() -> None:
        app = ReportBrowserApp(
            _entries("partial-hostile-zip.json", "complete-direct-python.json")
        )
        async with app.run_test(size=(52, 24)) as pilot:
            await pilot.pause()
            assert app.screen is app._artifact_screen
            options = app.screen.query_one("#artifact-options", OptionList)
            assert len(options._options) == 2
            title = app.screen.query_one("#artifact-title")
            content = app.screen.query_one("#artifact-content")
            footer = _footer(app.screen)
            assert content.region.y == title.region.bottom
            assert content.region.bottom == footer.region.y
            assert all(
                hint in _footer_text(app.screen) for hint in ("Search", "Help", "Quit")
            )

            await pilot.press("slash", "q", "a", "left", "right")
            await pilot.pause()

            search = app.screen.query_one("#artifact-search", Input)
            assert search.value == "qa"
            assert app.selected_index == 0
            assert app.screen is app._artifact_screen
            footer_text = _footer_text(app.screen)
            assert all(
                hint not in footer_text for hint in ("Search", "Help", "Quit")
            ), footer_text

            await pilot.press("escape")
            await pilot.pause()
            assert not search.has_focus
            assert all(
                hint in _footer_text(app.screen) for hint in ("Search", "Help", "Quit")
            )
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


def test_navigation_restores_page_detail_and_reading_position() -> None:
    async def exercise() -> None:
        app = ReportBrowserApp(
            _entries("partial-hostile-zip.json", "complete-direct-python.json")
        )
        async with app.run_test(size=(52, 12)) as pilot:
            await pilot.pause()
            await _open_selected(app, pilot)
            subject = app._report_screen.query_one("#report-subject", Static)
            assert "\\u001b" in _static_text(subject)
            assert "\u001b" not in _static_text(subject)
            await pilot.press(*(["right"] * 6), "a")
            await pilot.pause()
            assert app._page_keys[0] == "review"

            viewport = app._viewport()
            viewport.scroll_to(y=10, animate=False, force=True)
            await pilot.pause()
            saved_position = viewport.scroll_y
            assert saved_position > 0

            await pilot.press("right", "left")
            await pilot.pause()
            assert app._page_keys[0] == "review"
            assert app._viewport().scroll_y == saved_position

            app.load_idle.clear()
            await pilot.press("right_square_bracket")
            await _wait_for_load(app)
            assert app.selected_index == 1
            assert app._page_keys[1] == "artifact"

            app.load_idle.clear()
            await pilot.press("left_square_bracket")
            await _wait_for_load(app)
            await pilot.pause()

            assert app._page_keys[0] == "review"
            assert app._detail_modes[0] is True
            assert app._viewport().scroll_y == saved_position
            detail = app._report_screen.query_one("#report-detail", Static)
            assert _static_text(detail) == "All saved detail"
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


def test_single_report_opens_directly_and_empty_pages_remain_addressable() -> None:
    async def exercise() -> None:
        app = ReportBrowserApp(_entries("complete-direct-python.json"))
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_load(app)
            assert app._page_keys[0] == "artifact"

            await pilot.press("right", "right")
            await pilot.pause()
            assert app._page_keys[0] == "limits"
            await pilot.press("s")
            await pilot.pause()
            assert type(app.screen).__name__ == "_SectionMenu"
            await pilot.press("down", "enter")
            await pilot.pause()

            assert app._page_keys[0] == "skipped"
            content = app._report_screen.query_one("#report-content")
            assert "Checks skipped" in _surface_top_line(app.screen, content)
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


def test_responsive_report_chrome_preserves_full_identity(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "long-unicode.json"
    copyfile(FIXTURES / "complete-direct-python.json", report_path)
    document = json.loads(report_path.read_text(encoding="utf-8"))
    display_name = "analysis-" + "研究-" * 30 + "final.py"
    document["artifact"]["display_name"] = display_name
    report_path.write_text(
        json.dumps(document, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    expected_units = (
        "← → Pages",
        "[ ] Reports",
        "s Sections",
        "Esc List",
        "a Detail",
        "? Help",
        "q Quit",
    )
    assert checker_tui._REPORT_TABS_MIN_WIDTH == 92

    async def exercise() -> None:
        app = ReportBrowserApp([SavedReportEntry(report_path)])
        async with app.run_test(size=(100, 32)) as pilot:
            await _wait_for_load(app)
            await pilot.press("a")
            await pilot.pause()
            assert app._detail_modes[0] is True

            body = app._report_screen.query_one("#report-body", Static)
            rendered_body = _render_text(body.content)
            assert rendered_body.count("研") == rendered_body.count("究") == 30
            assert "final.py" in rendered_body
            wide_viewport_height = app._viewport().region.height

            for width, height in (
                (100, 32),
                (92, 24),
                (91, 24),
                (76, 24),
                (52, 24),
                (100, 32),
            ):
                await pilot.resize_terminal(width, height)
                if width == 52:
                    await pilot.press("right", "right", "right")
                await pilot.pause()

                header = app._report_screen.query_one("#report-header")
                count = app._report_screen.query_one("#report-count", Static)
                subject = app._report_screen.query_one("#report-subject", Static)
                detail = app._report_screen.query_one("#report-detail", Static)
                tabs = app._report_screen.query_one("#report-tabs", Tabs)
                narrow_navigation = app._report_screen.query_one(
                    "#report-narrow-navigation", Static
                )
                content = app._report_screen.query_one("#report-content")
                wide = width >= 92
                navigation = tabs if wide else narrow_navigation

                assert tabs.display is wide
                assert narrow_navigation.display is not wide
                assert navigation.region.y - header.region.bottom == 1
                assert content.region.y == navigation.region.bottom
                expected_title = (
                    "Checks skipped" if app._page_keys[0] == "skipped" else "Artifact"
                )
                assert expected_title in _surface_top_line(app.screen, content)

                if wide:
                    tab_widgets = list(tabs.query(Tab))
                    assert len(tab_widgets) == len(checker_tui.SECTION_KEYS)
                    assert all(
                        tabs.region.x <= tab.region.x
                        and tab.region.right <= tabs.region.right
                        for tab in tab_widgets
                    )
                    left_space = tab_widgets[0].region.x - tabs.region.x
                    right_space = tabs.region.right - tab_widgets[-1].region.right
                    assert abs(left_space - right_space) <= 1
                else:
                    assert _static_text(narrow_navigation).endswith(
                        f"{checker_tui.SECTION_KEYS.index(app._page_keys[0]) + 1}/8 "
                    )
                    if width == 52:
                        assert (
                            _static_text(narrow_navigation) == " Checks skipped  4/8 "
                        )

                assert header.region.height == 1
                assert (
                    count.region.height
                    == subject.region.height
                    == detail.region.height
                    == 1
                )
                assert count.region.y == subject.region.y == detail.region.y
                assert count.region.right <= subject.region.x
                assert subject.region.right <= detail.region.x
                header_center = (
                    2 * header.content_region.x + header.content_region.width
                )
                subject_center = 2 * subject.region.x + subject.region.width
                assert abs(header_center - subject_center) <= 2
                assert "Report 1 of 1" in _rendered_line(count)
                assert "All saved detail" in _rendered_line(detail)
                assert _static_text(subject) == display_name
                assert "…" in _rendered_line(subject)

                footer = _footer(app.screen)
                footer_text = _footer_text(app.screen)
                footer_lines = [
                    " ".join(line.split()) for line in footer_text.splitlines()
                ]
                assert all(
                    any(unit in line for line in footer_lines)
                    for unit in expected_units
                ), (width, footer_text)
                assert all(
                    Text(line).cell_len <= footer.content_size.width
                    for line in footer_text.splitlines()
                )

            assert app._viewport().region.height == wide_viewport_height
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


def test_rejected_report_does_not_block_later_valid_report(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    before = malformed.read_bytes()

    async def exercise() -> None:
        app = ReportBrowserApp(
            [
                SavedReportEntry(malformed),
                SavedReportEntry(FIXTURES / "complete-direct-python.json"),
            ]
        )
        async with app.run_test(size=(52, 24)) as pilot:
            await pilot.pause()
            app.load_idle.clear()
            await app.open_artifact(0)
            subject = app._report_screen.query_one("#report-subject", Static)
            assert _static_text(subject) == "malformed.json"
            await _wait_for_load(app)
            assert _static_text(subject) == "malformed.json"
            error = app.report_errors[0]
            assert error.code == "invalid_report_json"
            assert error.__traceback__ is None
            assert error.__context__ is None

            tabs = app._report_screen.query_one("#report-tabs", Tabs)
            narrow_navigation = app._report_screen.query_one(
                "#report-narrow-navigation", Static
            )
            assert tabs.display is False
            assert narrow_navigation.display is False
            await pilot.resize_terminal(100, 32)
            await pilot.pause()
            assert tabs.display is False
            assert narrow_navigation.display is False
            content = app._report_screen.query_one("#report-content")
            assert "Saved report unavailable" in _surface_top_line(app.screen, content)

            app.load_idle.clear()
            await pilot.press("right_square_bracket")
            await _wait_for_load(app)
            assert app._presentation is not None
            assert app.known_names[1] == "clean.py"
            assert _static_text(subject) == "clean.py"
            assert tabs.display is True
            assert narrow_navigation.display is False
            await pilot.press("q")
        assert app.return_value == 2

    asyncio.run(exercise())
    assert malformed.read_bytes() == before


def test_reopening_report_reads_changed_bytes_despite_restored_mtime(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    copyfile(FIXTURES / "complete-direct-python.json", report_path)
    original = report_path.read_bytes()
    changed = original.replace(b'"clean.py"', b'"other.py"', 1)
    assert changed != original
    assert len(changed) == len(original)
    original_mtime_ns = report_path.stat().st_mtime_ns

    async def exercise() -> None:
        app = ReportBrowserApp(
            [
                SavedReportEntry(report_path),
                SavedReportEntry(FIXTURES / "partial-hostile-zip.json"),
            ]
        )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await _open_selected(app, pilot)
            assert app.known_names[0] == "clean.py"

            app.load_idle.clear()
            await pilot.press("right_square_bracket")
            await _wait_for_load(app)

            report_path.write_bytes(changed)
            changed_status = report_path.stat()
            os.utime(
                report_path,
                ns=(changed_status.st_atime_ns, original_mtime_ns),
            )
            assert report_path.stat().st_mtime_ns == original_mtime_ns

            app.load_idle.clear()
            await pilot.press("left_square_bracket")
            await _wait_for_load(app)
            await pilot.pause()

            subject = app._report_screen.query_one("#report-subject", Static)
            assert app.known_names[0] == "other.py"
            assert _static_text(subject) == "other.py"
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("change", "error_code"),
    [
        ("remove", "report_not_found"),
        ("symlink", "invalid_report_path"),
    ],
)
def test_reopening_report_rechecks_file_admission(
    tmp_path: Path,
    change: str,
    error_code: str,
) -> None:
    report_path = tmp_path / "report.json"
    copyfile(FIXTURES / "complete-direct-python.json", report_path)
    other_path = FIXTURES / "partial-hostile-zip.json"

    async def exercise() -> None:
        app = ReportBrowserApp(
            [SavedReportEntry(report_path), SavedReportEntry(other_path)]
        )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await _open_selected(app, pilot)
            assert app.known_names[0] == "clean.py"

            app.load_idle.clear()
            await pilot.press("right_square_bracket")
            await _wait_for_load(app)
            assert app._presentation is not None

            report_path.unlink()
            if change == "symlink":
                report_path.symlink_to(
                    (FIXTURES / "complete-direct-python.json").resolve()
                )

            app.load_idle.clear()
            await pilot.press("left_square_bracket")
            await _wait_for_load(app)
            assert app.report_errors[0].code == error_code

            app.load_idle.clear()
            await pilot.press("right_square_bracket")
            await _wait_for_load(app)
            assert app._presentation is not None
            assert 1 not in app.report_errors
            await pilot.press("q")
        assert app.return_value == 2

    asyncio.run(exercise())


def test_validation_cache_never_admits_prior_failures(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    fixture_path = FIXTURES / "complete-direct-python.json"
    original = json.loads(fixture_path.read_text(encoding="utf-8"))
    cache: dict[Path, bytes] = {}

    schema_invalid = dict(original)
    schema_invalid.pop("inventory")
    report_path.write_text(json.dumps(schema_invalid), encoding="utf-8")
    for _attempt in range(2):
        with pytest.raises(SavedReportError) as caught:
            load_saved_report(report_path, validation_cache=cache)
        assert caught.value.code == "invalid_report_schema"
    assert report_path not in cache

    reference_invalid = json.loads(fixture_path.read_text(encoding="utf-8"))
    reference_invalid["source_index"][0]["member_id"] = "member:9"
    report_path.write_text(json.dumps(reference_invalid), encoding="utf-8")
    for _attempt in range(2):
        with pytest.raises(SavedReportError) as caught:
            load_saved_report(report_path, validation_cache=cache)
        assert caught.value.code == "invalid_report_references"
    assert report_path not in cache

    copyfile(fixture_path, report_path)
    restored = load_saved_report(report_path, validation_cache=cache)
    assert restored["artifact"]["display_name"] == "clean.py"


def test_validation_cache_returns_fresh_document(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    copyfile(FIXTURES / "complete-direct-python.json", report_path)
    cache: dict[Path, bytes] = {}

    first = load_saved_report(report_path, validation_cache=cache)
    first["artifact"]["display_name"] = "mutated.py"

    second = load_saved_report(report_path, validation_cache=cache)
    assert second["artifact"]["display_name"] == "clean.py"


def test_rapid_navigation_cannot_install_stale_report(monkeypatch) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[Path] = []
    first = FIXTURES / "partial-hostile-zip.json"
    third = FIXTURES / "complete-direct-python.json"

    def controlled_loader(
        path: Path,
        *,
        validation_cache: dict[Path, bytes] | None = None,
    ) -> dict[str, object]:
        calls.append(path)
        if path == first:
            first_started.set()
            assert release_first.wait(timeout=3)
        return load_saved_report(path, validation_cache=validation_cache)

    monkeypatch.setattr(checker_tui, "load_saved_report", controlled_loader)

    async def exercise() -> None:
        app = ReportBrowserApp(
            _entries(
                "partial-hostile-zip.json",
                "worker-error.json",
                "complete-direct-python.json",
            )
        )
        async with app.run_test(size=(52, 24)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            assert await asyncio.to_thread(first_started.wait, 3)
            subject = app._report_screen.query_one("#report-subject", Static)
            assert _static_text(subject) == "partial-hostile-zip.json"
            tabs = app._report_screen.query_one("#report-tabs", Tabs)
            narrow_navigation = app._report_screen.query_one(
                "#report-narrow-navigation", Static
            )
            try:
                assert tabs.display is False
                assert narrow_navigation.display is False
                await pilot.resize_terminal(100, 32)
                await pilot.pause()
                assert tabs.display is False
                assert narrow_navigation.display is False

                app.select_artifact_relative(1)
                app.select_artifact_relative(1)
                assert _static_text(subject) == "complete-direct-python.json"
            finally:
                release_first.set()
            await _wait_for_load(app)
            assert calls == [first, third]
            assert app.known_names[2] == "clean.py"
            assert _static_text(subject) == "clean.py"
            assert tabs.display is True
            assert narrow_navigation.display is False
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())


def test_return_to_list_rejects_late_loader_install(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def controlled_loader(
        path: Path,
        *,
        validation_cache: dict[Path, bytes] | None = None,
    ) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=3)
        return load_saved_report(path, validation_cache=validation_cache)

    monkeypatch.setattr(checker_tui, "load_saved_report", controlled_loader)

    async def exercise() -> None:
        app = ReportBrowserApp(
            _entries("partial-hostile-zip.json", "complete-direct-python.json")
        )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            assert await asyncio.to_thread(started.wait, 3)
            await app.show_artifact_list()
            release.set()
            await _wait_for_load(app)
            await pilot.pause()

            assert app.screen is app._artifact_screen
            assert app._presentation is None
            assert app.known_names == {}
            await pilot.press("q")
        assert app.return_value == 0

    asyncio.run(exercise())
