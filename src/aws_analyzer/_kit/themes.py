"""
How reports look: the theme a View draws them in, chosen for every report with set_theme('soft') or the
AWS_ANALYZER_THEME environment variable (THEME_ENV), else DEFAULT_THEME.

A theme is CSS drawn after a report's stylesheet that restyles its shared blocks (title, cards, findings, tables,
notes, the calls to run next). Every one of its rules is scoped to `.<root>.theme-<name>`, the class the report's box
carries, because a notebook page applies every output's <style> to the whole page: an output drawn in one theme keeps
it when a later cell is drawn in another. "{t}" in the rules stands for that selector, filled in by str.replace, not
str.format, since the rules are full of braces. "classic" has no rules: the stylesheet's own look.

The tones (warn, bad, ok, info) are custom properties, darker on a light page and lighter on a dark one. A page is
dark when JupyterLab or VS Code says so (_DARK_PAGES), or the browser prefers dark and neither says (_UNSET_PAGE);
the windows' terminal theme reads them too. The rest is mixed from currentColor, so it suits either.
"""

from __future__ import annotations

import difflib
import functools
import os
import warnings

THEME_ENV = "AWS_ANALYZER_THEME"
DEFAULT_THEME = "console"
_DARK_PAGES = ('body[data-jp-theme-light="false"]', "body.vscode-dark", "body.vscode-high-contrast")
_UNSET_PAGE = "body:not([data-jp-theme-light]):not(.vscode-light)"
_MONO = '"SF Mono",ui-monospace,Menlo,Consolas,"DejaVu Sans Mono",monospace'
_SERIF = 'Charter,"Bitstream Charter","Iowan Old Style","Palatino Linotype",Georgia,serif'

_LIGHT_TONES = ("--warn:#d97706;--bad:#dc2626;--ok:#059669;--info:#4f5bd5;"
                "--warn-fg:#92400e;--bad-fg:#991b1b;--ok-fg:#065f46;--info-fg:#3730a3")
_DARK_TONES = ("--warn:#f59e0b;--bad:#f87171;--ok:#34d399;--info:#8b95ff;"
               "--warn-fg:#fcd34d;--bad-fg:#fca5a5;--ok-fg:#6ee7b7;--info-fg:#c7ccff")

_SHARED_RULES = """\
{t} .cards{display:flex;flex-wrap:wrap}
{t} .card{flex:1 1 auto;min-width:128px;max-width:280px}
{t} .card .v{white-space:nowrap}
{t} .pill{white-space:nowrap}
{t} h4 code{text-transform:none;letter-spacing:0}
{t} table.t th,{t} table.t td{padding-left:10px;padding-right:10px}"""

