"""
The chat window's conversation and its composer.
"""

from __future__ import annotations

import time
from typing import Any

from ..._kit.render import _prose
from ..._kit.text import _esc

from ..helpers import MAX_QUESTION_CHARS
from ..blocks import _AVATAR, _question_html, _waiting, _wrap, _writing_html


class _ConversationPane:
    """
    The chat window's conversation: the greeting, Send, the Answer / Retrieve only switch and New chat.
    """

    def _hello(self) -> str:
        """
        The first bubble: two lines to start from, with the tour of the window folded under them, so an empty
        conversation isn't a page of instructions.
        """
        view = self.view
        name = view.core.kb_name(view.kb) if view.kb else "your knowledge base"
        tour = [
            "<b>Knowledge base</b> and <b>Model</b>, above, switch to another: search the list, or paste an ID and "
            "press Enter.",
            "<b>Data source</b> asks only one of the knowledge base's data sources." if self.source_pick.visible else "",
            "<b>Files</b> asks only the files you tick.",
            "<b>Retrieve only</b>, at the top right, only searches: every passage found, best first, with its score, "
            "and no answer.",
            "<b>Settings</b> change what every question sends. <b>Add a setting</b> finds any field the API has.",
            "<b>Request</b> shows the next request as JSON, to edit by hand or copy as <b>Python</b>; "
            "<i>Request and response JSON</i>, under an answer, shows what was sent and what came back.",
            "<b>Test</b> asks a list of questions, each on its own, and shows how each did. <b>Try variations</b> "
            "ranks every combination of the settings you list.",
            "<b>Runs</b> keeps every test run, to compare, switch to or save.",
            "<b>Code</b> gives this setup as Python, JSON or an AWS CLI command.",
            "Each question follows up on the ones before it. <b>New chat</b> starts over.",
        ]
        return (
            f'<div class="hello">{_AVATAR}<div><b>Ask {_esc(name)} a question.</b> Answers cite the passages they come '
            'from <sup>[1]</sup>. Click a source to read it.<details class="tour"><summary>How this window works'
            "</summary><ul>" + "".join(f"<li>{line}</li>" for line in tour if line) + "</ul></details></div></div>"
        )

    # ----------------------------------------------------------------- the chat

    def _send(self, *_: Any) -> None:
        if self.busy:
            return
        question = self.question.value.strip()
        if not question:
            self._set_status("Type a question first, then press Enter.")
            return
        if len(question) > MAX_QUESTION_CHARS:
            self._set_status(f"Bedrock takes questions of up to {MAX_QUESTION_CHARS:,} characters, and this one has "
                             f"{len(question):,}: shorten it.", "warn")
            return
        view = self.view
        retrieve = view.retrieve_only
        self.busy = True
        self.send_button.disabled, self.send_button.description = True, "Searching…" if retrieve else "Asking…"
        self.question.value = ""
        self._add(_question_html(question))
        model = view._model_label(view.model or "")
        name = view.core.kb_name(view.kb) if view.kb else "the knowledge base"
        doing = f"Searching {_esc(name)}" + (" (retrieve only: no answer)" if retrieve else f" and asking {_esc(model)}")
        waiting = f'<div class="msg bot wait"><span class="dots"><i></i><i></i><i></i></span>{doing}…</div>'
        bot = self._add(waiting)
        self._show_log()
        if self.broken:
            self._set_status(f"Not sent, because they can't be: {', '.join(self.broken)} (see Settings).", "warn")
        else:
            self._set_status("")
        shown = [0.0]

        def on_text(text: str) -> None:
            now = time.monotonic()
            if now - shown[0] >= 0.08:  # a few updates a second is smooth; more only floods the front end
                shown[0] = now
                bot.value = _wrap(_writing_html(model, text))

        try:
            a = view._turn(question, on_text=on_text if view.stream and not retrieve else None, retrieve_only=retrieve)
        except Exception as exc:  # shown in the conversation, where the answer would have been
            bot.value = _wrap(f'<div class="msg bot err">{_prose(self._error_text(exc))}<div class="who" '
                              'style="margin-top:6px">Your question is back in the box: change a setting, or the '
                              "question, and ask again.</div></div>")
            self.question.value = question
            self._set_status("")
        else:
            bot.value = _wrap(view._turn_html(a))
            self._render_response()
            self._refresh()
            self._set_status(view._conversation_line())
        finally:
            self.busy = False
            self.send_button.disabled = False
            self._show_mode()

    def _show_mode(self) -> None:
        """
        The box, the button and the model picker as Answer or Retrieve only has them: a search needs no model.
        """
        retrieve = self.view.retrieve_only
        self.send_button.description = "Retrieve" if retrieve else "Send"
        self.send_button.tooltip = "Search only (Enter does too)" if retrieve else "Ask (Enter does too)"
        self.question.placeholder = ("Type a question to search for" if retrieve
                                     else "Ask a question, then press Enter")
        self.model_pick.disabled = retrieve

    def _mode_changed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        view = self.view
        view.retrieve_only = change["new"] == "retrieve"
        self._show_mode()
        self._sync_rows()
        self._refresh()
        again = ""
        if view.answers and not self.question.value.strip():  # to ask the last question the other way
            self.question.value = view.answers[-1].question
            again = " Your last question is back in the box: press Enter to " + (
                "see what it retrieves." if view.retrieve_only else "ask it.")
        if view.retrieve_only:
            unsent = view._unsent()
            self._set_status("Retrieve only: the next questions only search, and show every passage found, best "
                             "first, with no answer." + (f" {_waiting(unsent)} for Answer." if unsent else "")
                             + again, "ok")
        else:
            self._set_status(f"Answer: the next questions get an answer from {view._model_label(view.model or '')}."
                             + again, "ok")

    def _new_chat(self, *_: Any) -> None:
        self.view._reset()
        self.cleared("New conversation: the next question starts a new Bedrock session.")

    def _stream_changed(self, change: dict[str, Any]) -> None:
        self.view.stream = bool(change["new"])
