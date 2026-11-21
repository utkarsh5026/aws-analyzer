"""
The explorer's Schema tab: what the open table's items hold, from about a thousand items read from across it (as
schema() reads them). Every attribute is a line, fields of a map under their map, with chips for the ones not on
every item and the ones holding more than one type; on the right the whole schema report, and a click on an attribute
counts its values there, as value_counts() does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import search_rank
from ..._kit.window import _skeleton

from ..models import AttributeProfile, TableProfile, ValueCounts
from .frame import _attribute_face
from .widgets import _Chip, _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import DynamoDBExplorer


SCHEMA_ITEMS = 1000

COUNT_LIMIT = 10_000

_SCHEMA_CHIPS = (
    _Chip("", "All", "all", "Every attribute, fields of a map under their map"),
    _Chip("missing", "Not on every item", "warn", "The attributes some items don't have"),
    _Chip("mixed", "Mixed types", "warn", "The attributes holding more than one type (a number here, text there)"),
    _Chip("maps", "Maps and lists", "none", "The attributes holding a map or a list"),
)


def _in_schema_chip(attribute: AttributeProfile, chip: str, items: int) -> bool:
    """
    Whether an attribute belongs under a chip ('' is every one).
    """
    if chip == "missing":
        return attribute.count < items
    if chip == "mixed":
        return len(attribute.types) > 1
    if chip == "maps":
        return attribute.main_type in ("M", "L")
    return True


def _attribute_text(attribute: AttributeProfile) -> tuple[str, ...]:
    """
    What the Schema tab's search box looks through: the attribute's path, its types and its examples.
    """
    examples = " ".join(str(value) for value in attribute.examples)
    return (attribute.path, " ".join(attribute.types), examples)


class _SchemaTab:
    """
    The Schema tab, a part of DynamoDBExplorer: attribute_list is its page, profile what was read for the open table
    (None before), and counts the values counted so far, by attribute path.
    """

    def _schema_page(self: DynamoDBExplorer) -> Any:
        self.profile: TableProfile | None = None
        self.counts: dict[str, ValueCounts] = {}
        self.attribute_list = _ListPage(self, placeholder="Search: attribute name, type, example value…",
                                        chips=_SCHEMA_CHIPS, on_click=self._clicked_attribute)
        self.attribute_list.redraw = self._draw_attribute_rows
        return self.attribute_list.page

    def _read_schema(self: DynamoDBExplorer) -> None:
        """
        Reads about SCHEMA_ITEMS of the open table's items from across it in the background, and profiles them.
        """
        table = self.table_name
        self.attribute_list.selected = ""
        self._set(self.attribute_list.view, _skeleton(f"Reading items from across {table}…"))
        self._status(f"Reading about {SCHEMA_ITEMS:,} items from across {table}…", busy=True, owner="schema")
        self._later("schema", lambda: self.core.profile(table, SCHEMA_ITEMS), self._got_profile, self._schema_failed)

    def _got_profile(self: DynamoDBExplorer, profile: TableProfile) -> None:
        self.profile = profile
        self._draw_attribute_rows()
        self._draw("schema", self.attribute_list.view, self.ui._schema_blocks(profile))
        attributes = sum(1 for a in profile.attributes.values() if a.depth == 0)
        self._finish("schema", f"{attributes:,} attributes in {profile.items:,} items: click one to count its values.",
                     tabs=("schema",))

    def _schema_failed(self: DynamoDBExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._draw("schema", self.attribute_list.view, [_Note(text, "warn")])

    def _roles(self: DynamoDBExplorer) -> dict[str, str]:
        info = self.reports[self.table_name].info
        roles = {info.partition_key: "partition key"}
        if info.sort_key:
            roles[info.sort_key] = "sort key"
        return roles

    def _draw_attribute_rows(self: DynamoDBExplorer) -> None:
        page, profile = self.attribute_list, self.profile
        attributes = list(profile.attributes.values()) if profile else []
        items = profile.items if profile else 0
        page.draw_chips({chip.key: sum(1 for a in attributes if _in_schema_chip(a, chip.key, items))
                         for chip in _SCHEMA_CHIPS})
        matching = [a for a in attributes if _in_schema_chip(a, page.chip, items)]
        if page.query:
            matching = [a for a in matching if search_rank(page.query, _attribute_text(a)) is not None]
        roles = self._roles() if profile else {}
        words = page.query.split()
        lines = [_Shown(a.path, _attribute_face(a, items=items, role=roles.get(a.path, "") if a.depth == 0 else "",
                                                words=words))
                 for a in matching]
        empty = "No attribute matches the search." if page.query or page.chip else "No items to profile."
        page.fill(lines, total=len(attributes), empty=empty, noun="attributes")

    def _clicked_attribute(self: DynamoDBExplorer, line: _Line) -> None:
        if line.key:
            self._open_attribute(line.key)

    def _open_attribute(self: DynamoDBExplorer, path: str) -> None:
        """
        An attribute's values counted on the right of the Schema tab: at once when they were counted before, else
        among the first COUNT_LIMIT items read in the background.
        """
        self.attribute_list.mark(path)
        if path in self.counts:
            self._draw_counts(self.counts[path])
            return
        table = self.table_name
        self._set(self.attribute_list.view, _skeleton(f"Counting the values of {path}…"))
        self._status(f"Counting the values of {path} in up to {COUNT_LIMIT:,} items…", busy=True, owner="counts")
        self._later("counts", lambda: self.core.value_counts(table, path, limit=COUNT_LIMIT), self._got_counts,
                    self._schema_failed)

    def _got_counts(self: DynamoDBExplorer, counts: ValueCounts) -> None:
        self.counts[counts.attribute] = counts
        if self.attribute_list.selected == counts.attribute:
            self._draw_counts(counts)
        self._finish("counts", f"{len(counts.counts):,} distinct values of {counts.attribute} in "
                               f"{counts.stats.scanned:,} items read.", tabs=("schema",))

    def _draw_counts(self: DynamoDBExplorer, counts: ValueCounts) -> None:
        self._draw("schema", self.attribute_list.view, self.ui._value_counts_blocks(counts, limit=COUNT_LIMIT))