_SOFT = """\
{t}{--line:color-mix(in srgb,currentColor 11%,transparent);--surface:color-mix(in srgb,currentColor 3.5%,transparent);--muted:color-mix(in srgb,currentColor 58%,transparent)}
{t} h3{display:flex;align-items:center;gap:10px;margin:6px 0 4px;font-size:21px;font-weight:650;letter-spacing:-.015em}
{t} h3 .badge{margin:0;padding:3px 9px;border-radius:7px;font-size:10.5px;letter-spacing:.06em;background:color-mix(in srgb,var(--info) 13%,transparent);color:var(--info-fg)}
{t} .sub{opacity:1;color:var(--muted);font-size:12.5px;margin-bottom:16px}
{t} .cards{gap:10px;margin:0 0 22px}
{t} .card,{t} .card.warn,{t} .card.bad,{t} .card.ok{border:1px solid var(--line);background:var(--surface);border-radius:12px;padding:12px 14px}
{t} .card .l{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:550;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);opacity:1}
{t} .card .v{margin-top:4px;font-size:19px;font-weight:650;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
{t} .card.warn .l::before,{t} .card.bad .l::before,{t} .card.ok .l::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--warn)}
{t} .card.bad .l::before{background:var(--bad)}
{t} .card.ok .l::before{background:var(--ok)}
{t} .card.warn .v{color:var(--warn-fg)}
{t} .card.bad .v{color:var(--bad-fg)}
{t} .fh{margin:0 0 8px;font-size:13px}
{t} .fh .fl{opacity:1;margin-right:4px;font-size:15px;font-weight:650}
{t} .chip{border-radius:99px;padding:1px 9px;line-height:19px}
{t} .chip.warn{background:color-mix(in srgb,var(--warn) 14%,transparent);box-shadow:none;color:var(--warn-fg)}
{t} .chip.info{background:color-mix(in srgb,var(--info) 12%,transparent);box-shadow:none;color:var(--info-fg)}
{t} .fi{grid-template-columns:26px minmax(0,1fr);column-gap:10px;margin:8px 0;padding:12px 16px 12px 12px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
{t} .fi.warn{border-color:color-mix(in srgb,var(--warn) 30%,transparent);background:color-mix(in srgb,var(--warn) 5%,transparent)}
{t} .fi::before{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;font-size:12px;opacity:1;background:color-mix(in srgb,var(--info) 15%,transparent);color:var(--info-fg)}
{t} .fi.warn::before{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn-fg)}
{t} .fi .hd{padding-top:2px;font-size:13.5px}
{t} .fi .dt{opacity:1;color:color-mix(in srgb,currentColor 78%,transparent)}
{t} h4,{t} form.tbl h4,{t} .tt h4{margin:26px 0 8px;font-size:15px;font-weight:650}
{t} .tw{width:fit-content;border:1px solid var(--line);border-radius:12px}
{t} table.t th{padding-top:9px;padding-bottom:9px;border-bottom:1px solid var(--line);background-image:linear-gradient(var(--surface),var(--surface));font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
{t} table.t td{padding-top:8px;padding-bottom:8px;border-bottom:1px solid var(--line)}
{t} table.t tbody tr:last-child td{border-bottom:0}
{t} table.t tbody tr:hover td{background:var(--surface)}
{t} .pill{border-radius:99px;padding:1px 9px}
{t} .pill.warn{background:color-mix(in srgb,var(--warn) 15%,transparent);box-shadow:none;color:var(--warn-fg)}
{t} .pill.bad{background:color-mix(in srgb,var(--bad) 13%,transparent);box-shadow:none;color:var(--bad-fg)}
{t} .pill.ok{background:color-mix(in srgb,var(--ok) 14%,transparent);box-shadow:none;color:var(--ok-fg)}
{t} .track{height:6px;border-radius:99px;background:var(--line)}
{t} .fill{border-radius:99px;background:var(--info)}
{t} code{padding:1px 5px;border:1px solid var(--line);border-radius:6px;background:color-mix(in srgb,currentColor 7%,transparent);font-size:11.5px}
{t} .note{margin:20px 0 4px;padding:11px 14px;border:1px solid color-mix(in srgb,var(--info) 25%,transparent);border-radius:12px;background:color-mix(in srgb,var(--info) 6%,transparent)}
{t} .note.warn{border-color:color-mix(in srgb,var(--warn) 30%,transparent);background:color-mix(in srgb,var(--warn) 7%,transparent)}
{t} .note.ok{border-color:color-mix(in srgb,var(--ok) 30%,transparent);background:color-mix(in srgb,var(--ok) 7%,transparent)}
{t} .flt select,{t} .cols>summary{border-color:var(--line);border-radius:8px;padding:2px 8px}
{t} .next{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:8px;margin:18px 0 4px;padding:0;border-top:0}
{t} .next .nl{grid-column:1/-1;opacity:1;color:var(--muted);letter-spacing:.06em}
{t} .next .ni{display:flex;flex-direction:column;gap:4px;padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
{t} .next .ni code{align-self:flex-start;padding:0;border:0;background:none;font-size:12.5px;font-weight:600;color:var(--info-fg)}
{t} .next .ni code::after{content:"  \\2192"}
{t} .next .nw{margin:0;font-size:12px}"""

