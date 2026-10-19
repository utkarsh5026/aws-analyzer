"""
The chat window's Settings tab.
"""

from __future__ import annotations

import json
from typing import Any

from ..._kit.errors import _Hint
from ..._kit.render import _prose
from ..._kit.text import _esc

from ..helpers import _norm
from ..analysis.schema import Field
from ..analysis.request import _retrieve_path, coerce_setting, describe_setting
from ..blocks import _QUICK, _kind_text, _wrap


_SHOWN_MATCHES = 6  # settings listed under the search box; Browse all lists every one

_BESIDE = ("integer", "float", "boolean", "choice")  # kinds whose box sits beside the setting's name


class _SettingsTab:
    """
    The chat window's Settings tab: a line per setting sent, and Add a setting with its search.
    """

    # ----------------------------------------------------------------- settings

    def _input(self, f: Field, value: Any) -> Any:
        w, layout = self.w, self.w.Layout
        full = layout(width="56%", flex="0 0 auto", min_width="0") if f.kind in _BESIDE else layout(
            width="100%", min_width="0")
        if f.kind == "integer":
            low = int(f.low) if f.low is not None else -(2**31)
            high = int(f.high) if f.high is not None else 2**31 - 1
            start = value if value is not None else f.default if f.default is not None else max(low, 1)
            return w.BoundedIntText(value=int(start), min=low, max=high, layout=full)
        if f.kind == "float":
            low = f.low if f.low is not None else -1e9
            high = f.high if f.high is not None else 1e9
            start = float(value if value is not None else f.default if f.default is not None else low)
            if high - low <= 2:
                return w.FloatSlider(value=start, min=low, max=high, step=0.01, readout_format=".2f",
                                     continuous_update=False, layout=full)
            return w.BoundedFloatText(value=start, min=low, max=high, layout=full)
        if f.kind == "boolean":
            return w.Checkbox(value=bool(value), description="on" if value else "off", indent=False, layout=full)
        if f.kind == "choice":
            return w.Dropdown(options=list(f.choices), value=value if value in f.choices else f.choices[0],
                              layout=full)
        text = "" if value is None else self._as_text(f, value)
        if f.key == "reranker" and getattr(w, "Combobox", None):
            return w.Combobox(value=text, options=("cohere", "amazon"), placeholder=f.placeholder,
                              continuous_update=False, layout=full)
        if f.kind in ("long_text", "json", "list"):
            lines = text.count("\n") + 1
            rows = min(14, max(lines, {"long_text": 8, "json": 4, "list": 3}[f.kind]))
            box = w.Textarea(value=text, rows=rows, placeholder=f.placeholder, continuous_update=False, layout=full)
            if f.kind == "json":
                box.add_class("kbc-mono")
            return box
        return w.Text(value=text, placeholder=f.placeholder, continuous_update=False, layout=full)

    @staticmethod
    def _as_text(f: Field, value: Any) -> str:
        if f.kind == "list":
            return "\n".join(map(str, value))
        if f.kind == "json":
            return json.dumps(value, indent=2, ensure_ascii=False)
        return str(value)

    def _row(self, key: str) -> Any:
        """
        A setting's line: its name, the box holding its value (beside the name when it's short, under it
        otherwise), ✕, and what the value means. Hovering the name says what the setting does, what it takes and
        where it goes in the request.
        """
        w, layout = self.w, self.w.Layout
        f = self.schema.fields[key]
        tip = f"{f.doc}\n\nTakes: {_kind_text(f)}\nSent as: {'.'.join(f.path)}"
        if key != ".".join(f.path):
            tip += f"\nIn code: {key}"
        kind = "" if f.kind in _BESIDE else f'<span class="rk">{_esc(_kind_text(f))}</span>'
        head = w.HTML(_wrap(f'<div class="rh" title="{_esc(tip)}"><b>{_esc(f.label)}</b>{kind}</div>'),
                      layout=layout(flex="1 1 auto", min_width="0"))
        remove = w.Button(description="✕", tooltip=f"Stop sending {key}", layout=layout(width="22px", flex="0 0 auto"))
        remove.add_class("kbc-x")
        remove.on_click(self._safely(lambda _button, key=key: self._remove(key)))
        value_box = self._input(f, self.view.values.get(key))
        value_box.observe(self._safely(lambda change, key=key: self._edited(key, change["new"])), names="value")
        note = w.HTML(layout=layout(width="100%"))
        line = layout(width="100%", align_items="center")
        if f.kind in _BESIDE:
            parts = [w.HBox([head, value_box, remove], layout=line), note]
        else:
            parts = [w.HBox([head, remove], layout=line), value_box, note]
        row = w.VBox(parts, layout=layout(width="100%"))
        row.add_class("kbc-row")
        self.inputs[key], self.row_notes[key], self.removes[key] = value_box, note, remove
        return row

    def _off(self, key: str) -> bool:
        """
        Whether a setting is left out because the window is on Retrieve only (it's for the answer).
        """
        return self.view.retrieve_only and _retrieve_path(self.schema.fields[key].path) is None

    def _note_row(self, key: str) -> None:
        f = self.schema.fields[key]
        if key in self.broken:
            text, css = f"Not sent: {_esc(self.broken[key])}", "rp bad"
        elif key in self.pending:
            text, css = "Not sent until you fill it in.", "rp pending"
        elif self._off(key):
            text, css = "Not sent with Retrieve only: it's for the answer.", "rp off"
        else:
            text, css = _esc(describe_setting(f, self.view.values.get(key))), "rp"
        self._set(self.row_notes[key], _wrap(f'<div class="{css}">{text}</div>'))
        box = self.inputs[key]
        if f.kind == "boolean":
            self._quietly(box, description="on" if box.value else "off")
        row = self.rows.get(key)
        if row is not None:
            for css_class, on in (("kbc-broken", key in self.broken), ("kbc-pending", key in self.pending),
                                  ("kbc-fresh", key == self.fresh), ("kbc-off", self._off(key))):
                if on and css_class not in row._dom_classes:
                    row.add_class(css_class)
                elif not on and css_class in row._dom_classes:
                    row.remove_class(css_class)

    def _sync_rows(self) -> None:
        """
        Cards for the settings that are set or being filled in, grouped, in the schema's order. Existing cards are
        kept (with whatever their boxes hold), so a half-typed value isn't lost.
        """
        keys = [k for k in self.schema.fields if k in self.view.values or k in self.pending or k in self.broken]
        for key in keys:
            if key not in self.rows:
                self.rows[key] = self._row(key)
                self._note_row(key)
            elif key in self.view.values and key not in self.broken:
                self._show_value(key, self.view.values[key])
            else:
                self._note_row(key)
        for key in [k for k in self.rows if k not in keys]:
            for store in (self.rows, self.inputs, self.row_notes, self.removes):
                store.pop(key).close()
        children: list[Any] = []
        group = None
        for key in keys:
            f = self.schema.fields[key]
            if f.group != group:
                group = f.group
                if group not in self.headers:
                    self.headers[group] = self.w.HTML()
                off = self.view.retrieve_only and group != "Retrieval"
                self._set(self.headers[group], _wrap(f'<div class="gh">{_esc(group)}'
                                                     + (" · not sent with Retrieve only" if off else "") + "</div>"))
                children.append(self.headers[group])
            children.append(self.rows[key])
        if not keys:
            children = [self.w.HTML(_wrap('<div class="more" style="margin:6px 0 2px">Nothing is set, so Bedrock '
                                          "uses its defaults.</div>"))]
        self.rows_box.children = children
        self._sync_chips()
        self._show_matches()

    def _show_value(self, key: str, value: Any) -> None:
        """
        Puts a setting's value in its box, unless the box already holds it (in the user's own formatting).
        """
        f, box = self.schema.fields[key], self.inputs[key]
        try:
            same = box.value not in ("", None) and coerce_setting(f, box.value) == value
        except ValueError:
            same = False
        if not same:
            shown = value if f.kind in ("integer", "float", "boolean", "choice") else self._as_text(f, value)
            self._quietly(box, value=shown)
        self._note_row(key)

    def _sync_chips(self) -> None:
        w = self.w
        shown = []
        for key in _QUICK:
            if key not in self.schema.fields or key in self.rows:
                continue
            if key not in self.chips:
                f = self.schema.fields[key]
                chip = w.Button(description=f"+ {f.label}", tooltip=f.doc, layout=w.Layout(width="auto"))
                chip.add_class("kbc-chip")
                chip.on_click(self._safely(lambda _button, key=key: self._add_setting(key)))
                self.chips[key] = chip
            shown.append(self.chips[key])
        self.chip_box.children = shown

    def _edited(self, key: str, raw: Any) -> None:
        if self.quiet:
            return
        f = self.schema.fields[key]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            self.view.values.pop(key, None)
            self.broken.pop(key, None)
            self.pending.add(key)
        else:
            try:
                value = coerce_setting(f, raw)
            except ValueError as exc:
                self.view.values.pop(key, None)
                self.pending.discard(key)
                self.broken[key] = str(exc)
            else:
                self.broken.pop(key, None)
                self.pending.discard(key)
                self.view.values[key] = value
                self.view.values = {k: self.view.values[k] for k in self.schema.fields if k in self.view.values}
        self._note_row(key)
        self._refresh()

    def _remove(self, key: str) -> None:
        self.view.values.pop(key, None)
        self.pending.discard(key)
        self.broken.pop(key, None)
        self.fresh = None if self.fresh == key else self.fresh
        self._sync_rows()
        self._refresh()
        self._set_status(f"{self.schema.fields[key].label} isn't sent any more.", "ok")

    def _add_setting(self, key: str) -> None:
        f = self.schema.fields[key]
        if key not in self.view.values and key not in self.pending and key not in self.broken:
            if f.default is None:
                self.pending.add(key)
            else:
                self.view.values[key] = coerce_setting(f, f.default)
                self.view.values = {k: self.view.values[k] for k in self.schema.fields if k in self.view.values}
        before, self.fresh = self.fresh, key
        if before in self.rows:
            self._note_row(before)
        self._sync_rows()
        self._refresh()
        what = describe_setting(f, self.view.values.get(key))
        self._set_status(f"Added {f.label}. {what} Change it in place; ✕ removes it.", "ok")

    # ------------------------------------------------- finding a setting to add

    def _show_adding(self, show: bool) -> None:
        """
        Opens Add a setting in place of its button (with the cursor in the search box), or folds it away again,
        forgetting the search.
        """
        self.adding.layout.display = "" if show else "none"
        self.add_button.layout.display = "none" if show else ""
        if show:
            if hasattr(self.add_name, "focus"):  # ipywidgets 8
                self.add_name.focus()
            return
        self.browsing = False
        self._quietly(self.add_name, value="")
        self._show_matches()

    @staticmethod
    def _mentions(f: Field, text: str) -> bool:
        """
        Whether every word of the search is in the field's names, path or description (not a near miss).
        """
        names = _norm(" ".join([f.key, f.label, *f.names, ".".join(f.path)]))
        return all(_norm(word) in names or word in f.doc.lower() for word in text.lower().split())

    def _pick(self, key: str) -> Any:
        """
        A setting's line in the list under the search box: what it is and does, and a button that adds it.
        """
        if key not in self.picks:
            w, layout, f = self.w, self.w.Layout, self.schema.fields[key]
            where = f.key if f.key == f.where else f"{f.key} · {f.where}"
            info = w.HTML(_wrap(f'<div class="fc" title="{_esc(f.doc)}"><div class="fl"><b>{_esc(f.label)}</b>'
                                f'<span class="rk">{_esc(_kind_text(f))}</span></div><div class="fd">{_esc(f.doc)}'
                                f'</div><div class="fw">{_esc(where)}</div></div>'),
                          layout=layout(flex="1 1 auto", min_width="0"))
            button = w.Button(description="+ Add", tooltip=f"Send {f.label} with every question",
                              layout=layout(width="auto", flex="0 0 auto"))
            button.add_class("kbc-small")
            button.on_click(self._safely(lambda _button, key=key: self._add_listed(key)))
            row = w.HBox([info, button], layout=layout(width="100%", align_items="center"))
            row.add_class("kbc-pick")
            self.picks[key] = (row, button)
        row, button = self.picks[key]
        added = key in self.rows
        if button.disabled != added:
            button.description, button.disabled = ("✓ Added", True) if added else ("+ Add", False)
        return row

    def _show_matches(self) -> None:
        """
        The list under the search box: the settings the search matches (the best few), or every setting by group
        while Browse all is on. Nothing while the box is empty.
        """
        text = str(self.add_name.value or "").strip()
        self.browse_button.description = "Hide the list" if self.browsing else f"Browse all {len(self.schema.fields)}"
        if not text and not self.browsing:
            self.results.children = ()
            self._set(self.add_help, "")
            return
        found = self.schema.search(text)
        help_text = ""
        if text and not found:
            try:
                self.schema.find(text)
            except ValueError as exc:
                help_text = str(exc)
        elif text and not self._mentions(found[0], text):
            labels = " or ".join(dict.fromkeys(f.label for f in found[:3]))
            help_text = f"No setting matches {text!r}. Did you mean {labels}?"
        shown = found if self.browsing else found[:_SHOWN_MATCHES]
        children: list[Any] = []
        group = None
        for f in shown:
            if self.browsing and not text and f.group != group:
                group = f.group
                if group not in self.pick_headers:
                    self.pick_headers[group] = self.w.HTML(_wrap(f'<div class="gh">{_esc(group)}</div>'))
                children.append(self.pick_headers[group])
            children.append(self._pick(f.key))
        if len(found) > len(shown):
            more = len(found) - len(shown)
            children.append(self.w.HTML(_wrap(f'<div class="more" style="margin:4px 0 0 10px">{more:,} more match: '
                                              "type more of the name, or Browse all.</div>")))
        self.results.children = children
        self._set(self.add_help, _wrap(f'<div class="rp" style="margin:6px 2px 0">{_prose(help_text)}</div>')
                  if help_text else "")

    def _typed(self, change: dict[str, Any]) -> None:
        self._show_matches()

    def _browse(self, *_: Any) -> None:
        self.browsing = not self.browsing
        self._show_matches()

    def _add_listed(self, key: str) -> None:
        self._add_setting(key)
        self._show_matches()

    def _add_typed(self, *_: Any) -> None:
        """
        Enter in the search box, or Add: adds the setting the text names, else the best match.
        """
        text = str(self.add_name.value or "").strip()
        if not text:
            self._set_status("Type a setting's name, or what it does, in the box; Browse all lists every one.")
            return
        try:
            f = self.schema.find(text)
        except ValueError as exc:
            found = self.schema.search(text)
            if not found or not self._mentions(found[0], text):
                raise _Hint(str(exc)) from None
            f = found[0]
        self._quietly(self.add_name, value="")
        self._add_setting(f.key)
