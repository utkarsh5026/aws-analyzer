"""
BedrockChatView's commands about what's sent.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.blocks import _Json
from ..._bedrock.filters import describe_sources
from ..._bedrock.prices import DEFAULT_MODEL
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from ..._kit.errors import _Hint
from ..._kit.fmt import _plural
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..helpers import DEFAULT_SETTINGS, _diff, _loads, _short
from ..analysis.schema import _FILTER_PATH, _GROUP_ORDER, _KB_CONFIG
from ..analysis.choices import describe_files, file_labels
from ..analysis.request import (
    _retrieve_path, build_request, build_retrieve_request, describe_setting, retrieve_settings,
    settings_from_request, validate_request,
)
from ..analysis.findings import settings_findings
from ..analysis.batches import _question_text, question_list
from ..analysis.code import cli_command, config_json, python_call, python_script
from ..blocks import _QUICK, _Code, _kind_text, _setting_marks, _waiting


class _SettingsCommands:
    """
    BedrockChatView's commands about what's sent: settings(), set(), unset(), fields(), request() and code(),
    with the request they preview and an edited request read back.
    """

    def _unsent(self, retrieve_only: bool | None = None) -> list[str]:
        """
        The settings the next request leaves out: the answer's, when it only searches.
        """
        if not self._retrieving(retrieve_only):
            return []
        sent = retrieve_settings(self.values, self.core.schema())
        return [key for key in self.values if key not in sent]

    def _preview(self, question: str | None = None, retrieve_only: bool | None = None
                 ) -> tuple[dict[str, Any], list[str]]:
        """
        The request the next question would send (a Retrieve request when it only searches; the window's mode
        when retrieve_only is None), and what Bedrock would refuse in it. Never raises: a knowledge base or model that
        can't be resolved yet shows as a placeholder.
        """
        try:
            kb_id = self._kb_id()
        except (ValueError, ClientError, BotoCoreError):
            kb_id = "<knowledge base ID>"
        schema = self.core.schema()
        if self._retrieving(retrieve_only):
            params = build_retrieve_request(question or "<your question>", kb_id, self.values, schema,
                                            region=self._region(), data_sources=self._sources_now(),
                                            files=self._files_now())
        else:
            try:
                _, arn = self.core.resolve_model(self.model)
            except (ValueError, ClientError, BotoCoreError):
                arn = str(self.model or self.core.default_model or DEFAULT_MODEL)
            params = build_request(question or "<your question>", kb_id, arn, self.values, schema,
                                   session_id=self.session_id, region=self._region(),
                                   data_sources=self._sources_now(), files=self._files_now())
        problems = [p for p in validate_request(params, schema) if not kb_id.startswith("<") or "knowledgeBaseId"
                    not in p]
        return params, problems

    def _marks(self, retrieve_only: bool | None = None) -> dict[tuple[str, ...], str]:
        return _setting_marks(self.values, self.core.schema(), self._retrieving(retrieve_only))

    def _json_notes(self, retrieve_only: bool | None = None) -> dict[tuple[str, ...], str]:
        schema = self.core.schema()
        if self._retrieving(retrieve_only):
            notes = {("retrievalQuery", "text"): "your question", ("knowledgeBaseId",): "knowledge base picker"}
            for path, _ in schema.auto:
                if _retrieve_path(path):
                    notes[_retrieve_path(path)] = "required; filled in for you"
            where = _retrieve_path(_FILTER_PATH)
        else:
            notes = {("input", "text"): "your question", ("sessionId",): "continues this conversation",
                     (*_KB_CONFIG, "knowledgeBaseId"): "knowledge base picker",
                     (*_KB_CONFIG, "modelArn"): "model picker"}
            notes.update({path: "required; filled in for you" for path, _ in schema.auto})
            where = _FILTER_PATH
        pickers = [name for name, on in (("data source", self._sources_now()), ("files", self._files_now())) if on]
        if pickers and where:
            notes[where] = " and ".join(pickers) + " picker" + ("s" if len(pickers) > 1 else "") + (
                " and your filter" if "filter" in self.values else "")
        return notes

    def _setup_call(self) -> str:
        """
        chat(...) with this knowledge base, model and settings, to open the same setup again.
        """
        args = [self.core.kb_name(self.kb)] if self.kb else []
        kwargs: dict[str, Any] = {"model": self.model} if self.model else {}
        sources = self._sources_now()
        if sources:
            names = [name or ds_id for ds_id, name in sources.items()]
            kwargs["data_source"] = names[0] if len(names) == 1 else names
        uris = self._files_now()
        if uris:
            kwargs["files"] = list(file_labels(uris).values())
        if self.retrieve_only:
            kwargs["retrieve_only"] = True
        kwargs.update({k: v for k, v in self.values.items() if k.isidentifier()})
        paths = {k: v for k, v in self.values.items() if not k.isidentifier()}
        if paths or any(key not in self.values for key in DEFAULT_SETTINGS):
            kwargs["settings"] = paths
        return _call("chat", *args, **kwargs)

    def _apply_request(self, text: str) -> list[str]:
        """
        Takes a request edited by hand: its knowledge base, model, session and settings become the chat's. A
        Retrieve request turns Retrieve only on (a RetrieveAndGenerate one turns it off) and leaves the answer's
        settings, which it has no place for, as they are. Raises a ValueError, changing nothing, if Bedrock would
        refuse it or the chat can't send it.
        """
        params = _loads(text, "The request")
        schema = self.core.schema()
        if not isinstance(params, dict):
            raise ValueError("The request is a JSON object: {\"input\": ..., \"retrieveAndGenerateConfiguration\": ...}")
        problems = validate_request(params, schema)
        if problems:
            if any("Unknown parameter in" in p and "InferenceConfig" in p for p in problems):
                problems.append("Settings a model takes beyond these (top_k for Claude, say) go in "
                                "generationConfiguration.additionalModelRequestFields: the model_fields setting.")
            raise ValueError("Bedrock would refuse this request:\n" + "\n".join(f"• {p}" for p in problems))
        picked, settings = settings_from_request(params, schema)
        retrieve_only = bool(picked.get("retrieve_only"))
        if retrieve_only:  # the answer's settings stay as they are: a Retrieve request has no place for them
            sent = retrieve_settings(self.values, schema)
            kept = {**{k: v for k, v in self.values.items() if k not in sent}, **settings}
            settings = {key: kept[key] for key in schema.fields if key in kept}
        kb = picked.get("knowledgeBaseId")
        kb_id = self.core.resolve(kb) if kb else self._kb_id()
        sources = self.core.resolve_sources(kb_id, picked.get("dataSources") or [])
        uris = list(dict.fromkeys(picked.get("files") or []))
        before = self._sources_now() if kb_id == self.kb else {}
        files_before = self._files_now() if kb_id == self.kb else []
        changes = []
        if kb_id != self.kb:
            changes.append(f"knowledge base {self.core.kb_name(kb_id)} (a new conversation)")
            self._use_kb(kb_id)
        if list(sources) != list(before):
            changes.append(f"questions search {describe_sources(sources)}")
        if uris != files_before:
            changes.append(f"questions search {describe_files(uris)}")
        self.data_source, self.picked_files = sources, uris
        if retrieve_only != self.retrieve_only:
            changes.append("Retrieve only: questions only search" if retrieve_only else "Answer: questions get an answer")
            self.retrieve_only = retrieve_only
        model = picked.get("modelArn")
        if model:
            try:
                same = self.core.resolve_model(self.model)[1] == model
            except (ValueError, ClientError, BotoCoreError):
                same = False
            if not same:
                self.model = self._model_from_arn(model)
                changes.append(f"model {self._model_label(self.model)}")
        session = picked.get("sessionId")
        if not retrieve_only and session != self.session_id and not (session is None and not self.answers):
            changes.append("a new conversation" if session is None else f"session {session}")
            self.session_id = session
        changes += _diff(self.values, settings)
        self.values = settings
        return changes or ["nothing changed"]

    def _model_from_arn(self, arn: str) -> str:
        """
        A model's ARN -> the ID the model picker shows, when it's one of the listed models.
        """
        for m in self.core._models or []:
            if arn in (m.arn, m.invoke_id, m.id):
                return m.invoke_id
        return arn

    def _next_set(self) -> str:
        """
        A set() call worth trying next: a common setting that isn't set yet, with its starting value.
        """
        fields = self.core.schema().fields
        for key in _QUICK:
            if key in fields and key not in self.values and fields[key].default is not None and key != "prompt":
                return _call("set", **{key: fields[key].default})
        return _call("set", n=10)

    def _settings_rows(self) -> list[list[Any]]:
        fields = self.core.schema().fields
        return [[key, _short(value, 70), describe_setting(fields[key], value), fields[key].where]
                for key, value in self.values.items()]

    def _code_questions(self, questions: Any = None) -> list[str]:
        """
        The questions the code asks: these, else the test questions, else this conversation's (each once).
        """
        if questions is not None:
            return [q for q, _ in question_list(questions)]
        if self.questions:
            return [q for q, _ in self.questions]
        return list(dict.fromkeys(a.question for a in self.answers))[-5:]

    def _about(self, retrieve_only: bool) -> str:
        """
        The comment over the code's CONFIG: the setup, in words.
        """
        kb_id = self.kb or ""
        name = self.core.kb_name(kb_id) if kb_id else ""
        parts = [f"Knowledge base {name} ({kb_id})" if name and name != kb_id else
                 f"Knowledge base {kb_id or '(not picked yet)'}"]
        parts.append("searched only (Retrieve), no answer" if retrieve_only else
                     f"answered by {self._model_label(self.model or '')}")
        sources, uris = self._sources_now(), self._files_now()
        parts += [f"only {describe_sources(sources)}"] if sources else []
        parts += [f"only {describe_files(uris)}"] if uris else []
        return ", ".join(parts) + ". Set up in the bedrock_chat window; each question is asked on its own."

    # ------------------------------------------------------------------ settings

    @_friendly_errors
    def settings(self) -> None:
        """
        What's sent with every question, each setting in plain English with where it goes in the request, and
        what Bedrock would refuse.
        """
        _, problems = self._preview()
        kb = self.core.kb_name(self.kb) if self.kb else "(not picked yet)"
        model = self._model_label(self.model or "")
        blocks: list[Any] = [
            _Title(f"Settings: {_plural(len(self.values), 'setting')} sent with every question",
                   f"{kb} · {model} · fields() lists every one you can add"),
            _Cards([("Knowledge base", kb), ("Data source", self._sources_text()), ("Files", self._files_text()),
                    ("Model", model), ("Settings", f"{len(self.values):,}"),
                    ("Conversation", f"{_plural(len(self.answers), 'question')} so far" if self.answers else "new")]),
            _Table(["Setting", "Value", "What it means", "Sent as"], self._settings_rows(), max_rows=0,
                   code_cols=(0,)),
        ]
        if not self.values:
            blocks.append(_Note("Nothing is set, so Bedrock uses its defaults: 5 passages, its own prompt and the "
                                "model's own temperature."))
        unsent = self._unsent()
        if unsent:
            blocks.append(_Note(f"The window is on Retrieve only, so its questions only search, and send the search "
                                f"settings only: {_waiting(unsent)} for an answer (ask(), or Answer in the "
                                "window)."))
        found = [("warn", f"Bedrock would refuse this request: {p}") for p in problems]
        sent = retrieve_settings(self.values, self.core.schema()) if self.retrieve_only else self.values
        blocks.append(_Findings(found + settings_findings(sent, self.model or ""),
                                empty="Nothing here looks wrong, as far as the API's own checks go."))
        blocks.append(_Code(self._setup_call(), "Open this setup again"))
        blocks.append(_Next([
            (self._next_set(), "change a setting, or add any field"),
            (_call("unset", next(reversed(self.values), "temperature")), "stop sending one"),
            ("request()", "the exact JSON the next question sends"),
        ]))
        self._show(blocks)

    @_friendly_errors
    def set(self, name: str | None = None, value: Any = None, **values: Any) -> None:
        """
        Changes what's sent with every question: set(temperature=0.2, n=8), or set('a.field.path', value) for any
        field fields() lists. None removes a setting; an open window follows.
        """
        if name is not None:
            if value is None:
                raise _Hint(f"Pass a value too: set({name!r}, ...). unset({name!r}) stops sending it.")
            values = {name: value, **values}
        if not values:
            raise _Hint("Pass what to change: set(temperature=0.2), or set('field.path', value). fields() lists "
                        "every setting.")
        changes = self._update(values)
        self._changed()
        _, problems = self._preview()
        found = [("warn", f"Bedrock would refuse this request: {p}") for p in problems]
        self._show([
            _Note("Changed: " + "; ".join(changes) + "." if changes else "Nothing changed: those were the values "
                  "already.", "ok"),
            _Findings(found + settings_findings(self.values, self.model or "")),
            _Next([("settings()", "every setting, in plain English"), ("request()", "the JSON the next question "
                   "sends"), ("ask('...')", "try it")]),
        ])

    @_friendly_errors
    def unset(self, *names: str) -> None:
        """
        Stops sending settings, so Bedrock uses its defaults for them: unset('temperature', 'top_p').
        """
        if not names:
            raise _Hint(f"Name the settings to stop sending: unset('temperature'). Set now: "
                        f"{', '.join(self.values) or 'nothing'}.")
        changes = self._update({name: None for name in names})
        self._changed()
        self._show([_Note("Removed: " + ", ".join(c.replace(" removed", "") for c in changes) + "." if changes
                          else "Nothing changed: " + ", ".join(names) + f" {'was' if len(names) == 1 else 'were'} "
                          "not set.", "ok")])

    @_friendly_errors
    def fields(self, match: str | None = None) -> None:
        """
        Every setting RetrieveAndGenerate takes, read from this boto3: the name set() takes, what it takes, what
        it does and where it goes in the request. match= keeps those whose name or description mentions it.
        """
        schema = self.core.schema()
        wanted = str(match).lower() if match else ""
        items = [f for f in schema.fields.values()
                 if not wanted or wanted in " ".join([f.key, f.where, f.label, *f.names, f.doc]).lower()]
        blocks: list[Any] = [
            _Title(f"Settings you can send ({len(items)})",
                   (f"matching {match!r} · " if match else "") + f"from this boto3's service model (botocore "
                   f"{schema.boto}) · set('name', value) adds one"),
        ]
        for group in _GROUP_ORDER:
            rows = [[f.key, _short(self.values[f.key], 30) if f.key in self.values else "", _kind_text(f), f.doc,
                     f.where if f.where != f.key else ""]
                    for f in items if f.group == group]
            if rows:
                blocks.append(_Table(["Setting", "Now", "Takes", "What it does", "Sent as"], rows, title=group,
                                     max_rows=0, code_cols=(0,)))
        if not items:
            blocks.append(_Note(f"No setting mentions {match!r}. fields() lists them all.", "warn"))
        blocks.append(_Next([(_call("set", items[0].key if items else "temperature",
                                    items[0].default if items and items[0].default is not None else 0.2),
                              "send one with every question"), ("settings()", "what's sent now")]))
        self._show(blocks)

    @_friendly_errors
    def request(self, question: str | None = None, retrieve_only: bool | None = None) -> None:
        """
        The exact RetrieveAndGenerate request your next question sends, as highlighted JSON, and the same call in
        Python. Nothing is sent. retrieve_only=True shows retrieve()'s Retrieve request (the search only), False the
        answer's; by default, the one the window's Send makes.
        """
        retrieve = self._retrieving(retrieve_only)
        params, problems = self._preview(_question_text(question) if question is not None else None, retrieve)
        kb = self.core.kb_name(self.kb) if self.kb else "(not picked yet)"
        found = [("warn", f"Bedrock would refuse this request: {p}") for p in problems]
        unsent = self._unsent(retrieve)
        sent = retrieve_settings(self.values, self.core.schema()) if retrieve else self.values
        blocks: list[Any] = [
            _Title("The request " + (f"for: {_clip(question, 70)}" if question else "your next question sends"),
                   ("Retrieve: the search only, no answer" if retrieve else "RetrieveAndGenerate")
                   + " · highlighted: your settings · nothing is sent"),
            _Cards([("Knowledge base", kb), ("Data source", self._sources_text()), ("Files", self._files_text()),
                    ("Model", "none: no answer" if retrieve else self._model_label(self.model or "")),
                    ("Settings", f"{len(sent):,} of {len(self.values):,}" if unsent else f"{len(self.values):,}"),
                    ("Conversation", "not part of it" if retrieve else "continues this one" if self.session_id
                     else "new")]),
            _Findings(found + settings_findings(sent, self.model or "")),
        ]
        if unsent:
            blocks.append(_Note(f"Retrieve sends the search settings only: {_waiting(unsent)} for an answer."))
        blocks += [
            _Json(params, "Request", marks=self._marks(retrieve), notes=self._json_notes(retrieve)),
            _Code(python_call(params, self._region()), "The same call in Python"),
            _Next([("settings()", "the settings in plain English"), (self._next_set(), "change one"),
                   (_call("request", retrieve_only=not retrieve), "the answer's request" if retrieve else
                    "the search-only request retrieve() sends")]),
        ]
        self._show(blocks)

    @_friendly_errors
    def code(self, questions: Any = None, retrieve_only: bool | None = None) -> None:
        """
        This setup as code to copy and run anywhere: a Python script (boto3 only) that asks your test questions and
        prints each answer with the files it cites, the config as JSON (the request without the question), and the
        AWS CLI command for one question. It follows the knowledge base, model, data source, files and settings.
        Nothing is sent. questions: the ones the script asks (default: the test questions, else this conversation's);
        retrieve_only=True gives the search-only (Retrieve) setup.
        """
        retrieve = self._retrieving(retrieve_only)
        asked = self._code_questions(questions)
        params, problems = self._preview(asked[0] if asked else None, retrieve)
        region = self._region()
        kb = self.core.kb_name(self.kb) if self.kb else "(not picked yet)"
        sent = retrieve_settings(self.values, self.core.schema()) if retrieve else self.values
        found = [("warn", f"Bedrock would refuse this request, so the code would fail the same way: {p}")
                 for p in problems]
        api = "Retrieve: the search only, no answer" if retrieve else "RetrieveAndGenerate"
        what = "every passage each question finds" if retrieve else "each answer with the files it cites"
        count = _plural(len(asked), "question") if asked else "a question (put yours in)"
        send = ("client.retrieve(retrievalQuery={'text': question}, **config)" if retrieve
                else "client.retrieve_and_generate(input={'text': question}, **config)")
        blocks: list[Any] = [
            _Title(f"This setup as code: {kb}", f"{api} · nothing is sent · copy it and run it wherever boto3 or the "
                                                "AWS CLI has credentials"),
            _Cards([("Knowledge base", kb), ("Model", "none: no answer" if retrieve else
                                             self._model_label(self.model or "")),
                    ("Data source", self._sources_text()), ("Files", self._files_text()),
                    ("Settings", f"{len(sent):,}"), ("Questions", f"{len(asked):,}" if asked else "none yet")]),
            _Findings(found + settings_findings(sent, self.model or "")),
            _Code(python_script(params, region, asked, about=self._about(retrieve)),
                  f"Python: asks {count} with this setup and prints {what} (boto3 only)"),
            _Code(config_json(params), "The config as JSON: the request without the question", lang="json"),
            _Code(cli_command(params, region, asked[0] if asked else None),
                  "AWS CLI: one question from a terminal (bash or zsh)", lang="shell"),
            _Note(f"The JSON is the API's own request without the question: save it as bedrock-config.json, and "
                  f"{send} sends it with any question (config = json.load(open('bedrock-config.json'))). The AWS CLI "
                  "reads it with --cli-input-json file://bedrock-config.json. The script and the command use the AWS "
                  "credentials and region of wherever they run."),
            _Next([("ask_all()" if self.questions else "ask_all(['a question', 'another'])",
                    "ask the test questions here first, with this setup"),
                   ("settings()", "what each setting does"),
                   (_call("code", retrieve_only=not retrieve), "the answer's setup" if retrieve else
                    "the search-only (Retrieve) setup")]),
        ]
        self._show(blocks)