_EDITORIAL = """\
{t}{line-height:1.55;--line:color-mix(in srgb,currentColor 12%,transparent);--muted:color-mix(in srgb,currentColor 55%,transparent)}
{t} h3{margin:4px 0 6px;font-family:%SERIF%;font-size:27px;font-weight:700;letter-spacing:-.01em}
{t} h3 .badge{display:block;width:fit-content;margin:0 0 8px;padding:0;border-radius:0;background:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:.14em;color:var(--warn)}
{t} .sub{max-width:70ch;margin-bottom:22px;opacity:1;color:var(--muted);font-size:13px}
{t} .cards{gap:0;margin:0 0 30px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
{t} .card,{t} .card.warn,{t} .card.bad,{t} .card.ok{position:relative;flex:0 1 auto;max-width:none;margin-right:22px;padding:14px 22px 14px 0;border:0;border-radius:0;background:none}
{t} .card:not(:last-child)::after{content:"";position:absolute;top:16px;right:0;bottom:16px;width:1px;background:var(--line)}
{t} .card .l{opacity:1;color:var(--muted);font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.09em}
{t} .card .v{margin-top:2px;font-size:22px;font-weight:500;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
{t} .card.warn .v{color:var(--warn)}
{t} .card.bad .v{color:var(--bad)}
{t} .card.ok .v{color:var(--ok)}
{t} .fh{margin:0 0 4px;font-size:12px}
{t} .fh .fl{opacity:1;color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.09em}
{t} .chip,{t} .chip.warn,{t} .chip.info{padding:0 6px 0 0;background:none;box-shadow:none;font-size:12px}
{t} .chip.warn{color:var(--warn)}
{t} .chip.info{color:var(--muted)}
{t} .fi,{t} .fi.warn{grid-template-columns:22px minmax(0,1fr);column-gap:8px;margin:0;padding:14px 0;border:0;border-bottom:1px solid var(--line);border-radius:0;background:none}
{t} .fi::before{content:"";width:8px;height:8px;margin-top:7px;border-radius:50%;opacity:1;background:color-mix(in srgb,currentColor 35%,transparent)}
{t} .fi.warn::before{content:"";background:var(--warn);box-shadow:0 0 0 4px color-mix(in srgb,var(--warn) 18%,transparent)}
{t} .fi .hd{font-size:14.5px;letter-spacing:-.005em}
{t} .fi .dt{margin-top:3px;opacity:1;color:var(--muted)}
{t} .fi ul.dt{padding-left:14px}
{t} h4,{t} form.tbl h4,{t} .tt h4{margin:34px 0 8px;color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.09em}
{t} table.t th{padding-top:8px;padding-bottom:8px;border-bottom:1.5px solid color-mix(in srgb,currentColor 55%,transparent);background-image:none;color:var(--muted);font-size:11.5px}
{t} .tw.scroll table.t th{box-shadow:inset 0 -1.5px color-mix(in srgb,currentColor 55%,transparent)}
{t} table.t td{padding-top:9px;padding-bottom:9px;border-bottom:1px solid var(--line)}
{t} table.t th:first-child,{t} table.t td:first-child{padding-left:0}
{t} table.t tbody tr:hover td{background:color-mix(in srgb,currentColor 3%,transparent)}
{t} .pill,{t} .pill.warn,{t} .pill.bad,{t} .pill.ok{padding:0;border-radius:0;background:none;box-shadow:none}
{t} .pill::before{content:"\\25CF  ";font-size:9px;vertical-align:1px}
{t} .pill.warn{color:var(--warn)}
{t} .pill.bad{color:var(--bad)}
{t} .pill.ok{color:var(--ok)}
{t} .track{height:3px;border-radius:0;background:var(--line)}
{t} .fill{border-radius:0;background:currentColor;opacity:.75}
{t} code{padding:0;border-radius:0;border-bottom:1px dotted color-mix(in srgb,currentColor 45%,transparent);background:none;color:var(--info-fg)}
{t} .note,{t} .note.warn,{t} .note.ok{margin:28px 0 8px;padding:2px 0 2px 16px;border:0;border-left:2px solid currentColor;border-radius:0;background:none;font-family:%SERIF%;font-size:14.5px;font-style:italic;color:color-mix(in srgb,currentColor 80%,transparent)}
{t} .note.warn{border-left-color:var(--warn)}
{t} .note.ok{border-left-color:var(--ok)}
{t} .note::before,{t} .note.warn::before,{t} .note.ok::before{content:none}
{t} .note .ld{font-style:normal}
{t} .flt select,{t} .cols>summary{padding:0 2px;border:0;border-bottom:1px solid var(--line);border-radius:0}
{t} .next{display:block;margin-top:30px;padding-top:12px;border-top:1.5px solid color-mix(in srgb,currentColor 55%,transparent)}
{t} .next .nl{display:block;margin-bottom:6px;opacity:1;color:var(--muted);letter-spacing:.09em}
{t} .next .ni{display:block;padding:4px 0}
{t} .next .ni code{border:0;font-size:13px;font-weight:600}
{t} .next .ni code::before{content:"\\2192  ";color:var(--muted)}
{t} .next .nw{margin-left:12px;opacity:1;color:var(--muted);font-size:13px}""".replace("%SERIF%", _SERIF)

