"""
The chat window's header: the knowledge base, data source, files and model fields.
"""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.blocks import _Download
from ..._bedrock.files import _LIST_LINK_BYTES, SavedFileList, _data_href, file_list_name
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import source_name
from ..._bedrock.models import ModelInfo
from ..._bedrock.prices import DEFAULT_MODEL
from ..._kit.errors import _Hint, _error_name, _why
from ..._kit.fmt import _plural, human_age
from ..._kit.render import _blocks_html

from ..models import DataSource, KnowledgeBase
from ..analysis.choices import describe_files, file_labels, kb_search_text
from ..style import _ROOT
from ..blocks import _wrap
from .widgets import _Choice, _ChoicePicker


class _PickersPane:
    """
    The chat window's header: the knowledge base, data source, files and model fields, and what picking or
    typing in each does.
    """

    def _kb_picker(self) -> _ChoicePicker:
        view = self.view
        picker = _ChoicePicker(self, "Knowledge base", "knowledge base", on_pick=self._kb_picked, on_text=self._kb_typed,
                         query=kb_search_text, placeholder="Search by name, ID or description",
                         empty="Pick a knowledge base", basis="250px", list_width="420px")
        kbs: list[KnowledgeBase] | None
        try:
            kbs = view.core.knowledge_bases()
        except (ClientError, BotoCoreError, ValueError) as exc:
            kbs = None
            reason = _why(_error_name(exc), "bedrock:ListKnowledgeBases") if not isinstance(exc, ValueError) else exc
            self.problems.append(f"Couldn't list the knowledge bases ({reason}): click Knowledge base and type one's "
                                 "ID.")
            picker.problem = f"Couldn't list the knowledge bases ({reason}). Type one's ID or ARN and press Enter."
        current = view.kb
        if kbs:
            try:
                current = view._kb_id() if view.kb is not None or len(kbs) == 1 else None
            except (ValueError, ClientError, BotoCoreError) as exc:
                self.problems.append(str(exc))
                current = None
            if current is None:  # pick one to start with: the first active one
                ready = [kb for kb in sorted(kbs, key=lambda k: k.name.lower()) if kb.status == "ACTIVE"]
                current = (ready or sorted(kbs, key=lambda k: k.name.lower()))[0].id
                view.kb = current
        elif kbs == []:
            self.problems.append(f"There are no knowledge bases in {view.core.region}. They're regional: "
                                 "chat(region='us-west-2') looks in another region.")
            picker.problem = (f"There are no knowledge bases in {view.core.region}; chat(region='us-west-2') looks in "
                              "another region. One's ID or ARN still works here: type it and press Enter.")
        picker.set_choices([self._kb_choice(kb) for kb in sorted(kbs or [], key=lambda k: k.name.lower())],
                           current or "")
        return picker

    @staticmethod
    def _kb_choice(kb: KnowledgeBase) -> _Choice:
        status = "" if kb.status == "ACTIVE" else kb.status.lower().replace("_", " ")
        changed = f"changed {human_age(kb.updated)}" if kb.updated else ""
        tone = {"ACTIVE": "ok", "FAILED": "bad", "DELETE_UNSUCCESSFUL": "bad"}.get(kb.status, "warn")
        return _Choice(kb.id, kb.name or kb.id, detail=kb.id, tone=tone, also=(kb.status,),
                       note=" · ".join(part for part in (status, kb.description, changed) if part))

    def _source_picker(self) -> _ChoicePicker:
        picker = _ChoicePicker(self, "Data source", "data source", on_pick=self._source_picked,
                         placeholder="Search by name or ID", empty="All data sources", basis="190px")
        self.source_pick = picker
        problem = self._fill_sources()
        if problem:
            self.problems.append(problem)
        return picker

    def _fill_sources(self) -> str:
        """
        Puts the knowledge base's data sources in the picker, and shows it when there's a choice to make (more
        than one). Returns what went wrong, if anything.
        """
        view, picker = self.view, self.source_pick
        sources: list[DataSource] = []
        problem = ""
        if view.kb is not None and view.kb not in self.unlisted:
            try:
                sources = view.core.data_sources(view.kb)
            except (ClientError, BotoCoreError, ValueError) as exc:
                self.unlisted.add(view.kb)
                reason = _why(_error_name(exc), "bedrock:ListDataSources") if not isinstance(exc, ValueError) else exc
                problem = f"Couldn't list the data sources ({reason}), so questions search all of them."
        current: dict[str, str] = {}
        if view.kb is not None and view.data_source:
            try:
                current = view._sources(view.kb)
            except (ClientError, BotoCoreError, ValueError) as exc:
                problem = f"{exc} Questions search all of them."
                view.data_source = {}
        tones = {"AVAILABLE": "ok", "DELETE_UNSUCCESSFUL": "bad"}
        choices = [_Choice("", "All data sources", note=f"Questions search all {len(sources)}" if sources else
                           "Questions search every data source")]
        for ds in sorted(sources, key=lambda d: (d.name or d.id).lower()):
            status = "" if ds.status == "AVAILABLE" else ds.status.lower().replace("_", " ")
            changed = f"changed {human_age(ds.updated)}" if ds.updated else ""
            choices.append(_Choice(ds.id, ds.name or ds.id, detail=ds.id, tone=tones.get(ds.status, "warn"),
                                   note=" · ".join(part for part in (status, ds.description, changed) if part)))
        value = ",".join(current)
        if value and value not in {c.value for c in choices}:  # several, or one that couldn't be listed
            choices.insert(1, _Choice(value, " + ".join(name or ds_id for ds_id, name in current.items()),
                                      badge=_plural(len(current), "data source") if len(current) > 1 else "",
                                      note="Picked with Edit JSON or use(data_source=...)", also=tuple(current)))
        picker.set_choices(choices, value)
        picker.visible = len(choices) > 2 or bool(value)
        return problem

    def _files_picker(self) -> _ChoicePicker:
        """
        The Files field, and the line under the header that shows the files ticked, as chips.
        """
        w, layout = self.w, self.w.Layout
        self.files_kb: str | None = None  # the knowledge base whose files the list holds
        self.files_pick = _ChoicePicker(self, "Files", "indexed file", on_pick=self._set_files, on_text=self._file_typed,
                                  on_open=self._list_files, describe=self._files_face, multi=True,
                                  placeholder="Search by file name or folder", empty="All files", basis="140px")
        self.file_chips = w.HBox(layout=layout(width="auto", flex_flow="row wrap", align_items="center"))
        self.file_chips.add_class("kbc-files")
        self.all_files_button = w.Button(description="All files", tooltip="Search every file again",
                                         layout=layout(width="auto"))
        self.all_files_button.add_class("kbc-small")
        self.all_files_button.add_class("kbc-ghost")
        self.all_files_button.on_click(self._safely(lambda _button: self._set_files([])))
        label = w.HTML(_wrap('<span class="fbl">Questions search only</span>'), layout=layout(flex="0 0 auto"))
        self.file_bar = w.HBox([label, self.file_chips, self.all_files_button],
                               layout=layout(width="100%", align_items="center", flex_flow="row wrap",
                                             display="none"))
        self.file_bar.add_class("kbc-file-bar")
        self.files_list_button = w.Button(description="⬇ List", tooltip="Save the s3:// path of each file the list "
                                          "shows (what the search finds, in its order), one per line, in a .txt file "
                                          "beside the notebook, with a link that downloads it",
                                          layout=layout(width="auto"))
        for name in ("kbc-small", "kbc-list"):
            self.files_list_button.add_class(name)
        self.files_list_button.on_click(self._safely(lambda _button: self._save_file_list()))
        self.files_saved = w.HTML(layout=layout(width="100%"))
        self.files_saved.add_class("kbc-saved")
        search_row = self.files_pick.panel.children[0]
        search_row.children = (search_row.children[0], self.files_list_button, *search_row.children[1:])
        self.files_pick.panel.children = (*self.files_pick.panel.children, self.files_saved)
        self.saved_list: SavedFileList | None = None  # the list ⬇ List saved last
        problem = self._draw_files()
        if problem:
            self.problems.append(problem)
        return self.files_pick

    @staticmethod
    def _files_face(uris: list[str]) -> tuple[str, str]:
        return ("All files" if not uris else _plural(len(uris), "file")), ""

    def _draw_files(self) -> str:
        """
        The picked files as chips (a click removes one), and the buttons that fit. Returns what went wrong.
        """
        w, view = self.w, self.view
        problem = ""
        if self.files_kb is not None and self.files_kb != view.kb:  # another knowledge base: list its files anew
            self.files_kb = None
            self.saved_list = None
            self._set(self.files_saved, "")
            self.files_pick.problem = self.files_pick.note = ""
            self.files_pick.close()
            self.files_pick.set_choices([])
        uris: list[str] = []
        if view.kb is not None and view.picked_files:
            try:
                uris = view._files_for(view.kb)
            except (ClientError, BotoCoreError, ValueError) as exc:
                problem = f"{str(exc).rstrip('.')}. Questions search every file."
                view.picked_files = []
        chips = []
        for uri in uris:
            chip = w.Button(description=f"{source_name(uri)} ✕", tooltip=f"{uri}: click to stop asking only it",
                            layout=w.Layout(width="auto"))
            chip.add_class("kbc-file")
            chip.on_click(self._safely(lambda _button, uri=uri: self._remove_file(uri)))
            chips.append(chip)
        self.file_chips.children = chips
        self.files_pick.set_value(uris)
        self.files_pick.visible = view.kb is not None
        self.file_bar.layout.display = "" if uris else "none"
        return problem

    def _list_files(self) -> None:
        """
        Lists the knowledge base's indexed files into the Files list, once per knowledge base (when it opens).
        """
        view, picker = self.view, self.files_pick
        if view.kb is None:
            raise _Hint("Pick a knowledge base first.")
        if self.files_kb == view.kb:
            return
        self.files_kb = view.kb
        picker.problem = picker.note = ""
        try:
            listing = view.core.files(view.kb)
        except (ClientError, BotoCoreError) as exc:
            picker.problem = (f"Couldn't list the files ({_why(_error_name(exc), 'bedrock:ListKnowledgeBaseDocuments')})"
                              ". Type a file's s3:// path and press Enter.")
            picker.set_choices([])
            return
        try:
            names = view.core.data_source_names(view.kb)  # listed already, by files()
        except (ClientError, BotoCoreError):
            names = {}
        labels = file_labels(d.uri for d in listing.searchable)
        several = len({d.data_source_id for d in listing.documents}) > 1
        choices = []
        for d in sorted(listing.searchable, key=lambda d: labels[d.uri].lower()):
            label = labels[d.uri]
            folder = label.rsplit("/", 1)[0] + "/" if "/" in label else ""
            parts = (folder, names.get(d.data_source_id) or d.data_source_id if several else "",
                     "partly indexed" if d.status != "INDEXED" else "")
            choices.append(_Choice(d.uri, source_name(d.uri), note=" · ".join(p for p in parts if p),
                                   also=(label, d.uri)))
        notes = []
        if listing.truncated:
            notes.append(f"Only the first {len(listing.documents):,} files were listed; type a full s3:// path for "
                         "another")
        if listing.errors:
            notes.append(", ".join(names.get(ds_id) or ds_id for ds_id in listing.errors) + " has no file list "
                         "(only S3 and custom data sources keep one)")
        picker.note = ". ".join(notes)
        picker.set_choices(choices)

    def _save_file_list(self) -> None:
        """
        ⬇ List in the Files list: the s3:// paths of the files it shows (what the search finds, in its order), one
        per line, in <knowledge base>-indexed-files.txt beside the notebook, and a link under the list that downloads
        it.
        """
        view, picker = self.view, self.files_pick
        found = picker.matches()
        if not found:
            picker.message = "No file in the list to save: change the search."
            picker._draw_list()
            return
        name = view.core.kb_name(view.kb) if view.kb else "knowledge-base"
        self.saved_list = saved = view.core.save_file_list([c.value for c in found], file_list_name(name, "indexed"))
        href = _data_href(saved.text, _LIST_LINK_BYTES)
        self._set(self.files_saved, _wrap(_blocks_html([_Download(saved.path, href)], root=_ROOT)))
        where = ("the link under the Files list downloads it to your computer" if href else
                 "it's too big to download from here: download it from Jupyter's file browser")
        self._set_status(f"Saved the s3:// paths of {_plural(saved.count, 'file')}, one per line, in {saved.name} "
                         f"beside the notebook{', replacing the one there' if saved.replaced else ''}: {where}.", "ok")

    def _file_typed(self, text: str) -> None:
        """
        Enter on text no listed file holds: a full s3:// path is picked as it is.
        """
        if "://" not in text:
            raise _Hint(f"No indexed file's path holds {text!r}. A file that isn't listed can be picked by its full "
                        "s3:// path.")
        self._add_file(text)

    def _add_file(self, uri: str) -> None:
        self._set_files([*self.view._files_now(), uri])

    def _remove_file(self, uri: str) -> None:
        self._set_files([u for u in self.view._files_now() if u != uri])

    def _set_files(self, uris: list[str]) -> None:
        self.view.picked_files = list(dict.fromkeys(uris))
        self._draw_files()
        self._refresh()
        self._set_status(f"The next questions search {describe_files(self.view.picked_files)}; the conversation goes "
                         "on.", "ok")

    def _model_picker(self) -> _ChoicePicker:
        view = self.view
        picker = _ChoicePicker(self, "Model", "model", on_pick=self._model_picked, on_text=self._model_typed,
                         placeholder="Search by name, provider or ID", empty="Pick a model", basis="300px",
                         list_width="420px")
        picker.tip_off = "Retrieve only doesn't use a model: switch to Answer to pick one"
        models: list[ModelInfo] = []
        current = str(view.model or view.core.default_model or DEFAULT_MODEL)
        try:
            models = [m for m in view.core.models() if m.via != "provisioned only"]
            current = view.core.resolve_model(view.model)[0]
        except (ClientError, BotoCoreError) as exc:
            reason = _why(_error_name(exc), 'bedrock:ListFoundationModels')
            self.problems.append(f"Couldn't list the models ({reason}): click Model and type a model ID.")
            picker.problem = (f"Couldn't list the models ({reason}). Type a model ID or inference profile and press "
                              "Enter.")
        except ValueError as exc:
            self.problems.append(str(exc))
        if models:
            view.model = current
        picker.set_choices([self._model_choice(m) for m in models], current)
        return picker

    @staticmethod
    def _model_choice(m: ModelInfo) -> _Choice:
        price = f"${m.price_in:,.2f} / ${m.price_out:,.2f}" if m.price_in is not None else ""
        how = m.via + (" (legacy)" if m.status == "LEGACY" else "")
        return _Choice(m.invoke_id, m.name or m.id, detail=m.invoke_id, also=(m.id, m.arn),
                       badge=" · ".join(part for part in (m.provider, price) if part),
                       note=" · ".join(part for part in (m.provider, how, f"{price} per 1M tokens" if price
                                                         else "price unknown") if part))

    def _kb_picked(self, value: str) -> None:
        kb_id = self.view.core.resolve(value)
        if kb_id != self.view.kb:
            self.view._use_kb(kb_id)
            self._draw_files()
            problem = self._fill_sources()
            self.cleared(f"Now asking {self.view.core.kb_name(kb_id)}: a new conversation." + (f" {problem}" if problem
                                                                                               else ""))

    def _kb_typed(self, text: str) -> None:
        """
        Enter on a knowledge base the list doesn't hold (it couldn't be listed, or was made since): its ID, name or
        ARN. A ValueError says which knowledge bases there are.
        """
        kb_id = self.view.core.resolve(text)
        self.kb_pick.set_value(kb_id)
        self._kb_picked(kb_id)

    def _source_picked(self, value: str) -> None:
        if self.view.kb is None:
            return
        ids = [ds_id for ds_id in str(value or "").split(",") if ds_id]
        self.view.data_source = self.view.core.resolve_sources(self.view.kb, ids)
        self._refresh()
        self._set_status(f"The next questions search {describe_sources(self.view.data_source)}; the conversation "
                         "goes on.", "ok")

    def _model_picked(self, value: str) -> None:
        self.view.model = str(value).strip()
        self._refresh()
        self._set_status(f"The next question goes to {self.view._model_label(self.view.model)}.", "ok")

    def _model_typed(self, text: str) -> None:
        """
        Enter on a model the list doesn't hold: its ID, inference profile or ARN, or a short name ('sonnet').
        """
        model_id = self.view.core.resolve_model(text)[0]
        self.model_pick.set_value(model_id)
        self._model_picked(model_id)
