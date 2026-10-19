"""
BedrockChatView's commands about the knowledge base, its files and the model.
"""

from __future__ import annotations

import difflib
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.blocks import _file_list_blocks
from ..._bedrock.files import _link_seconds, _list_target, _notebook_href, file_list_name, parse_list_link
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import SEARCHABLE, source_name
from ..._bedrock.prices import _per_million
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone
from ..._kit.errors import _Hint, _why
from ..._kit.fmt import _plural, human_age
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..analysis.choices import describe_files, file_labels, match_kbs


class _KnowledgeBaseCommands:
    """
    BedrockChatView's commands about the knowledge base, its files and the model: use(), files(),
    save_file_list(), kbs() and models().
    """

    # ------------------------------------------------------ knowledge base, model

    @_friendly_errors
    def use(self, kb: str | None = None, model: str | None = None, data_source: Any = None,
            files: Any = None) -> None:
        """
        Switches the knowledge base, the model, or what questions search: data_source= (one of the knowledge base's
        data sources, a name or ID, or a list) and files= (file names, paths or s3:// paths, which files() lists), or
        'all' for every one. Another knowledge base starts a new conversation; the rest keep it.
        """
        if kb is None and model is None and data_source is None and files is None:
            raise _Hint("Pass kb=, model=, data_source= or files=: use('support-docs'), use(model='sonnet'), "
                        "use(data_source='faq'), use(files=['refund-policy.pdf']). kbs(), models() and files() list "
                        "them.")
        # everything is checked before anything changes
        kb_id = self.core.resolve(kb) if kb is not None else None
        target = kb_id or (self._kb_id() if data_source is not None or files is not None else "")
        sources = self.core.resolve_sources(target, data_source) if data_source is not None else None
        uris = self.core.resolve_files(target, files) if files is not None else None
        model_id = self.core.resolve_model(model)[0] if model is not None else None
        notes: list[str] = []
        level = "ok"
        if kb_id is not None:
            if kb_id != self.kb:
                self._use_kb(kb_id)
                self._changed(f"Now asking {self.core.kb_name(kb_id)}: a new conversation.")
            notes.append(f"Knowledge base: {self.core.kb_name(kb_id)} ({kb_id}).")
            if data_source is None and files is None:
                try:
                    names = [ds.name or ds.id for ds in self.core.data_sources(kb_id)]
                except (ClientError, BotoCoreError):
                    names = []
                if len(names) > 1:
                    notes.append(f"It has {len(names)} data sources ({', '.join(names[:6])}"
                                 f"{', …' if len(names) > 6 else ''}): {_call('use', data_source=names[0])} asks only "
                                 "one.")
        if sources is not None:
            self.data_source = sources
            notes.append(f"Questions search {describe_sources(sources)}.")
        if uris is not None:
            self.picked_files = uris
            notes.append(f"Questions search {describe_files(uris)}.")
            unindexed = [u for u in uris if self.core.file_status(target, u) not in ("", *SEARCHABLE)]
            if unindexed:
                level = "warn"
                status = self.core.file_status(target, unindexed[0]).lower().replace("_", " ")
                one = len(unindexed) == 1
                what = describe_files(unindexed)
                notes.append(f"{what[:1].upper()}{what[1:]} {'is' if one else 'are'}n't indexed ({status}), so "
                             f"nothing can come from {'it' if one else 'them'}: files() shows each file's status.")
        if model_id is not None:
            self.model = model_id
            notes.append(f"Model: {self._model_label(self.model)} ({self.model}).")
        if sources is not None or uris is not None or model_id is not None:
            self._changed()
        self._show([_Note(" ".join(notes), level), _Next([("ask('...')", "ask it something"),
                                                           ("settings()", "what's sent with every question")])])

    @_friendly_errors
    def files(self, match: str | None = None) -> None:
        """
        The knowledge base's files, to point questions at: name, folder, data source, whether it's indexed and when
        it changed, with the ones questions search marked. match= keeps those whose path contains it.
        """
        kb_id = self._kb_id()
        with self._progress("Listing files", unit="files"):
            listing = self.core.files(kb_id)
        picked = set(self._files_now())
        try:
            names = self.core.data_source_names(kb_id)
        except (ClientError, BotoCoreError):
            names = {}
        wanted = str(match).lower() if match else ""
        labels = file_labels(d.uri for d in listing.documents)
        docs = sorted((d for d in listing.documents if not wanted or wanted in d.uri.lower()),
                      key=lambda d: (d.uri not in picked, labels.get(d.uri, d.uri).lower()))
        tones = {"FAILED": "bad", "NOT_FOUND": "bad"}
        rows = [[source_name(d.uri) + (" (picked)" if d.uri in picked else ""),
                 labels[d.uri].rsplit("/", 1)[0] + "/" if "/" in labels.get(d.uri, "") else "-",
                 names.get(d.data_source_id) or d.data_source_id or "-",
                 _Tone(d.status.lower().replace("_", " "), "" if d.status in SEARCHABLE else tones.get(d.status, "warn")),
                 human_age(d.updated)] for d in docs]
        unindexed = len(listing.documents) - len(listing.searchable)
        found: list[tuple[str, str]] = []
        if unindexed:
            found.append(("warn", f"{_plural(unindexed, 'file')} {'is' if unindexed == 1 else 'are'}n't indexed (failed, "
                                  "ignored or still syncing), so questions can't find anything in them. bedrock_kb.py's "
                                  "documents(status='FAILED') says why; a sync picks up fixed files."))
        for ds_id, code in listing.errors.items():
            name = names.get(ds_id) or ds_id
            reason = (_why(code, "bedrock:ListKnowledgeBaseDocuments") if "Denied" in code
                      else f"{code}: only S3 and custom data sources keep one")
            found.append(("info", f"The data source {name!r} has no file list ({reason}), so its files can't be picked. "
                                  "It's searched as long as no files are picked."))
        if listing.truncated:
            found.append(("info", f"Only the first {len(listing.documents):,} files were listed. A file past them can "
                                  "still be picked by its full path: use(files=['s3://...'])."))
        kb = self.core.kb_name(kb_id)
        blocks: list[Any] = [
            _Title(f"Files in {kb} ({len(docs):,}{'+' if listing.truncated and not wanted else ''})",
                   (f"matching {match!r} · " if match else "") + "use(files=[...]) or the window's Files list makes "
                   "questions search only some of them"),
            _Cards([("Files", f"{len(listing.documents):,}{'+' if listing.truncated else ''}"),
                    ("Indexed", f"{len(listing.searchable):,}"),
                    ("Not indexed", f"{unindexed:,}", "warn" if unindexed else ""),
                    ("Questions search", self._files_text())]),
            _Findings(found),
            _Table(["File", "Folder", "Data source", "Status", "Changed"], rows,
                   title=f"Matching {match!r}" if match else ""),
        ]
        if not docs:
            blocks.append(_Note(f"No file's path contains {match!r}. files() lists them all." if match else
                                "No files are listed for this knowledge base.", "warn" if match else ""))
        steps: list[tuple[str, str]] = []
        first = next((d for d in docs if d.status in SEARCHABLE and d.uri not in picked), None)
        if first is not None:
            steps.append((_call("use", files=[labels[first.uri]]), "ask only this file"))
        if picked:
            steps.append((_call("use", files="all"), "ask every file again"))
        elif len(docs) > 50 and not match:
            steps.append((_call("files", "refund"), "only the files whose path contains a word"))
        steps.append((_call("save_file_list", *([match] if match else [])),
                      "every one's s3:// path in a .txt file, one per line, to download"))
        steps.append(("app()", "tick files in the chat window's Files list"))
        blocks.append(_Next(steps[:3]))
        self._show(blocks)

    @_friendly_errors
    def save_file_list(self, match: str | None = None, path: str | None = None, *, link: str = "s3",
                       expires: int = 3600) -> None:
        """
        Saves the knowledge base's files as a text file, one per line, with a link that downloads it to your
        computer: each file's s3:// path, sorted by path, ready for a script, aws s3 cp or a spreadsheet.

        match= keeps the files whose path contains it, as files() does. link='https' writes a link that opens each
        file in a browser instead (presigned here with your credentials, no AWS call; each works for `expires`
        seconds, an hour unless you say, and anyone holding one can open its file until then), and link='key' each
        one's path in the bucket. path: default <knowledge base>-files.txt beside the notebook (a folder puts it
        there); a file of that name is replaced. Only that file is written: nothing in AWS changes. The window's Files
        list has the same as its ⬇ List button.
        """
        kb_id = self._kb_id()
        link = parse_list_link(link)
        if link == "https":
            expires = _link_seconds(expires)
        with self._progress("Listing files", unit="files"):
            listing = self.core.files(kb_id)
        wanted = str(match).lower() if match else ""
        docs = sorted((d for d in listing.documents if not wanted or wanted in d.uri.lower()),
                      key=lambda d: d.uri.lower())
        name = self.core.kb_name(kb_id)
        if not docs:
            raise _Hint(f"No file's path in {name} contains {match!r}, so nothing was saved: files() lists them all."
                        if match else f"{name} has no files listed (no S3 or custom data source, or nothing in them "
                                      "yet), so nothing was saved.")
        saved = self.core.save_file_list(docs, _list_target(path, file_list_name(name)), link=link, expires=expires)
        notes = []
        if listing.truncated:
            notes.append(_Note(f"Only the first {len(listing.documents):,} files were listed, so it may miss some: "
                               ".core.files(..., limit=...) lists more, and .core.save_file_list() saves them.", "warn"))
        if listing.errors:
            try:
                names = self.core.data_source_names(kb_id)
            except (ClientError, BotoCoreError):
                names = {}
            sources = " and ".join(names.get(ds_id) or ds_id for ds_id in listing.errors)
            notes.append(_Note(f"{sources} keeps no file list, so its files aren't in it: files() says why."))
        blocks = _file_list_blocks(saved, name, href=_notebook_href(saved.path), notes=notes)
        narrowed = [match] if match else []
        steps = []
        if saved.link != "https":
            steps.append((_call("save_file_list", *narrowed, link="https"),
                          "the same files as links that open each one in a browser, for an hour"))
        if saved.link != "s3":
            steps.append((_call("save_file_list", *narrowed), "the same files' s3:// paths"))
        steps.append((_call("files", *narrowed), "the same files with their status, and which questions search"))
        self._show(blocks + [_Next(steps)])

    @_friendly_errors
    def kbs(self, match: str | None = None) -> None:
        """
        Every knowledge base in the region you can chat with: name, ID, status, description and when it last
        changed. match= keeps those whose name, ID, ARN or description holds it, best match first: kbs('support'),
        kbs('K7QJ').
        """
        every = sorted(self.core.knowledge_bases(refresh=True), key=lambda k: k.name.lower())
        kbs = match_kbs(every, match) if match else every
        tones = {"ACTIVE": "ok", "FAILED": "bad", "DELETE_UNSUCCESSFUL": "bad"}
        rows = [[kb.name + (" (in use)" if kb.id == self.kb else ""), kb.id, _Tone(kb.status, tones.get(kb.status,
                 "warn")), human_age(kb.updated), _clip(kb.description, 80)] for kb in kbs]
        ready = [kb for kb in kbs if kb.status == "ACTIVE"]
        count = f"{len(kbs)} of {len(every)}" if match else f"{len(kbs)}"
        blocks: list[Any] = [
            _Title(f"Knowledge bases in {self.core.region} ({count})",
                   (f"matching {match!r} by name, ID or description · " if match else "") + "the ones you can chat "
                   "with"),
            _Cards([("Knowledge bases", f"{len(every):,}"), ("Active", f"{sum(kb.status == 'ACTIVE' for kb in every):,}"),
                    ("In use", self.core.kb_name(self.kb) if self.kb else "none yet")]),
            _Table(["Name", "ID", "Status", "Changed", "Description"], rows, max_rows=0, code_cols=(1,)),
        ]
        if not every:
            blocks.append(_Note("There are none here. Knowledge bases are regional: chat(region='us-west-2') looks "
                                "in another region.", "warn"))
        elif not kbs:
            close = difflib.get_close_matches(str(match).lower(), {kb.name.lower(): kb.name for kb in every}, n=3,
                                              cutoff=0.6)
            guess = [kb.name for kb in every if kb.name.lower() in close]
            blocks.append(_Note(f"No knowledge base's name, ID or description holds {match!r}."
                                + (f" Did you mean {' or '.join(map(repr, guess))}?" if guess else "")
                                + f" kbs() lists all {len(every)}.", "warn"))
        if ready:
            blocks.append(_Next([(_call("use", ready[0].name), "ask this one"),
                                 (_call("chat", ready[0].name), "the chat window on it")]))
        self._show(blocks)

    @_friendly_errors
    def models(self, match: str | None = None) -> None:
        """
        Models you can chat with here: the ID to pass as model=, provider, on demand or through an inference
        profile, and $ per 1M tokens in and out. match= keeps those whose ID, name or provider contains it.
        """
        with self._progress("Listing models", unit="models"):
            models = [m for m in self.core.models(match) if m.via != "provisioned only"]
        try:
            current = self.core.resolve_model(self.model)[0]
        except ValueError:
            current = "not offered here"
        rows = [[m.invoke_id + (" (in use)" if m.invoke_id == current else ""), m.name, m.provider,
                 m.via + (" (legacy)" if m.status == "LEGACY" else ""), _per_million(m.price_in),
                 _per_million(m.price_out)] for m in models]
        blocks: list[Any] = [
            _Title(f"Models in {self.core.region} ({len(models)})",
                   (f"matching {match!r} · " if match else "") + "text models; $ per 1M tokens at "
                   + self._price_basis(models=True)),
            _Cards([("Models", f"{len(models):,}"), ("In use", current)]),
            _Table(["Pass as model=", "Name", "Provider", "How it's called", "$ in / 1M", "$ out / 1M"], rows,
                   max_rows=0),
            _Note("Short names work too: model='opus', 'sonnet' or 'haiku' pick the current Claude model of that kind. "
                  "A model you haven't enabled fails with AccessDeniedException: enable it in the Bedrock console "
                  "under Model access."),
        ]
        if "profiles" in self.core.model_errors:
            profiles = "bedrock:ListInferenceProfiles"
            blocks.insert(2, _Note(
                f"Couldn't list inference profiles ({_why(self.core.model_errors['profiles'], profiles)}), so models "
                "that need one show 'inference profile (unknown)'.", "warn"))
        if models:
            blocks.append(_Next([(_call("use", model=models[0].invoke_id), "ask with this model")]))
        self._show(blocks)