_CONSOLE = """\
{t}{--accent:#e8860c;--line:color-mix(in srgb,currentColor 14%,transparent);--panel:color-mix(in srgb,currentColor 3%,transparent);--head:color-mix(in srgb,currentColor 6%,transparent);--muted:color-mix(in srgb,currentColor 60%,transparent)}
{t} h3{display:flex;align-items:center;gap:10px;margin:0 0 16px;padding:14px 16px;border:1px solid var(--line);border-radius:8px;background:var(--head);font-size:18px;font-weight:650}
{t} h3:has(+.sub){margin-bottom:0;border-bottom:0;border-radius:8px 8px 0 0}
{t} h3 .badge{margin:0;padding:3px 7px;border-radius:4px;background:var(--accent);color:#fff;font-size:10px;letter-spacing:.08em}
{t} .sub{margin:0 0 16px;padding:8px 16px;border:1px solid var(--line);border-radius:0 0 8px 8px;opacity:1;color:var(--muted);font-family:%MONO%;font-size:11.5px}
{t} .cards{gap:0;margin:0 0 18px;border:1px solid var(--line);border-radius:8px;overflow:hidden}
{t} .card,{t} .card.warn,{t} .card.bad,{t} .card.ok{flex:1 1 auto;max-width:none;margin:0 -1px -1px 0;padding:10px 14px 11px;border:0;border-right:1px solid var(--line);border-bottom:1px solid var(--line);border-radius:0;background:var(--panel);box-shadow:inset 0 3px 0 color-mix(in srgb,currentColor 18%,transparent)}
{t} .card.warn{box-shadow:inset 0 3px 0 var(--warn)}
{t} .card.bad{box-shadow:inset 0 3px 0 var(--bad)}
{t} .card.ok{box-shadow:inset 0 3px 0 var(--ok)}
{t} .card .l{opacity:1;color:var(--muted);font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.07em}
{t} .card .v{margin-top:3px;font-family:%MONO%;font-size:15px;font-weight:600}
{t} .fh{margin:0;padding:9px 14px;border:1px solid var(--line);border-bottom:0;border-radius:8px 8px 0 0;background:var(--head)}
{t} .fh .fl{opacity:1;font-size:12px;text-transform:uppercase;letter-spacing:.07em}
{t} .chip{border-radius:4px;font-family:%MONO%;font-size:10.5px}
{t} .chip.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);box-shadow:none;color:var(--warn-fg)}
{t} .chip.info{background:color-mix(in srgb,var(--info) 14%,transparent);box-shadow:none;color:var(--info-fg)}
{t} .fd{margin:6px 0 16px}
{t} .fi,{t} .fi.warn{grid-template-columns:52px minmax(0,1fr);column-gap:12px;margin:0;padding:10px 14px;border:1px solid var(--line);border-top:0;border-left:3px solid var(--info);border-radius:0;background:var(--panel)}
{t} .fi.warn{border-left-color:var(--warn)}
{t} .fi:last-child{border-radius:0 0 8px 0}
{t} .fi::before{content:"INFO";align-self:start;margin-top:2px;padding:1px 0;border-radius:3px;opacity:1;background:color-mix(in srgb,var(--info) 14%,transparent);color:var(--info-fg);font-family:%MONO%;font-size:10px;font-weight:700;letter-spacing:.04em;text-align:center}
{t} .fi.warn::before{content:"WARN";background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn-fg)}
{t} .fi .dt{opacity:1;color:var(--muted)}
{t} h4,{t} form.tbl h4,{t} .tt h4{margin:22px 0 6px;font-size:12px;font-weight:650;text-transform:uppercase;letter-spacing:.07em}
{t} .tw{width:fit-content;border:1px solid var(--line);border-radius:8px}
{t} table.t th{padding-top:7px;padding-bottom:7px;border-bottom:1px solid var(--line);background-image:linear-gradient(var(--head),var(--head));font-size:11.5px}
{t} table.t td{padding-top:6px;padding-bottom:6px;border-bottom:0}
{t} table.t tbody tr:nth-child(even) td{background:var(--panel)}
{t} table.t tbody tr:hover td{background:color-mix(in srgb,var(--accent) 8%,transparent)}
{t} table.t td.n,{t} table.t td.bar{font-family:%MONO%;font-size:12px}
{t} th.srt:hover,{t} th.srt:has(.ra:checked),{t} th.srt:has(.rd:checked){color:var(--accent)}
{t} .pill{padding:1px 6px;border-radius:4px;font-family:%MONO%;font-size:11px}
{t} .pill.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);box-shadow:none;color:var(--warn-fg)}
{t} .pill.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);box-shadow:none;color:var(--bad-fg)}
{t} .pill.ok{background:color-mix(in srgb,var(--ok) 16%,transparent);box-shadow:none;color:var(--ok-fg)}
{t} .track{height:10px}
{t} .fill{background:var(--accent)}
{t} code{border-radius:3px;background:color-mix(in srgb,currentColor 8%,transparent);font-family:%MONO%;font-size:11.5px}
{t} .note{margin:18px 0 4px;padding:9px 14px;border:1px solid var(--line);border-left:3px solid var(--info);border-radius:0 8px 8px 0;background:var(--panel)}
{t} .note.warn{border-left-color:var(--warn)}
{t} .note.ok{border-left-color:var(--ok)}
{t} .flt select,{t} .cols>summary{border-color:var(--line);border-radius:4px}
{t} .tbl button[type=reset]{color:var(--accent)}
{t} .next{gap:6px 22px;margin-top:18px;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
{t} .next .nl{opacity:1;color:var(--accent)}
{t} .next .ni code{padding:0;background:none;font-weight:600}
{t} .next .ni code::before{content:"$ ";color:var(--muted)}""".replace("%MONO%", _MONO)

