"""How reports are drawn: the stylesheet every report starts with, written once for every service.

Each service's reports sit in an HTML box of its own root class (.ddb, .kba, ...), so the shared rules are written
with `{root}` in its place, and `_base_css(root, extra)` fills it in and adds the rules for the service's own blocks.
"""

from __future__ import annotations

# The rules every service's reports share. "{root}" stands for the service's root class (filled in by str.replace,
# not str.format: the rules are full of braces).
_BASE_RULES = """\
.{root}{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.{root} h3{margin:10px 0 2px;font-size:16px}
.{root} h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.{root} h4{margin:14px 0 4px;font-size:13px}
.{root} .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.{root} .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.{root} .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.{root} .card.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.{root} .card.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.{root} .card.ok{border-color:rgba(16,185,129,.7)}
.{root} .card .l{font-size:11px;opacity:.65}
.{root} .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.{root} .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.{root} .tw.scroll{max-height:640px;overflow:auto}
.{root} table.t{border-collapse:collapse;width:auto;font-size:inherit}
.{root} table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.{root} .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.{root} .tw.scroll table.t th{background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.{root} table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.{root} table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.{root} table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.{root} table.t td.s{white-space:nowrap}
.{root} table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.{root} table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.{root} table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.{root} .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.{root} .track{vertical-align:middle;margin-right:6px}
.{root} .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.{root} .pill{display:inline-block;padding:0 7px;border-radius:9px;font-weight:600;font-size:12px}
.{root} .pill.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.6)}
.{root} .pill.bad{background:rgba(239,68,68,.16);box-shadow:inset 0 0 0 1px rgba(239,68,68,.6)}
.{root} .pill.ok{background:rgba(16,185,129,.14);box-shadow:inset 0 0 0 1px rgba(16,185,129,.55)}
.{root} .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.{root} .note::before{content:"\\2139\\FE0E";margin-right:7px;opacity:.7}
.{root} .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.{root} .note.warn::before{content:"\\26A0\\FE0E"}
.{root} .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.{root} .note.ok::before{content:"\\2713"}
.{root} .fh{font-size:12px;font-weight:600;opacity:.75;margin:10px 0 2px}
.{root} code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:0 4px;border-radius:4px}
.{root} code{background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
.{root} .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.{root} pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.{root} pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.5;max-height:560px}
.{root} pre.code{user-select:all;-webkit-user-select:all;cursor:text}
.{root} .hint{font-weight:400;font-size:11px;opacity:.55;margin-left:8px}
.{root} details.sec{margin:14px 0 4px}
.{root} details.sec>summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.{root} .next{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:12px 0 4px;padding-top:8px}
.{root} .next{border-top:1px dashed rgba(127,127,127,.35)}
.{root} .next .nl{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.{root} .next .nw{font-size:12px;opacity:.65;margin-left:6px}"""


def _base_css(root: str, extra: str = "", *, theme: str = "", restyle: dict[str, str] | None = None,
              after: dict[str, str] | None = None) -> str:
    """The <style> every report starts with: the shared rules for the root class `root`, then `extra`, the rules
    for the service's own blocks.

    A service whose reports look different (the chat's match its window) changes some shared rules: `theme` goes
    first (the custom properties its rules use), `restyle` replaces a shared rule and `after` adds rules after one.
    Their keys are how the shared rule starts after `.<root> ` ("h3 .badge{", "code{background:"); one that matches
    no rule, or more than one, raises ValueError, so a change to the shared rules can't silently undo a restyle."""
    rules = _BASE_RULES.replace("{root}", root).split("\n")
    for edits, keep in ((restyle or {}, False), (after or {}, True)):
        for start, new in edits.items():
            found = [i for i, rule in enumerate(rules) if rule.startswith(f".{root} {start}")]
            if len(found) != 1:
                raise ValueError(f"{start!r} starts {len(found)} of the shared CSS rules, not one")
            rules[found[0]] = f"{rules[found[0]]}\n{new}" if keep else new
    return "\n".join(["<style>", *filter(None, [theme, "\n".join(rules), extra]), "</style>"])
