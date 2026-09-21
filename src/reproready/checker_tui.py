"""Keyboard-only browsing for validated saved checker reports."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, cast

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option

from .checker_render import (
    _PALETTE,
    SECTION_KEYS,
    SECTION_SPECS,
    ReportPresentation,
    SectionKey,
    _terminal_text,
)
from .checker_view import SavedReportEntry, SavedReportError, load_saved_report

_PAGE_BINDING_GROUP = Binding.Group(description="Pages")
_REPORT_BINDING_GROUP = Binding.Group(description="Reports")
_REPORT_SELECTION_TEXT = "#262626"
_REPORT_TABS_MIN_WIDTH = (
    sum(Text(spec.short_title).cell_len + 4 for spec in SECTION_SPECS)
    + len(SECTION_SPECS)
    - 1
)


class _ShortcutFooter(Static, can_focus=False):
    """Render active shortcuts as whole wrapping units."""

    ALLOW_SELECT = False

    def _unit(
        self,
        bindings: list[tuple[Binding, bool]],
        description: str,
        width: int,
    ) -> Text:
        key_area = Text(" ", style="bold reverse")
        for position, (binding, enabled) in enumerate(bindings):
            if position:
                key_area.append(" ", style="bold reverse")
            style = "bold reverse" if enabled else "bold reverse dim"
            key_area.append(self.app.get_key_display(binding), style=style)
        key_area.append(" ", style="bold reverse")

        unit = Text()
        unit.append_text(key_area)
        if not description:
            return unit
        label = Text(
            description,
            style=("dim" if not any(enabled for _, enabled in bindings) else ""),
        )
        label_width = width - key_area.cell_len - 1
        if label_width <= 0:
            return unit
        if label.cell_len > label_width:
            label.truncate(label_width, overflow="ellipsis")
        unit.append(" ")
        unit.append_text(label)
        return unit

    def _refresh_bindings(self, screen: Screen | None = None) -> None:
        if screen is not None:
            if screen is not self.screen:
                return
            self.call_after_refresh(self._refresh_bindings)
            return
        if not self.is_attached:
            return
        active: list[tuple[Binding, bool]] = []
        seen_actions: set[str] = set()
        for active_binding in self.screen.active_bindings.values():
            binding = active_binding.binding
            if not binding.show or binding.action in seen_actions:
                continue
            seen_actions.add(binding.action)
            active.append((binding, active_binding.enabled))

        grouped: list[tuple[list[tuple[Binding, bool]], str]] = []
        position = 0
        while position < len(active):
            binding, _enabled = active[position]
            if binding.group is None:
                grouped.append(([active[position]], binding.description))
                position += 1
                continue
            end = position + 1
            while end < len(active) and active[end][0].group == binding.group:
                end += 1
            grouped.append((active[position:end], binding.group.description))
            position = end

        width = max(1, self.content_size.width)
        rendered = Text()
        line_width = 0
        for bindings, description in grouped:
            unit = self._unit(bindings, description, width)
            separator = 2 if line_width else 0
            if line_width and line_width + separator + unit.cell_len > width:
                rendered.append("\n")
                line_width = 0
                separator = 0
            if separator:
                rendered.append("  ")
                line_width += separator
            rendered.append_text(unit)
            line_width += unit.cell_len
        self.update(rendered)

    def on_mount(self) -> None:
        self.screen.bindings_updated_signal.subscribe(self, self._refresh_bindings)
        self.call_after_refresh(self._refresh_bindings)

    def on_resize(self, _event: Resize) -> None:
        self._refresh_bindings()

    def on_unmount(self) -> None:
        self.screen.bindings_updated_signal.unsubscribe(self)


class _ArtifactListScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("slash", "search", "Search", show=True, priority=True),
        Binding("up", "previous_option", show=False, priority=True),
        Binding("down", "next_option", show=False, priority=True),
        Binding("escape", "back", show=False, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True),
        Binding("q", "quit", "Quit", show=True, priority=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._visible_indices: list[int] = []

    def compose(self) -> ComposeResult:
        yield Static("Saved reports", id="artifact-title")
        with Container(id="artifact-content", classes="content-surface"):
            yield Static(
                "Choose one saved report. Search matches saved paths and artifact names "
                "that have already been loaded.",
                id="artifact-purpose",
            )
            yield Input(placeholder="Search saved reports", id="artifact-search")
            yield Static(id="artifact-count")
            yield OptionList(id="artifact-options")
            yield Static("No matching reports", id="artifact-empty")
            yield Static(
                "Up/Down choose  ·  Enter opens  ·  / searches",
                id="artifact-hint",
            )
        yield _ShortcutFooter()

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],
    ) -> bool | None:
        return not (action == "search" and isinstance(self.focused, Input))

    def on_mount(self) -> None:
        self.refresh_entries()
        self.query_one("#artifact-options", OptionList).focus()

    def _browser(self) -> ReportBrowserApp:
        return cast(ReportBrowserApp, self.app)

    def _input(self) -> Input:
        return self.query_one("#artifact-search", Input)

    def _options(self) -> OptionList:
        return self.query_one("#artifact-options", OptionList)

    def _path_labels(self) -> list[str]:
        entries = self._browser().entries
        short = [
            (
                f"{entry.path.parent.name}/{entry.path.name}"
                if entry.path.name == "report.json"
                else entry.path.name
            )
            for entry in entries
        ]
        counts = Counter(short)
        return [
            str(entry.path) if counts[label] > 1 else label
            for entry, label in zip(entries, short, strict=True)
        ]

    def _option_prompt(self, index: int, path_label: str) -> Text:
        browser = self._browser()
        prompt = Text(f"{index + 1}. ")
        prompt.append_text(_terminal_text(path_label))
        known_name = browser.known_names.get(index)
        if known_name is not None:
            prompt.append("  ")
            prompt.append_text(_terminal_text(known_name, "bold"))
        if index in browser.report_errors or browser.entries[index].error is not None:
            prompt.append("  unavailable", style="bold")
        return prompt

    def refresh_entries(self) -> None:
        browser = self._browser()
        query = self._input().value.casefold()
        labels = self._path_labels()
        visible = [
            index
            for index, entry in enumerate(browser.entries)
            if query in str(entry.path).casefold()
            or query in browser.known_names.get(index, "").casefold()
        ]
        options = self._options()
        options.clear_options()
        options.add_options(
            Option(self._option_prompt(index, labels[index]), id=str(index))
            for index in visible
        )
        self._visible_indices = visible
        empty = self.query_one("#artifact-empty", Static)
        options.display = bool(visible)
        empty.display = not visible
        total = len(browser.entries)
        count = self.query_one("#artifact-count", Static)
        if query:
            count.update(f"{len(visible)} of {total} saved-report entries")
        else:
            count.update(f"{total} saved-report entries")
        if visible:
            try:
                options.highlighted = visible.index(browser.selected_index)
            except ValueError:
                options.highlighted = 0

    @on(Input.Changed, "#artifact-search")
    def _filter_changed(self) -> None:
        self.refresh_entries()

    @on(Input.Submitted, "#artifact-search")
    async def _input_submitted(self) -> None:
        await self._open_highlighted()

    @on(OptionList.OptionSelected, "#artifact-options")
    async def _option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            await self._browser().open_artifact(int(event.option_id))

    async def _open_highlighted(self) -> None:
        options = self._options()
        if options.highlighted is None or not self._visible_indices:
            return
        await self._browser().open_artifact(self._visible_indices[options.highlighted])

    def action_search(self) -> None:
        search = self._input()
        if search.has_focus:
            search.insert_text_at_cursor("/")
        else:
            search.focus()

    def _move_option(self, offset: int) -> None:
        search_had_focus = self._input().has_focus
        options = self._options()
        if offset < 0:
            options.action_cursor_up()
        else:
            options.action_cursor_down()
        if search_had_focus:
            self._input().focus()

    def action_previous_option(self) -> None:
        self._move_option(-1)

    def action_next_option(self) -> None:
        self._move_option(1)

    async def action_back(self) -> None:
        search = self._input()
        if search.has_focus:
            self._options().focus()
            return
        await self._browser().resume_or_quit()

    async def action_help(self) -> None:
        if self._input().has_focus:
            self._input().insert_text_at_cursor("?")
            return
        self._browser().open_help()

    async def action_quit(self) -> None:
        if self._input().has_focus:
            self._input().insert_text_at_cursor("q")
            return
        await self._browser().close_browser(interrupted=False)

    async def action_interrupt(self) -> None:
        await self._browser().close_browser(interrupted=True)


class _ReportScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding(
            "left",
            "previous_page",
            "Previous page",
            show=True,
            priority=True,
            group=_PAGE_BINDING_GROUP,
        ),
        Binding(
            "right",
            "next_page",
            "Next page",
            show=True,
            priority=True,
            group=_PAGE_BINDING_GROUP,
        ),
        Binding(
            "left_square_bracket",
            "previous_artifact",
            "Previous report",
            show=True,
            group=_REPORT_BINDING_GROUP,
        ),
        Binding(
            "right_square_bracket",
            "next_artifact",
            "Next report",
            show=True,
            group=_REPORT_BINDING_GROUP,
        ),
        Binding("up", "scroll_up", "Up", show=False),
        Binding("down", "scroll_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "scroll_home", "Beginning", show=False),
        Binding("end", "scroll_end", "End", show=False),
        Binding("s", "sections", "Sections", show=True),
        Binding(
            "escape",
            "artifacts",
            "List",
            show=True,
            key_display="Esc",
        ),
        Binding("a", "detail", "Detail", show=True),
        Binding("question_mark", "help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._section_key: SectionKey | None = None

    def compose(self) -> ComposeResult:
        with Container(id="report-header"):
            yield Static(id="report-count")
            yield Static(id="report-subject")
            yield Static(id="report-detail")
        yield Tabs(
            *(Tab(spec.short_title, id=f"tab-{spec.key}") for spec in SECTION_SPECS),
            active="tab-artifact",
            id="report-tabs",
        )
        yield Static(id="report-narrow-navigation")
        with (
            Container(id="report-content", classes="content-surface"),
            VerticalScroll(id="report-scroll"),
        ):
            yield Static(id="report-body")
        yield _ShortcutFooter()

    def on_mount(self) -> None:
        tabs = self.query_one("#report-tabs", Tabs)
        tabs.can_focus = False
        self.update_width(self.size.width)

    def on_resize(self, event: Resize) -> None:
        self.update_width(event.size.width)
        self._layout_header()

    def _browser(self) -> ReportBrowserApp:
        return cast(ReportBrowserApp, self.app)

    def _viewport(self) -> VerticalScroll:
        return self.query_one("#report-scroll", VerticalScroll)

    def _content_surface(self) -> Container:
        return self.query_one("#report-content", Container)

    def update_width(self, width: int) -> None:
        tabs = self.query_one("#report-tabs", Tabs)
        narrow_navigation = self.query_one("#report-narrow-navigation", Static)
        if self._section_key is None:
            tabs.display = False
            narrow_navigation.display = False
            return

        narrow = width < _REPORT_TABS_MIN_WIDTH
        tabs.display = not narrow
        narrow_navigation.display = narrow

        spec = ReportPresentation.section_spec(self._section_key)
        position = SECTION_KEYS.index(self._section_key)
        count = f"{position + 1}/{len(SECTION_KEYS)}"
        available_width = max(0, width - 2)
        title_width = available_width - Text(f"   {count} ").cell_len
        indicator = Text(style=f"bold {_REPORT_SELECTION_TEXT} on {_PALETTE.accent}")
        if title_width > 0:
            title = Text(spec.title)
            title.truncate(title_width, overflow="ellipsis")
            indicator.append(" ")
            indicator.append_text(title)
            indicator.append(f"  {count} ")
        else:
            indicator.append(f" {count} ")
        narrow_navigation.update(indicator)

    def _layout_header(self) -> None:
        header = self.query_one("#report-header", Container)
        count_widget = self.query_one("#report-count", Static)
        detail_widget = self.query_one("#report-detail", Static)
        count = count_widget.content
        detail = detail_widget.content
        count_width = count.cell_len if isinstance(count, Text) else 0
        detail_width = detail.cell_len if isinstance(detail, Text) else 0
        desired_width = max(count_width, detail_width) + 2
        slot_width = min(desired_width, header.content_size.width // 2)
        count_widget.styles.width = slot_width
        detail_widget.styles.width = slot_width

    def _update_header(self, header: tuple[Text, Text, Text]) -> None:
        count, subject, detail = header
        self.query_one("#report-count", Static).update(count)
        self.query_one("#report-subject", Static).update(subject)
        self.query_one("#report-detail", Static).update(detail)
        self._layout_header()

    def show_loading(self, header: tuple[Text, Text, Text]) -> None:
        self._section_key = None
        self._update_header(header)
        self.update_width(self.size.width)
        self._content_surface().border_title = "Saved report"
        self.query_one("#report-body", Static).update(Text("Loading saved report..."))
        self._viewport().scroll_to(y=0, animate=False, force=True)

    def show_error(
        self,
        header: tuple[Text, Text, Text],
        body: RenderableType,
    ) -> None:
        self._section_key = None
        self._update_header(header)
        self.update_width(self.size.width)
        self._content_surface().border_title = "Saved report unavailable"
        self.query_one("#report-body", Static).update(body)

    def show_section(
        self,
        *,
        header: tuple[Text, Text, Text],
        key: SectionKey,
        body: RenderableType,
    ) -> None:
        spec = ReportPresentation.section_spec(key)
        self._section_key = key
        self._update_header(header)
        tabs = self.query_one("#report-tabs", Tabs)
        tab_id = f"tab-{key}"
        if tabs.active != tab_id:
            tabs.active = tab_id
        self.update_width(self.size.width)
        self._content_surface().border_title = spec.title
        self.query_one("#report-body", Static).update(body)

    @on(Tabs.TabActivated, "#report-tabs")
    def _tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None or not event.tab.id:
            return
        key = event.tab.id.removeprefix("tab-")
        if key in SECTION_KEYS:
            self._browser().select_section(cast(SectionKey, key))

    def action_previous_page(self) -> None:
        self._browser().select_section_relative(-1)

    def action_next_page(self) -> None:
        self._browser().select_section_relative(1)

    def action_previous_artifact(self) -> None:
        self._browser().select_artifact_relative(-1)

    def action_next_artifact(self) -> None:
        self._browser().select_artifact_relative(1)

    def action_scroll_up(self) -> None:
        self._viewport().scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self._viewport().scroll_down(animate=False)

    def action_page_up(self) -> None:
        self._viewport().scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self._viewport().scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self._viewport().scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self._viewport().scroll_end(animate=False)

    def action_sections(self) -> None:
        self._browser().open_section_menu()

    async def action_artifacts(self) -> None:
        await self._browser().show_artifact_list()

    def action_detail(self) -> None:
        self._browser().toggle_detail()

    def action_help(self) -> None:
        self._browser().open_help()

    async def action_quit(self) -> None:
        await self._browser().close_browser(interrupted=False)

    async def action_interrupt(self) -> None:
        await self._browser().close_browser(interrupted=True)


class _SectionMenu(ModalScreen[SectionKey | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", show=False, priority=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
    ]

    def __init__(self, selected: SectionKey) -> None:
        super().__init__()
        self._selected = selected

    def compose(self) -> ComposeResult:
        with Container(id="section-dialog"):
            yield Static("Report sections", id="section-title")
            yield OptionList(
                *(
                    Option(
                        f"{index + 1}. {spec.title}",
                        id=spec.key,
                    )
                    for index, spec in enumerate(SECTION_SPECS)
                ),
                id="section-options",
            )
            yield Static("Enter opens  ·  Escape cancels", id="section-hint")

    def on_mount(self) -> None:
        options = self.query_one("#section-options", OptionList)
        options.highlighted = SECTION_KEYS.index(self._selected)
        options.focus()

    @on(OptionList.OptionSelected, "#section-options")
    def _selected_option(self, event: OptionList.OptionSelected) -> None:
        if event.option_id in SECTION_KEYS:
            self.dismiss(cast(SectionKey, event.option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)

    async def action_interrupt(self) -> None:
        await cast(ReportBrowserApp, self.app).close_browser(interrupted=True)


class _HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", show=False, priority=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        controls = Table.grid(padding=(0, 2))
        controls.add_column(style="bold", no_wrap=True)
        controls.add_column(ratio=1, overflow="fold")
        for key, purpose in (
            ("Left / Right", "Previous / next report section"),
            ("[ / ]", "Previous / next saved report"),
            ("s", "Open the report-section menu"),
            ("Escape", "Return to the full saved-report list"),
            ("Up / Down", "Scroll a report; move in a list"),
            ("PgUp / PgDn", "Scroll one report page"),
            ("Home / End", "Beginning / end of the current section"),
            ("/", "Focus saved-report search"),
            ("Enter", "Open the selected report or section"),
            ("a", "Toggle compact / all saved detail"),
            ("?", "Show this help"),
            ("q", "Quit"),
            ("Ctrl-C", "Interrupt and return status 130"),
        ):
            controls.add_row(key, purpose)
        with Container(id="help-dialog"):
            yield Static("Report browser controls", id="help-title")
            yield Static(controls, id="help-controls")
            yield Static("Escape closes help", id="help-hint")

    def action_close(self) -> None:
        self.dismiss(None)

    async def action_interrupt(self) -> None:
        await cast(ReportBrowserApp, self.app).close_browser(interrupted=True)


class ReportBrowserApp(App[int]):
    """Browse admitted saved checker reports without reopening their artifacts."""

    ENABLE_COMMAND_PALETTE = False
    TITLE = "ReproReady"
    CSS = (
        f"$report-accent: {_PALETTE.accent};\n"
        f"$report-border: {_PALETTE.box_border};\n"
        f"$report-selection-text: {_REPORT_SELECTION_TEXT};\n"
        + """
    Screen {
        background: ansi_default;
        color: ansi_default;
    }

    #artifact-title {
        width: 100%;
        height: 1;
        padding: 0 1;
        text-style: bold;
        background: ansi_default;
        color: ansi_default;
    }

    #report-header {
        layout: horizontal;
        width: 100%;
        height: 1;
        padding: 0 1;
        overflow: hidden;
        background: ansi_default;
        color: ansi_default;
    }

    #report-count, #report-subject, #report-detail {
        height: 1;
        text-wrap: nowrap;
        overflow: hidden;
        background: ansi_default;
    }

    #report-count {
        text-align: left;
    }

    #report-subject {
        width: 1fr;
        min-width: 0;
        text-align: center;
        text-overflow: ellipsis;
        text-style: bold;
        color: $report-accent;
    }

    #report-detail {
        text-align: right;
    }

    #artifact-title {
        margin-top: 1;
    }
    .content-surface {
        width: 100%;
        height: 1fr;
        border: none;
        border-top: solid $report-border;
        background: ansi_default;
        color: ansi_default;
    }

    #report-content {
        border-title-align: left;
        border-title-background: ansi_default;
        border-title-color: $report-accent;
        border-title-style: bold;
    }


    #artifact-purpose {
        height: auto;
        padding: 1 1 0 1;
    }

    #artifact-search {
        height: 3;
        margin: 1 1 0 1;
        background: ansi_default;
        color: ansi_default;
        border: solid ansi_default;
    }

    #artifact-search:focus {
        border: double ansi_default;
    }

    #artifact-count {
        height: 1;
        margin: 0 1;
        color: ansi_default;
        text-style: dim;
    }

    #artifact-options {
        height: 1fr;
        margin: 0 1;
        background: ansi_default;
        color: ansi_default;
        border: none;
    }

    #artifact-options > .option-list--option-highlighted,
    #section-options > .option-list--option-highlighted {
        background: ansi_default;
        color: ansi_default;
        text-style: reverse bold;
    }

    #artifact-empty {
        height: 1fr;
        content-align: center middle;
    }

    #artifact-hint {
        height: 1;
        margin: 0 1;
        text-align: center;
    }

    #report-tabs {
        width: 100%;
        height: 3;
        margin: 1 0 0 0;
        background: ansi_default;
        color: ansi_default;
    }

    #report-tabs #tabs-list {
        align-horizontal: center;
    }

    #report-tabs Underline {
        display: none;
    }

    #report-tabs Tab {
        height: 3;
        padding: 0 1;
        margin: 0 1 0 0;
        border: solid $report-border;
        background: ansi_default;
        color: ansi_default;
        text-style: not dim;
    }

    #report-tabs #tab-saved {
        margin-right: 0;
    }

    #report-tabs Tab.-active {
        border: solid $report-accent;
        background: $report-accent;
        color: $report-selection-text;
        text-style: not dim bold;
    }

    #report-narrow-navigation {
        display: none;
        width: 100%;
        height: 1;
        margin: 1 0 0 0;
        padding: 0 1;
        text-align: center;
        text-wrap: nowrap;
        background: ansi_default;
        color: ansi_default;
    }


    #report-scroll {
        height: 1fr;
        width: 100%;
        overflow-x: hidden;
        overflow-y: auto;
        background: ansi_default;
        color: ansi_default;
    }

    #report-body {
        width: 100%;
        height: auto;
        padding: 0 1 1 1;
        background: ansi_default;
        color: ansi_default;
    }

    _ShortcutFooter {
        dock: bottom;
        width: 100%;
        height: auto;
        min-height: 1;
        padding: 0 1;
        overflow: hidden;
        text-wrap: nowrap;
        background: ansi_default;
        color: ansi_default;
    }

    _SectionMenu, _HelpScreen {
        align: center middle;
        background: ansi_default 70%;
        color: ansi_default;
    }

    #section-dialog, #help-dialog {
        width: 80%;
        max-width: 100;
        height: 80%;
        padding: 1 2;
        border: solid $report-border;
        background: ansi_default;
        color: ansi_default;
    }

    #help-dialog {
        height: auto;
        max-height: 90%;
    }

    #section-title, #help-title {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    #section-options {
        height: 1fr;
        background: ansi_default;
        color: ansi_default;
        border: none;
    }

    #section-hint, #help-hint {
        height: 1;
        margin-top: 1;
        text-align: center;
    }

    #help-controls {
        height: auto;
    }
    """
    )

    def __init__(
        self,
        entries: Sequence[SavedReportEntry],
        *,
        show_all: bool = False,
    ) -> None:
        if not entries:
            raise ValueError("at least one saved report entry is required")
        super().__init__(ansi_color=None)
        self.entries = tuple(entries)
        self.report_errors: dict[int, SavedReportError] = {}
        self.selected_index = 0
        self.known_names: dict[int, str] = {}
        self._generation = 0
        self._pending_load: tuple[int, int] | None = None
        self._loader_task: asyncio.Task[None] | None = None
        self._shutting_down = False
        self._validation_cache: dict[Path, bytes] = {}
        self._presentation: ReportPresentation | None = None
        self._current_error: SavedReportError | None = None
        self._page_keys: list[SectionKey] = ["artifact" for _entry in entries]
        self._detail_modes = [show_all for _entry in entries]
        self._scroll_positions: list[dict[tuple[SectionKey, bool], float]] = [
            {} for _entry in entries
        ]
        self._artifact_screen = _ArtifactListScreen()
        self._report_screen: _ReportScreen | None = None
        self._has_opened_report = False
        self.load_idle = asyncio.Event()
        self.animation_level = "none"

    async def on_mount(self) -> None:
        self.theme = "ansi-dark"
        await self.push_screen(self._artifact_screen)
        if len(self.entries) == 1:
            await self.open_artifact(0)

    def _header_content(self, subject: object) -> tuple[Text, Text, Text]:
        mode = (
            "All saved detail" if self._detail_modes[self.selected_index] else "Compact"
        )
        return (
            Text(f"Report {self.selected_index + 1} of {len(self.entries)}"),
            _terminal_text(subject, _PALETTE.accent),
            Text(mode),
        )

    def _viewport(self) -> VerticalScroll:
        if self._report_screen is None:
            raise RuntimeError("report screen is not open")
        return self._report_screen.query_one("#report-scroll", VerticalScroll)

    def _save_scroll(self) -> None:
        if (
            self._report_screen is None
            or not self._report_screen.is_mounted
            or self._presentation is None
        ):
            return
        key = self._page_keys[self.selected_index]
        mode = self._detail_modes[self.selected_index]
        self._scroll_positions[self.selected_index][(key, mode)] = (
            self._viewport().scroll_y
        )

    def _restore_scroll(self) -> None:
        if self._report_screen is None:
            return
        index = self.selected_index
        key = self._page_keys[index]
        mode = self._detail_modes[index]
        target = self._scroll_positions[index].get((key, mode), 0.0)

        def restore() -> None:
            if self._report_screen is None or not self._report_screen.is_mounted:
                return
            viewport = self._viewport()
            viewport.scroll_to(
                y=min(target, viewport.max_scroll_y),
                animate=False,
                force=True,
            )

        self.call_after_refresh(restore)

    async def open_artifact(self, index: int) -> None:
        if self._shutting_down or not 0 <= index < len(self.entries):
            return
        if self._report_screen is None:
            self._report_screen = _ReportScreen()
            await self.push_screen(self._report_screen)
        self._has_opened_report = True
        self._request_index(index)

    async def show_artifact_list(self) -> None:
        if self._report_screen is None:
            return
        self._save_scroll()
        self._generation += 1
        self._pending_load = None
        self._presentation = None
        self._current_error = None
        await self.pop_screen()
        self._report_screen = None
        self._artifact_screen.refresh_entries()
        self.call_after_refresh(
            lambda: self._artifact_screen.query_one(
                "#artifact-options", OptionList
            ).focus()
        )

    async def resume_or_quit(self) -> None:
        if self._has_opened_report:
            await self.open_artifact(self.selected_index)
        else:
            await self.close_browser(interrupted=False)

    def _show_loading(self) -> None:
        if self._report_screen is None:
            return
        entry = self.entries[self.selected_index]
        self._report_screen.show_loading(self._header_content(entry.path.name))

    def _request_index(self, index: int) -> None:
        if (
            self._shutting_down
            or self._report_screen is None
            or not 0 <= index < len(self.entries)
        ):
            return
        self._save_scroll()
        self.selected_index = index
        self._generation += 1
        self._pending_load = (index, self._generation)
        self._presentation = None
        self._current_error = None
        self.load_idle.clear()
        self._show_loading()
        if self._loader_task is None or self._loader_task.done():
            self._loader_task = asyncio.create_task(self._load_coordinator())

    def select_artifact_relative(self, offset: int) -> None:
        target = self.selected_index + offset
        if 0 <= target < len(self.entries):
            self._request_index(target)

    def select_section_relative(self, offset: int) -> None:
        if self._presentation is None or self._current_error is not None:
            return
        current = SECTION_KEYS.index(self._page_keys[self.selected_index])
        target = current + offset
        if 0 <= target < len(SECTION_KEYS):
            self.select_section(SECTION_KEYS[target])

    def select_section(self, key: SectionKey) -> None:
        if self._presentation is None or self._current_error is not None:
            return
        if key == self._page_keys[self.selected_index]:
            return
        self._save_scroll()
        self._page_keys[self.selected_index] = key
        self._render_current()

    async def _load_coordinator(self) -> None:
        try:
            while not self._shutting_down and self._pending_load is not None:
                index, generation = self._pending_load
                self._pending_load = None
                entry = self.entries[index]
                report: dict[str, object] | None = None
                error: SavedReportError | None = None
                if entry.error is not None:
                    error = SavedReportError(entry.error.code, entry.error.message)
                else:
                    try:
                        report = await asyncio.to_thread(
                            load_saved_report,
                            entry.path,
                            validation_cache=self._validation_cache,
                        )
                    except SavedReportError as caught:
                        error = SavedReportError(caught.code, caught.message)
                    except Exception:  # noqa: BLE001 - fixed viewer error boundary
                        error = SavedReportError(
                            "internal_error",
                            "An internal failure prevented the saved report from being displayed.",
                        )
                if (
                    self._shutting_down
                    or generation != self._generation
                    or self._report_screen is None
                ):
                    continue
                if error is not None:
                    self._install_error(index, error)
                elif report is not None:
                    self._install_report(index, report)
        finally:
            self._loader_task = None
            if self._pending_load is None:
                self.load_idle.set()

    def _install_error(self, index: int, error: SavedReportError) -> None:
        stored = SavedReportError(error.code, error.message)
        self.report_errors[index] = stored
        self._presentation = None
        self._current_error = stored
        rows = Table.grid(padding=(0, 2))
        rows.add_column(style="bold", no_wrap=True)
        rows.add_column(ratio=1, overflow="fold")
        rows.add_row("Path", _terminal_text(self.entries[index].path))
        rows.add_row("Error", _terminal_text(stored.message))
        rows.add_row("Code", _terminal_text(stored.code))
        helper = (
            Text("Other saved-report entries remain available.")
            if len(self.entries) > 1
            else Text("")
        )
        body = Panel(
            Group(rows, Text(""), helper),
            title="Saved-file error",
            title_align="left",
            padding=(0, 1),
            expand=True,
        )
        if self._report_screen is not None:
            self._report_screen.show_error(
                self._header_content(self.entries[index].path.name),
                body,
            )
        self._restore_scroll()
        self._artifact_screen.refresh_entries()

    def _install_report(self, index: int, report: dict[str, object]) -> None:
        try:
            presentation = ReportPresentation(
                report,
                report_path=self.entries[index].path,
                interactive=True,
            )
        except Exception:  # noqa: BLE001 - fixed viewer error boundary
            self._install_error(
                index,
                SavedReportError(
                    "internal_error",
                    "An internal failure prevented the saved report from being displayed.",
                ),
            )
            return
        self._presentation = presentation
        self._current_error = None
        self.report_errors.pop(index, None)
        self.known_names[index] = presentation.artifact_name
        self._artifact_screen.refresh_entries()
        self._render_current()

    def _render_current(self) -> None:
        presentation = self._presentation
        if presentation is None or self._report_screen is None:
            return
        key = self._page_keys[self.selected_index]
        try:
            renderable = presentation.render_section(
                key,
                show_all=self._detail_modes[self.selected_index],
            )
        except Exception:  # noqa: BLE001 - fixed viewer error boundary
            self._presentation = None
            self._install_error(
                self.selected_index,
                SavedReportError(
                    "internal_error",
                    "An internal failure prevented the saved report from being displayed.",
                ),
            )
            return
        subject = self.known_names.get(
            self.selected_index, self.entries[self.selected_index].path.name
        )
        self._report_screen.show_section(
            header=self._header_content(subject),
            key=key,
            body=cast(RenderableType, renderable),
        )
        self._restore_scroll()

    def toggle_detail(self) -> None:
        if self._presentation is None or self._current_error is not None:
            return
        self._save_scroll()
        self._detail_modes[self.selected_index] = not self._detail_modes[
            self.selected_index
        ]
        self._render_current()

    def open_section_menu(self) -> None:
        if self._presentation is None or self._current_error is not None:
            return
        menu = _SectionMenu(self._page_keys[self.selected_index])

        def selected(key: SectionKey | None) -> None:
            if key is not None:
                self.select_section(key)

        self.push_screen(menu, selected)

    def open_help(self) -> None:
        self.push_screen(_HelpScreen())

    def _normal_status(self) -> int:
        if any(error.code == "internal_error" for error in self.report_errors.values()):
            return 1
        if self.report_errors:
            return 2
        return 0

    async def close_browser(self, *, interrupted: bool) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._pending_load = None
        loader_task = self._loader_task
        if loader_task is not None and loader_task is not asyncio.current_task():
            await asyncio.shield(loader_task)
        self.exit(130 if interrupted else self._normal_status())