_BRAND = """\
{t}{--ink:#172036;--on-ink:#ffffff;--blue:#3949c6;--blue-soft:#eceefc;--mark:#fce7a2;--mark-fg:#172036;--card:#ffffff;--shadow:0 1px 2px rgba(23,32,54,.06),0 4px 14px rgba(23,32,54,.07);--line:color-mix(in srgb,currentColor 10%,transparent);--muted:color-mix(in srgb,currentColor 58%,transparent)}
{t} h3{margin:4px 0;color:var(--ink);font-size:22px;font-weight:750;letter-spacing:-.02em}
{t} h3 .badge{margin-right:12px;padding:3px 8px;border-radius:6px;vertical-align:4px;background:var(--ink);color:var(--on-ink);font-size:10px;letter-spacing:.08em}
{t} .sub{margin-bottom:18px;opacity:1;color:var(--muted);font-size:12.5px}
{t} .cards{gap:12px;margin:0 0 24px}
{t} .card,{t} .card.warn,{t} .card.bad,{t} .card.ok{padding:13px 16px;border:0;border-radius:14px;background:var(--card);box-shadow:var(--shadow)}
{t} .card.bad{box-shadow:var(--shadow),inset 0 0 0 1.5px color-mix(in srgb,var(--bad) 55%,transparent)}
{t} .card .l{opacity:1;color:var(--muted);font-size:11.5px}
{t} .card .v{margin-top:3px;color:var(--ink);font-size:20px;font-weight:700;letter-spacing:-.015em;font-variant-numeric:tabular-nums}
{t} .card.warn .v,{t} .card.bad .v{display:inline;margin-left:-2px;padding:0 2px;background:linear-gradient(transparent 52%,var(--mark) 52%,var(--mark) 92%,transparent 92%);color:var(--mark-fg)}
{t} .card.bad .v{background:linear-gradient(transparent 52%,color-mix(in srgb,var(--bad) 22%,transparent) 52%,color-mix(in srgb,var(--bad) 22%,transparent) 92%,transparent 92%);color:var(--bad-fg)}
{t} .fh{margin:0 0 10px}
{t} .fh .fl{opacity:1;color:var(--ink);font-size:16px;font-weight:750;letter-spacing:-.01em}
{t} .chip{border-radius:99px;padding:1px 9px;line-height:19px}
{t} .chip.warn{background:var(--mark);box-shadow:none;color:var(--mark-fg)}
{t} .chip.info{background:var(--blue-soft);box-shadow:none;color:var(--blue)}
{t} .fi,{t} .fi.warn{grid-template-columns:28px minmax(0,1fr);column-gap:10px;margin:10px 0;padding:13px 18px 13px 14px;border:0;border-radius:14px;background:var(--card);box-shadow:var(--shadow)}
{t} .fi::before{display:grid;place-items:center;width:24px;height:24px;border-radius:8px;opacity:1;background:var(--blue-soft);color:var(--blue);font-size:12px}
{t} .fi.warn::before{background:var(--mark);color:var(--mark-fg)}
{t} .fi .hd{padding-top:2px;color:var(--ink);font-size:13.5px;font-weight:650}
{t} .fi .dt{opacity:1;color:color-mix(in srgb,currentColor 75%,transparent)}
{t} .m{padding:0 1px;background:linear-gradient(transparent 55%,var(--mark) 55%,var(--mark) 92%,transparent 92%);color:var(--mark-fg)}
{t} h4,{t} form.tbl h4,{t} .tt h4{margin:30px 0 10px;color:var(--ink);font-size:16px;font-weight:750;letter-spacing:-.01em}
{t} .tw{width:fit-content;border-radius:14px;background:var(--card);box-shadow:var(--shadow)}
{t} table.t th{padding-top:11px;padding-bottom:8px;border-bottom:1px solid var(--line);background-image:none;color:var(--muted);font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.07em}
{t} table.t td{padding-top:8px;padding-bottom:8px;border-bottom:1px solid var(--line)}
{t} table.t tbody tr:last-child td{border-bottom:0}
{t} table.t tbody tr:hover td{background:color-mix(in srgb,var(--blue) 5%,transparent)}
{t} th.srt:hover,{t} th.srt:has(.ra:checked),{t} th.srt:has(.rd:checked){color:var(--blue)}
{t} .pill{padding:1px 8px;border-radius:6px;font-weight:650}
{t} .pill.warn{background:var(--mark);box-shadow:none;color:var(--mark-fg)}
{t} .pill.bad{background:color-mix(in srgb,var(--bad) 14%,transparent);box-shadow:none;color:var(--bad-fg)}
{t} .pill.ok{background:color-mix(in srgb,var(--ok) 14%,transparent);box-shadow:none;color:var(--ok-fg)}
{t} .track{height:7px;border-radius:99px;background:var(--blue-soft)}
{t} .fill{border-radius:99px;background:var(--blue)}
{t} code{padding:1px 6px;border-radius:6px;background:var(--blue-soft);color:var(--blue);font-size:11.5px}
{t} .note,{t} .note.warn,{t} .note.ok{margin:24px 0 4px;padding:12px 16px;border:0;border-radius:14px;background:var(--blue-soft)}
{t} .note.warn{background:color-mix(in srgb,var(--mark) 70%,transparent)}
{t} .note.ok{background:color-mix(in srgb,var(--ok) 12%,transparent)}
{t} .note::before{opacity:1;color:var(--blue)}
{t} .flt select,{t} .cols>summary{border-color:var(--line);border-radius:8px}
{t} .tbl button[type=reset]{color:var(--blue)}
{t} .next{gap:8px 24px;margin:20px 0 4px;padding:14px 18px;border-top:0;border-radius:14px;background:var(--ink);color:var(--on-ink)}
{t} .next .nl,{t} .next .nw{opacity:.75}
{t} .next code{background:color-mix(in srgb,var(--on-ink) 14%,transparent);color:var(--on-ink)}"""

