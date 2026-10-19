"""
The chat window's Request, Response and Code tabs.
"""

from __future__ import annotations

import json
from typing import Any

from ..._bedrock.blocks import _json_html
from ..._kit.fmt import _plural
from ..._kit.highlight import _python_html
from ..._kit.render import _SELECT, _ordered, _prose
from ..._kit.text import _esc

from ..helpers import _diff
from ..analysis.request import retrieve_settings, settings_from_request
from ..analysis.findings import settings_findings
from ..analysis.batches import parse_questions
from ..analysis.code import cli_command, config_of, python_call, python_script
from ..blocks import _json_text_html, _setting_marks, _shell_html, _wrap

_PYTHON_WIDTH = 64  # where the Request tab's Python breaks lines, so it fits the tab


class _RequestTab:
    """
    The chat window's Request, Response and Code tabs: the JSON sent and returned, Edit JSON, and the setup as
    code to copy.
    """

    def _code_tab(self) -> Any:
        """
        Code: the setup as a Python script, its JSON, or an AWS CLI command, following every change.
        """
        w, layout = self.w, self.w.Layout
        self.code_mode = w.ToggleButtons(options=["Python", "JSON", "AWS CLI"], value="Python",
                                         tooltips=["A script that asks your test questions with this setup (boto3 "
                                                   "only)", "This setup as the API's own JSON: the request without "
                                                   "the question", "One question from a terminal, with the AWS CLI"],
                                         style={"button_width": "74px"}, layout=layout(margin="0 0 6px 0"))
        self.code_mode.observe(self._safely(lambda _change: self._render_code()), names="value")
        self.code_view = w.HTML(layout=layout(width="100%"))
        head = w.HTML(_wrap('<div class="ph">Use this setup in your code<span class="hint">it follows every change'
                            "</span></div>"))
        return w.VBox([head, self.code_mode, self.code_view], layout=layout(width="100%"))

    # --------------------------------------------------------- the JSON views

    def _refresh(self) -> None:
        """
        Everything that depends on the settings: the title (the knowledge base's name), findings, the next request,
        the setup line.
        """
        view = self.view
        name = view.core.kb_name(view.kb) if view.kb else "no knowledge base yet"
        self._set(self.title, _wrap(f'<div class="whd"><span class="av">\u203a</span><h3>{_esc(name)}</h3></div>'))
        params, problems = view._preview()
        found = [("warn", f"Bedrock would refuse this request: {p}") for p in problems]
        found += [("warn", f"{key} isn't sent: {why}") for key, why in self.broken.items()]
        sent = retrieve_settings(view.values, self.schema) if view.retrieve_only else view.values
        found += settings_findings(sent, view.model or "")
        notes = "".join(f'<div class="note {level}">{_prose(message)}</div>' for level, message in _ordered(found))
        self._set(self.findings, _wrap(f'<div style="margin:0 0 10px">{notes}</div>') if notes else "")
        self._set(self.setup, _wrap(f'<details class="setup"><summary>Open this setup again<span class="hint">the '
                                    f"chat(...) call, to paste into another notebook</span></summary><pre "
                                    f'class="code hl"{_SELECT}>{_python_html(view._setup_call())}</pre></details>'))
        self._params = params
        self._render_request()
        if self.editing:
            self._follow_edit()
        if self.varying_card.layout.display != "none":  # the chips follow Answer / Retrieve only
            self._draw_vary_chips()
        self._draw_test_note()
        self._render_code()

    def _render_request(self) -> None:
        params, view = self._params, self.view
        if params is None:
            return
        mode = self.request_mode.value
        if mode == "Python":
            body = f'<pre class="code hl"{_SELECT}>{_python_html(python_call(params, view._region(), _PYTHON_WIDTH))}</pre>'
            hint = "The same call with boto3: click it to select all, then copy."
        elif mode == "JSON":
            body = f'<pre class="code hl"{_SELECT}>{_json_text_html(params)}</pre>'
            hint = "As JSON text: click it to select all, then copy."
        else:
            body = _json_html(params, marks=view._marks(), notes=view._json_notes())
            hint = "Highlighted: your settings. Click ▸ to fold a part."
        if view.retrieve_only:
            lead = "The request your next question sends: Retrieve, the search only, with no answer"
        else:
            lead = "The request your next question sends" + (", continuing this conversation" if view.session_id
                                                             else "")
        self._set(self.request_view, _wrap(f'<div class="pd">{_esc(lead)}. {_esc(hint)}</div>{body}'))

    def _render_response(self) -> None:
        if not self.view.answers:
            self._set(self.response_view, _wrap('<div class="more" style="margin:10px 0">Nothing yet: ask a question, '
                                                "and what Bedrock sends back shows here as JSON.</div>"))
            return
        a = self.view.answers[-1]
        how = ("Retrieve" if a.retrieve_only else "streamed: built from the stream's events" if a.streamed
               else "RetrieveAndGenerate")
        meta = f"{'Search' if a.retrieve_only else 'Answer'} {len(self.view.answers)} · {a.seconds:.1f}s · {how}"
        if self.response_mode.value == "Request sent":
            body = _json_html(a.request, marks=_setting_marks([k for k in a.settings if k != "reranker"], self.schema,
                                                              a.retrieve_only))
        else:
            body = _json_html(a.response, open_depth=3)
        self._set(self.response_view, _wrap(f'<div class="pd" style="margin-top:8px">{_esc(meta)}</div>{body}'))

    def _request_text(self) -> str:
        return json.dumps(self._params or {}, indent=2, ensure_ascii=False)

    def _fill_editor(self, note: str = "") -> None:
        self.edit_base = self._request_text()
        self._quietly(self.editor, value=self.edit_base)
        self._set(self.edit_message, _wrap(note or '<div class="pd">Change anything, add or delete fields, then Apply. '
                                           "The knowledge base, the model and the settings follow what you write; the "
                                           "question here is only a placeholder.</div>"))

    def _edit(self, *_: Any) -> None:
        """
        Opens the editor on the request as it is now. The view buttons wait until it's closed: the editor is
        the view while it's open.
        """
        self.editing = True
        self._fill_editor()
        self.edit_box.layout.display = ""
        self.request_view.layout.display = "none"
        self.edit_button.disabled = self.request_mode.disabled = True

    def _restart_edit(self, *_: Any) -> None:
        self._fill_editor()

    def _cancel_edit(self, *_: Any) -> None:
        self.editing = False
        self.edit_box.layout.display = "none"
        self.request_view.layout.display = ""
        self.edit_button.disabled = self.request_mode.disabled = False

    def _follow_edit(self) -> None:
        """
        The request changed while the editor is open (a setting, the model, an answer that started a session).
        An untouched editor takes the new request; an edited one keeps the edits and says what Apply would undo.
        """
        now = self._request_text()
        if now == self.edit_base:
            return
        if self.editor.value == self.edit_base:
            self._fill_editor()
            return
        changes = []
        try:
            picked, settings = settings_from_request(json.loads(self.edit_base), self.schema)
            now, _ = settings_from_request(self._params or {}, self.schema)
            sent = retrieve_settings(self.view.values, self.schema) if picked.get("retrieve_only") else self.view.values
            changes = _diff(settings, sent)
            for label, key in (("the knowledge base", "knowledgeBaseId"), ("the model", "modelArn"),
                               ("the conversation", "sessionId"), ("Answer / Retrieve only", "retrieve_only")):
                if picked.get(key) != now.get(key):
                    changes.append(f"{label} changed")
        except (ValueError, TypeError):
            pass
        what = f": {'; '.join(changes)}" if changes else ""
        self._set(self.edit_message, _wrap(
            f'<div class="note warn">The request changed since you started editing{_esc(what)}. Apply sends what\'s '
            "written below and undoes that; Start over puts the request as it is now in the box.</div>"))

    def _apply(self, *_: Any) -> None:
        try:
            changes = self.view._apply_request(self.editor.value)
        except ValueError as exc:
            self._set(self.edit_message, _wrap(f'<div class="note warn" style="white-space:pre-wrap">{_prose(exc)}'
                                               "</div>"))
            return
        self.pending.clear()
        self.broken.clear()
        for key in list(self.rows):  # the values may be written differently now: build every card again
            for store in (self.rows, self.inputs, self.row_notes, self.removes):
                store.pop(key).close()
        self._cancel_edit()
        self.sync()
        self._set_status("Applied: " + "; ".join(changes) + ".", "ok")

    def _render_code(self) -> None:
        """
        The Code tab: the setup as a Python script that asks the test questions, its JSON, or an AWS CLI command.
        """
        view = self.view
        asked = [q for q, _ in parse_questions(self.test_box.value)] or view._code_questions()
        retrieve = view.retrieve_only
        params, problems = view._preview(asked[0] if asked else None)
        region = view._region()
        if self.code_mode.value == "JSON":
            send = ("client.retrieve(retrievalQuery={'text': question}, **config)" if retrieve else
                    "client.retrieve_and_generate(input={'text': question}, **config)")
            lead = (f"This setup as the API's own JSON: the request without the question. Save it as "
                    f"bedrock-config.json; then {send} sends it with any question, and the AWS CLI reads it with "
                    "--cli-input-json file://bedrock-config.json.")
            body = _json_text_html(config_of(params))
        elif self.code_mode.value == "AWS CLI":
            lead = ("The first test question from a terminal (bash or zsh) with the AWS CLI v2: paste it and press "
                    "Enter. " + ("It prints each passage's score and file." if retrieve else "It prints the answer."))
            body = _shell_html(cli_command(params, region, asked[0] if asked else None))
        else:
            count = _plural(len(asked), "test question") if asked else "a question (put yours in)"
            what = "every passage each one finds, with its score" if retrieve else "each answer with the files it cites"
            lead = (f"A script that asks {count} with this setup and prints {what}. It needs only boto3: paste it "
                    "into a cell, or save it as a .py file and run it.")
            body = _python_html(python_script(params, region, asked, about=view._about(retrieve),
                                              width=_PYTHON_WIDTH))
        refused = "Bedrock would refuse this request, so the code would fail the same way: "
        warn = "".join(f'<div class="note warn">{_prose(refused + p)}</div>' for p in problems)
        self._set(self.code_view, _wrap(f'<div class="pd">{_prose(lead)}</div>{warn}'
                                        f'<pre class="code hl"{_SELECT}>{body}</pre>'))