_BRAND_DARK = ("--ink:#e6e9f4;--on-ink:#151a2b;--blue:#a3adff;--blue-soft:#222a4d;--mark:rgba(245,196,81,.24);"
               "--mark-fg:#fbe6ad;--card:color-mix(in srgb,currentColor 6%,transparent);"
               "--shadow:0 1px 2px rgba(0,0,0,.3),0 6px 18px rgba(0,0,0,.25)")

THEMES = {
    "console": "a dense dashboard, like the AWS console: boxed panels, WARN / INFO tags, monospace numbers",
    "soft": "rounded tiles on a light fill, small capitals over the numbers and columns, calls to run as tiles",
    "editorial": "almost no boxes: a serif title, the numbers as a row of large figures, findings as a list",
    "brand": "the guide site's look: navy and indigo, white cards with soft shadows, amber highlighter on what "
             "needs a look",
    "classic": "the look before themes: thin borders and tinted boxes",
}
_RULES = {"console": _CONSOLE, "soft": _SOFT, "editorial": _EDITORIAL, "brand": _BRAND, "classic": ""}
_DARK_EXTRA = {"brand": _BRAND_DARK}
_chosen: str | None = None
_warned_env: set[str] = set()


def _theme_from(value: str) -> str:
    """
    What set_theme() takes -> a theme's name. Any case and spaces around it work; a name it doesn't know raises a
    ValueError that lists the themes, with the closest one first.
    """
    if not isinstance(value, str):
        raise TypeError(f"set_theme() takes a theme's name, like 'soft'; got {value!r}")
    name = value.strip().lower()
    if name in THEMES:
        return name
    close = difflib.get_close_matches(name, list(THEMES), n=1, cutoff=0.6)
    hint = f" Did you mean {close[0]!r}?" if close else ""
    raise ValueError(f"{value!r} isn't a theme.{hint} The themes are {', '.join(map(repr, THEMES))}")


def set_theme(name: str | None) -> str:
    """
    Pick how reports look: set_theme('soft'). Every report drawn from then on uses that theme; the ones already in
    the notebook keep the look they were drawn with. Returns the theme's name.

    The themes are 'console' (the default: boxed panels, WARN / INFO tags, monospace numbers), 'soft' (rounded
    tiles on a light fill), 'editorial' (a serif title, large figures, almost no boxes), 'brand' (the guide site's
    navy, indigo and amber highlighter) and 'classic' (thin borders and tinted boxes, the look before themes). Each
    suits a light or a dark notebook. None goes back to the default: the theme the AWS_ANALYZER_THEME environment
    variable names, else 'console'. The windows (explorers and chats) keep their own look, and text output is the
    same in every theme.
    """
    global _chosen
    _chosen = None if name is None else _theme_from(name)
    return _theme_name()


def _theme_name() -> str:
    """
    The theme reports are drawn in: set_theme()'s, else the one AWS_ANALYZER_THEME names, else DEFAULT_THEME. A
    name in the variable that isn't a theme is said once, as a warning, and the default is used.
    """
    if _chosen is not None:
        return _chosen
    named = os.environ.get(THEME_ENV, "").strip()
    if not named:
        return DEFAULT_THEME
    try:
        return _theme_from(named)
    except ValueError as exc:
        if named not in _warned_env:
            _warned_env.add(named)
            warnings.warn(f"{THEME_ENV}={named!r}: {exc}. Reports use {DEFAULT_THEME!r}.", stacklevel=2)
        return DEFAULT_THEME


def _theme_class(name: str) -> str:
    """
    The class a report's box carries in a theme: 'theme-soft'.
    """
    return f"theme-{name}"


def _dark_rules(scoped: str, tokens: str) -> str:
    """
    The custom properties `tokens` on `scoped` when the page is dark: JupyterLab's or VS Code's dark themes, or a
    browser that prefers dark on a page that says neither.
    """
    pages = ",".join(f"{page} {scoped}" for page in _DARK_PAGES)
    return f"{pages}{{{tokens}}}\n@media (prefers-color-scheme:dark){{{_UNSET_PAGE} {scoped}{{{tokens}}}}}"


@functools.lru_cache(maxsize=None)
def _theme_css(root: str, name: str) -> str:
    """
    The <style> that draws a report of root class `root` in theme `name`, every rule scoped to `.<root>.theme-<name>`
    ('' for 'classic', or a name that isn't a theme).
    """
    rules = _RULES.get(name, "")
    if not rules:
        return ""
    scoped = f".{root}.{_theme_class(name)}"
    dark_tokens = ";".join(filter(None, [_DARK_TONES, _DARK_EXTRA.get(name, "")]))
    parts = [f"{scoped}{{{_LIGHT_TONES}}}", _dark_rules(scoped, dark_tokens), _SHARED_RULES, rules]
    return "<style>\n" + "\n".join(parts).replace("{t}", scoped) + "\n</style>"
