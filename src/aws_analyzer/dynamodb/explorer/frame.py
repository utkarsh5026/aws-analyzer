"""
What the DynamoDB explorer window is drawn with: its tabs, stylesheet and colours (the Athena explorer's console look,
on the windows' terminal theme), the sentences from reports reworded for the window, and the faces of its lines (a
table, an item, an attribute).
"""

from __future__ import annotations

import re
from typing import Any

from ..._kit.fmt import _count, _share, human_money, human_size
from ..._kit.render import _CALL_RE
from ..._kit.text import _clip, _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..helpers import KB, TYPE_NAMES, format_value, item_size
from ..models import AttributeProfile, TableReport


_EXPLORER_TABS = (
    ("overview", "Overview", "Every table at a glance: items, size, cost and what to fix first",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("tables", "Tables", "Every table, with what's wrong with it: click one for its keys, indexes, usage and cost",
     "<ellipse cx='12' cy='6' rx='7.5' ry='2.8'/><path d='M4.5 6v12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6'/>"
     "<path d='M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8'/>"),
    ("items", "Items", "The open table's items: a sample, from the start, or one partition key's; click one for all of it",
     "<rect x='3.5' y='4.5' width='17' height='15' rx='2'/><path d='M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15'/>"),
    ("schema", "Schema", "What the open table's items hold: every attribute's types and fill rate; click one for its "
     "values", "<path d='M5 4.5h5v5H5zM14 14.5h5v5h-5zM7.5 9.5v7h6.5M14 7h5'/>"),
)

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/>'
         '<path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>'
         '</svg>')

_NOTE_CHARS = 120

_VALUE_CHARS = 28

_NOTE_ATTRIBUTES = 4

_LARGE_ITEM = 300 * KB


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .ddx-i-<key> sets --tx-icon, which a tab's ::before draws.
    """
    return "\n".join(f".ddx-app .ddx-i-{key}{{--tx-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--tx-accent:#60a5fa;--tx-accent-2:#f472b6;--tx-soft:rgba(96,165,250,.15);--tx-ring:rgba(96,165,250,"
                  ".38);--tx-raised:rgba(255,255,255,.11);--tx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--tx-ink-bad:#f87171;--tx-ink-warn:#fbbf24;--tx-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--tx-accent:var(--ddt-accent);--tx-button:var(--ddt-accent);--tx-accent-2:var(--ddt-prompt);"
                    "--tx-soft:var(--ddt-hover);--tx-ring:var(--ddt-accent);--tx-line:var(--ddt-line);"
                    "--tx-line-2:var(--ddt-line);--tx-tint:transparent;--tx-tint-2:var(--ddt-hover);--tx-bg:var(--ddt-bg);"
                    "--tx-surface:transparent;--tx-raised:var(--ddt-hover);--tx-shadow:none;--tx-ok:var(--ddt-ok);"
                    "--tx-warn:var(--ddt-warn);--tx-bad:var(--ddt-bad);--tx-ink-ok:var(--ddt-ok);"
                    "--tx-ink-warn:var(--ddt-warn);--tx-ink-bad:var(--ddt-bad)")

_DYNAMODB_MAGENTA = "#c925d1"

_TERMINAL_CSS = _terminal_theme((".ddx-app",), "ddt") + """
.ddx-app.ddx-app.ddx-app.ddx-app{""" + _TERMINAL_TOKENS + """}
.ddx-app.ddx-app{font-family:var(--ddt-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.ddx-app.ddx-app .widget-html-content,.ddx-app.ddx-app .widget-label,.ddx-app.ddx-app input,.ddx-app.ddx-app select{font-family:var(--ddt-mono)}
.ddx-app.ddx-app button.jupyter-button{font-family:var(--ddt-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.ddx-app.ddx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--ddt-accent);color:var(--ddt-accent)}
.ddx-app.ddx-app button.jupyter-button.mod-primary,.ddx-app.ddx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--ddt-accent);color:var(--ddt-accent);font-weight:700;filter:none}
.ddx-app.ddx-app .widget-text input,.ddx-app.ddx-app .jupyter-widget-text input,.ddx-app.ddx-app .widget-dropdown>select,.ddx-app.ddx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.ddx-app.ddx-app .widget-text input:focus,.ddx-app.ddx-app .jupyter-widget-text input:focus,.ddx-app.ddx-app .widget-dropdown>select:focus,.ddx-app.ddx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.ddx-app.ddx-app .widget-toggle-buttons,.ddx-app.ddx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.ddx-app.ddx-app .widget-toggle-buttons .widget-toggle-button,.ddx-app.ddx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--ddt-line);border-radius:6px;opacity:1;color:var(--ddt-dim)}
.ddx-app.ddx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.ddx-app.ddx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--ddt-accent);color:var(--ddt-accent);font-weight:700;box-shadow:none}
.ddx-app .ddx-logo{width:30px;height:30px;border-radius:7px;color:""" + _DYNAMODB_MAGENTA + """;background:transparent;box-shadow:inset 0 0 0 1px var(--ddt-line)}
.ddx-app .ddx-name{font-size:14px;font-weight:700}
.ddx-app .ddx-name::before{content:"\\276F  ";color:var(--ddt-prompt)}
.ddx-app .ddx-name span{color:var(--ddt-comment);opacity:1;font-size:12px}
.ddx-app .ddx-sub{color:var(--ddt-comment);opacity:1;font-size:11.5px}
.ddx-app .ddx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.ddx-app .ddx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ddt-comment);opacity:1}
.ddx-app .ddx-stat .l::before{content:"# "}
.ddx-app .ddx-stat b{font-size:14px}
.ddx-app .ddx-stat.warn{border-color:var(--ddt-warn);background:transparent}
.ddx-app .ddx-stat.warn b{color:var(--ddt-warn)}
.ddx-app .ddx-stat.bad{border-color:var(--ddt-bad);background:transparent}
.ddx-app .ddx-stat.bad b{color:var(--ddt-bad)}
.ddx-app .ddx-stat.ok{border-color:var(--ddt-line)}
.ddx-app .ddx-stat.ok b{color:var(--ddt-ok)}
.ddx-app.ddx-app .ddx-trig>.ddx-trig-b{border-radius:6px;box-shadow:none}
.ddx-app.ddx-app .ddx-open .ddx-trig>.ddx-trig-b{box-shadow:none}
.ddx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ddt-comment);opacity:1}
.ddx-app .fxl::before{content:"# "}
.ddx-app .fxi,.ddx-app .opi{font-family:var(--ddt-mono);color:var(--ddt-comment);opacity:1}
.ddx-app.ddx-app .ddx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.ddx-app.ddx-app .ddx-opt>.ddx-opt-b,.ddx-app.ddx-app .ddx-row>.ddx-row-b{border-radius:5px}
.ddx-app.ddx-app .ddx-opt.ddx-on>.ddx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--ddt-accent)}
.ddx-app.ddx-app .ddx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--ddt-line)}
.ddx-app.ddx-app button.ddx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--ddt-dim)}
.ddx-app.ddx-app button.ddx-tab:hover:enabled{background:transparent;color:var(--ddt-accent);opacity:1}
.ddx-app.ddx-app button.ddx-tab.ddx-on,.ddx-app.ddx-app button.ddx-tab.ddx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--ddt-accent);color:var(--ddt-accent);font-weight:700}
.ddx-app.ddx-app button.ddx-small,.ddx-app.ddx-app button.ddx-chip,.ddx-app.ddx-app button.ddx-x{border-radius:6px}
.ddx-app.ddx-app button.ddx-chip.ddx-on,.ddx-app.ddx-app button.ddx-chip.ddx-on:hover:enabled{background:transparent;border-color:var(--ddt-accent);font-weight:700}
.ddx-app.ddx-app .ddx-left,.ddx-app.ddx-app .ddx-machines{border-radius:8px}
.ddx-app.ddx-app .ddx-machines{border:1px solid var(--ddt-line);padding:0 4px 4px 0;overflow:visible}
.ddx-app.ddx-app .ddx-lhead{border-bottom:1px solid var(--ddt-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.ddx-app.ddx-app button.ddx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ddt-comment);opacity:1}
.ddx-app.ddx-app button.ddx-col.ddx-c-name::before{content:"# "}
.ddx-app.ddx-app button.ddx-col:hover:enabled{background:transparent;color:var(--ddt-accent)}
.ddx-app.ddx-app button.ddx-col.ddx-on{color:var(--ddt-accent);font-weight:700}
.ddx-app.ddx-app .ddx-row.ddx-on>.ddx-row-b,.ddx-app.ddx-app .ddx-row.ddx-on>.ddx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--ddt-accent)}
.ddx-app .tag,.ddx-app .ei{border-radius:4px}
.ddx-app .tag{font-weight:400;letter-spacing:0;color:var(--ddt-comment)}
.ddx-app .tag.x{background:rgba(201,37,209,.12);color:""" + _DYNAMODB_MAGENTA + """}
.ddx-app .ef,.ddx-app .ff,.ddx-app .ez,.ddx-app .fz{color:var(--ddt-comment);opacity:1}
.ddx-app .ddx-pager .widget-html-content,.ddx-app .ddx-pager .jupyter-widget-html-content{color:var(--ddt-dim);opacity:1}
.ddx-app.ddx-app .ddx-pager{border-top:0}
.ddx-app .ddx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--ddt-comment);opacity:1}
.ddx-app .ddx-label::before{content:"# "}
.ddx-app .ddb .card{border-radius:6px}
.ddx-app .ddb .note,.ddx-app .ddb .fi{border-radius:3px 6px 6px 3px}
.ddx-app .ddb pre{border-radius:6px}
.ddx-app .ddx-status{color:var(--ddt-dim)}
.ddx-app .ddx-status .st{opacity:1}
.ddx-app .ddx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--ddt-ok)}
.ddx-app .ddx-status code{border-radius:4px;color:var(--ddt-s)}
.ddx-app .ddx-hint{border-radius:6px}
.ddx-app .spin{border-top-color:var(--ddt-accent)}"""

_BASE_CSS = """.ddx-app{--tx-accent:#2563eb;--tx-button:#2563eb;--tx-accent-2:#db2777;--tx-soft:rgba(37,99,235,.10);--tx-ring:rgba(37,99,235,.28);--tx-line:rgba(127,127,127,.22);--tx-line-2:rgba(127,127,127,.36);--tx-tint:rgba(127,127,127,.06);--tx-tint-2:rgba(127,127,127,.11);--tx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--tx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--tx-raised:var(--tx-surface);--tx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--tx-ok:#10b981;--tx-warn:#f59e0b;--tx-bad:#ef4444;--tx-ink-bad:#dc2626;--tx-ink-warn:#b45309;--tx-ink-ok:#047857}
body[data-jp-theme-light="false"] .ddx-app,body.vscode-dark .ddx-app,body.vscode-high-contrast .ddx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .ddx-app{""" + _EXPLORER_DARK + """}}
.ddx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--tx-line);border-radius:18px;padding:14px 16px 10px;background:var(--tx-bg);box-shadow:var(--tx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.ddx-app *{box-sizing:border-box}
.ddx-app .widget-html-content,.ddx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.ddx-app.ddx-app .ddx-flat>*{margin:0}
.ddx-app.ddx-app button.jupyter-button{color:inherit;background:var(--tx-tint);border:1px solid var(--tx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.ddx-app.ddx-app button.jupyter-button:hover:enabled{background:var(--tx-tint-2);border-color:var(--tx-line-2);box-shadow:none}
.ddx-app.ddx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.ddx-app.ddx-app button.jupyter-button:focus-visible{outline:2px solid var(--tx-ring);outline-offset:-1px}
.ddx-app.ddx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.ddx-app.ddx-app button.jupyter-button.mod-primary{background:var(--tx-button);border-color:transparent;color:#fff;font-weight:600}
.ddx-app.ddx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--tx-button);filter:brightness(1.08)}
.ddx-app.ddx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.ddx-app.ddx-app .widget-text input,.ddx-app.ddx-app .jupyter-widget-text input,.ddx-app.ddx-app .widget-dropdown>select,.ddx-app.ddx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--tx-line-2);border-radius:9px;background-color:var(--tx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.ddx-app.ddx-app .widget-dropdown>select,.ddx-app.ddx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.ddx-app.ddx-app .widget-text input:focus,.ddx-app.ddx-app .jupyter-widget-text input:focus,.ddx-app.ddx-app .widget-dropdown>select:focus,.ddx-app.ddx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--tx-accent);box-shadow:0 0 0 3px var(--tx-soft)}
.ddx-app.ddx-app .widget-text,.ddx-app.ddx-app .widget-dropdown,.ddx-app.ddx-app .jupyter-widget-text,.ddx-app.ddx-app .jupyter-widget-dropdown{margin:0;height:auto}
.ddx-app.ddx-app .widget-toggle-buttons,.ddx-app.ddx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--tx-tint-2);gap:2px;flex:0 0 auto;margin:0}
.ddx-app.ddx-app .widget-toggle-buttons .widget-toggle-button,.ddx-app.ddx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.ddx-app.ddx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.ddx-app.ddx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--tx-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.ddx-app.ddx-app .widget-checkbox input[type=checkbox],.ddx-app.ddx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--tx-accent)}
.ddx-app.ddx-app .ddx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--tx-line);overflow:visible}
.ddx-app.ddx-app .ddx-top{align-items:center;gap:12px;overflow:visible}
.ddx-app.ddx-app .ddx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.ddx-app .ddx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.ddx-app .ddx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--tx-accent),var(--tx-accent-2));box-shadow:0 2px 8px var(--tx-ring);flex:0 0 auto}
.ddx-app .ddx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app .ddx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.ddx-app .ddx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app .ddx-stats{display:flex;flex-wrap:wrap;gap:8px}
.ddx-app .ddx-stat{border:1px solid var(--tx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--tx-tint)}
.ddx-app .ddx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.ddx-app .ddx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.ddx-app .ddx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.ddx-app .ddx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.ddx-app .ddx-stat.ok{border-color:rgba(16,185,129,.55)}
.ddx-app .ddx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--tx-tint-2) 30%,var(--tx-line) 50%,var(--tx-tint-2) 70%);animation:ddx-glow 1.3s ease-in-out infinite}
.ddx-app.ddx-app .ddx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.ddx-app.ddx-app .ddx-field.ddx-open{z-index:41}
.ddx-app.ddx-app .ddx-trig{position:relative;min-height:52px;overflow:visible}
.ddx-app.ddx-app .ddx-trig>.ddx-trig-b,.ddx-app.ddx-app .ddx-opt>.ddx-opt-b,.ddx-app.ddx-app .ddx-row>.ddx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.ddx-app.ddx-app .ddx-trig>.ddx-trig-b{border-color:var(--tx-line-2);border-radius:12px;background:var(--tx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.ddx-app.ddx-app .ddx-trig>.ddx-trig-b:hover:enabled{border-color:var(--tx-accent);background:var(--tx-surface)}
.ddx-app.ddx-app .ddx-open .ddx-trig>.ddx-trig-b{border-color:var(--tx-accent);box-shadow:0 0 0 3px var(--tx-soft)}
.ddx-app.ddx-app .ddx-trig>.ddx-trig-b:active:enabled,.ddx-app.ddx-app .ddx-opt>.ddx-opt-b:active:enabled,.ddx-app.ddx-app .ddx-row>.ddx-row-b:active:enabled{transform:none}
.ddx-app.ddx-app .ddx-trig>.ddx-face,.ddx-app.ddx-app .ddx-opt>.ddx-opt-t,.ddx-app.ddx-app .ddx-row>.ddx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.ddx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.ddx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.ddx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.ddx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ddx-app .fxi,.ddx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.ddx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.ddx-app .ddx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--tx-accent)}
.ddx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.ddx-app .dot.ok{background:var(--tx-ok)}
.ddx-app .dot.warn{background:var(--tx-warn)}
.ddx-app .dot.bad{background:var(--tx-bad)}
.ddx-app.ddx-app .ddx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--tx-line-2);border-radius:14px;background:var(--tx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.ddx-app.ddx-app .ddx-pop>*{margin:0}
.ddx-app.ddx-app .ddx-pop .ddx-x{margin-left:6px}
.ddx-app.ddx-app .ddx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.ddx-app.ddx-app .ddx-opts>*{flex:0 0 auto}
.ddx-app.ddx-app .ddx-opt{position:relative;margin:0 0 2px;overflow:visible}
.ddx-app.ddx-app .ddx-opt>.ddx-opt-b{border-radius:10px}
.ddx-app.ddx-app .ddx-opt>.ddx-opt-b:hover:enabled{background:var(--tx-tint-2)}
.ddx-app.ddx-app .ddx-opt.ddx-on>.ddx-opt-b{background:var(--tx-soft);border-color:var(--tx-ring)}
.ddx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.ddx-app .op .dot{margin-top:6px}
.ddx-app .opb{flex:1 1 auto;min-width:0}
.ddx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.ddx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.ddx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--tx-line);line-height:1.4}
.ddx-app .opf.warn{opacity:1;color:var(--tx-ink-warn)}
.ddx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.ddx-app.ddx-app .ddx-backdrop,.ddx-app.ddx-app .ddx-backdrop:hover:enabled,.ddx-app.ddx-app .ddx-backdrop:active:enabled,.ddx-app.ddx-app .ddx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.ddx-app.ddx-app .ddx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--tx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.ddx-app.ddx-app button.ddx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.ddx-app.ddx-app button.ddx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--tx-icon) center/contain no-repeat;mask:var(--tx-icon) center/contain no-repeat}
.ddx-app.ddx-app button.ddx-tab:hover:enabled{background:var(--tx-tint);opacity:.95}
.ddx-app.ddx-app button.ddx-tab.ddx-on,.ddx-app.ddx-app button.ddx-tab.ddx-on:hover:enabled{background:var(--tx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.ddx-app.ddx-app button.ddx-tab.ddx-on::before{background:var(--tx-accent)}
.ddx-app.ddx-app button.ddx-tab.ddx-alert::after,.ddx-app.ddx-app button.ddx-tab.ddx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--tx-warn);flex:0 0 auto}
.ddx-app.ddx-app button.ddx-tab.ddx-alarm::after{background:var(--tx-bad)}
@container (max-width:520px){.ddx-app.ddx-app button.ddx-tab::before{display:none}}
.ddx-app.ddx-app .ddx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .ddx-app.ddx-app .ddx-page{height:620px}
.ddx-app.ddx-app .ddx-page>*{flex:0 0 auto}
.ddx-app.ddx-app .ddx-page.ddx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.ddx-app.ddx-app .ddx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--tx-line);border-radius:14px;padding:10px 10px 6px;background:var(--tx-surface);overflow:hidden}
.ddx-app.ddx-app .ddx-left>*{margin:0;flex:0 0 auto}
.ddx-app.ddx-app .ddx-left,.ddx-app.ddx-app .ddx-right,.ddx-app.ddx-app .ddx-rows{min-height:0}
@media (max-width:900px){.ddx-app.ddx-app .ddx-page.ddx-split{flex-wrap:wrap;overflow:hidden auto}.ddx-app.ddx-app .ddx-left{flex:1 1 100%;max-width:100%;height:440px}.ddx-app.ddx-app .ddx-right{flex:1 1 100%;overflow:visible}}
.ddx-app.ddx-app .ddx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.ddx-app.ddx-app .ddx-right>*{flex:0 0 auto;margin:0}
.ddx-app.ddx-app .ddx-find{position:relative;align-items:center}
.ddx-app.ddx-app .ddx-find input,.ddx-app.ddx-app .ddx-find input:focus{padding-left:32px;background:var(--tx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.ddx-app.ddx-app .ddx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.ddx-app.ddx-app .ddx-chips>*{margin:0}
.ddx-app.ddx-app button.ddx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--tx-line);display:inline-flex;align-items:center;gap:6px}
.ddx-app.ddx-app button.ddx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.ddx-app.ddx-app button.ddx-chip.ddx-t-all::before{display:none}
.ddx-app.ddx-app button.ddx-chip.ddx-t-bad::before{background:var(--tx-bad)}
.ddx-app.ddx-app button.ddx-chip.ddx-t-warn::before{background:var(--tx-warn)}
.ddx-app.ddx-app button.ddx-chip.ddx-t-ok::before{background:var(--tx-ok)}
.ddx-app.ddx-app button.ddx-chip.ddx-on,.ddx-app.ddx-app button.ddx-chip.ddx-on:hover:enabled{background:var(--tx-soft);border-color:var(--tx-ring);color:var(--tx-accent);font-weight:650}
.ddx-app.ddx-app .ddx-filters{gap:6px;margin:8px 0 0;align-items:center}
.ddx-app.ddx-app .ddx-filters>*{margin:0}
.ddx-app.ddx-app .ddx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.ddx-app.ddx-app .ddx-rows>*{flex:0 0 auto}
.ddx-app.ddx-app .ddx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.ddx-app.ddx-app .ddx-row>.ddx-row-b{border-radius:10px}
.ddx-app.ddx-app .ddx-row>.ddx-row-b:hover:enabled{background:var(--tx-tint-2)}
.ddx-app.ddx-app .ddx-row.ddx-on>.ddx-row-b,.ddx-app.ddx-app .ddx-row.ddx-on>.ddx-row-b:hover:enabled{background:var(--tx-soft);border-color:var(--tx-ring)}
.ddx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.ddx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.ddx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.ddx-app .sl{font-weight:600}
.ddx-app .sl.bad{color:var(--tx-ink-bad)}
.ddx-app .sl.warn{color:var(--tx-ink-warn)}
.ddx-app .sl.ok{color:var(--tx-ink-ok)}
.ddx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.ddx-app .ddx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.ddx-app.ddx-app .ddx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--tx-line);margin-top:4px}
.ddx-app.ddx-app .ddx-pager>*{margin:0}
.ddx-app.ddx-app button.ddx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.ddx-app .ddx-pager .widget-html-content,.ddx-app .ddx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app.ddx-app .ddx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--tx-bg);border-bottom:1px solid var(--tx-line);flex-wrap:wrap}
.ddx-app.ddx-app .ddx-bar>*{margin:0}
.ddx-app.ddx-app button.ddx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.ddx-app.ddx-app button.ddx-ghost{background:transparent;border-color:transparent}
.ddx-app.ddx-app button.ddx-ghost:hover:enabled{background:var(--tx-tint-2)}
.ddx-app.ddx-app button.ddx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.ddx-app.ddx-app button.ddx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--tx-ink-bad)}
.ddx-app .ddx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.ddx-app .ddx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.ddx-app .ddx-status .st{opacity:.72}
.ddx-app .ddx-status .st.warn{opacity:1;color:var(--tx-ink-warn)}
.ddx-app .ddx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.ddx-app .ddx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--tx-ok)}
.ddx-app .ddx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--tx-tint-2);user-select:all;-webkit-user-select:all}
.ddx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--tx-accent);border-radius:50%;animation:ddx-spin .8s linear infinite}
@keyframes ddx-spin{to{transform:rotate(360deg)}}
.ddx-app .skw{padding:6px 2px}
.ddx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.ddx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--tx-tint) 30%,var(--tx-tint-2) 50%,var(--tx-tint) 70%);animation:ddx-glow 1.3s ease-in-out infinite}
.ddx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.ddx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.ddx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes ddx-glow{from{background-position:100% 0}to{background-position:0 0}}
.ddx-app .ddx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--tx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.ddx-app .ddx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.ddx-app *,.ddx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.ddx-app.ddx-app .ddx-row{height:52px}
.ddx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.ddx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--tx-tint-2)}
.ddx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--tx-ink-ok)}
.ddx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--tx-ink-bad)}
.ddx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--tx-ink-warn)}
.ddx-app .ei.run{background:rgba(59,130,246,.15);color:var(--tx-accent)}
.ddx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.ddx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.ddx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.ddx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--tx-tint-2);margin-left:6px;vertical-align:1px}
.ddx-app .tag.x{background:rgba(219,39,119,.12);color:var(--tx-accent-2)}
.ddx-app.ddx-app .ddx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--tx-line)}
.ddx-app.ddx-app .ddx-lhead>*{margin:0;min-width:0}
.ddx-app.ddx-app button.ddx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ddx-app.ddx-app button.ddx-col.ddx-c-name{text-align:left;justify-content:flex-start}
.ddx-app.ddx-app button.ddx-col:hover:enabled{background:transparent;opacity:1;color:var(--tx-accent)}
.ddx-app.ddx-app button.ddx-col.ddx-on{opacity:1;color:var(--tx-accent)}
.ddx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.ddx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.ddx-app .mr .num.bad{color:var(--tx-ink-bad);font-weight:650}
.ddx-app .mr .num.warn{color:var(--tx-ink-warn);font-weight:650}
.ddx-app.ddx-app .ddx-machines{overflow:hidden auto}
.ddx-app.ddx-app .ddx-machines>*{flex:0 0 auto}
.ddx-app .ddb h3 .badge{display:none}
.ddx-app .ddb h3{font-size:17px;margin:6px 0 2px}
.ddx-app .ddb .card{border-radius:12px;background:var(--tx-tint);border-color:var(--tx-line)}
.ddx-app .ddb .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.ddx-app .ddb .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.ddx-app .ddb .card.ok{border-color:rgba(16,185,129,.55)}
.ddx-app .ddb .note,.ddx-app .ddb .fi{border-radius:4px 10px 10px 4px}
.ddx-app .ddb pre{border-radius:10px}
"""

_OWN_CSS = """
.ddx-app .fr .fn .tag{margin-left:6px}
.ddx-app .fz b{display:block;font-weight:600;opacity:1}
.ddx-app .fz.warn b{color:var(--tx-ink-warn)}
.ddx-app .fz.bad b{color:var(--tx-ink-bad)}
.ddx-app .fr .fn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ddx-app.ddx-app .ddx-search{gap:8px;align-items:center;flex-wrap:wrap;padding:0 0 10px;margin:0 0 6px;border-bottom:1px solid var(--tx-line)}
.ddx-app.ddx-app .ddx-search>*{margin:0}
.ddx-app.ddx-app .ddx-search .ddx-find{flex:1 1 260px}
"""

_EXPLORER_CSS = "<style>\n" + _BASE_CSS + _OWN_CSS + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "tables": "the Overview tab",
    "table_info": "the Tables tab",
    "sample": "the Items tab",
    "scan": "the Items tab",
    "query": "the Items tab",
    "get": "the Items tab",
    "more": "More on the Items tab",
    "schema": "the Schema tab",
    "value_counts": "the Schema tab",
}

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he \w+ tab)")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (table_info('orders') -> "the Tables tab", sample('orders') -> "the Items tab"), capitalised where it starts a
    sentence. A call the window has no tab for (largest(...), count(...), sql(...)) stays as it is, to run in a cell.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        if call.startswith(".") or name not in _IN_WINDOW:
            return call
        return _IN_WINDOW[name]

    swapped = _CALL_RE.sub(swap, str(text or ""))
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), swapped)


def _side(size: str, under: str, tone: str = "") -> str:
    """
    The right of a line: a number in bold over a few words.
    """
    return f'<div class="fz {tone}"><b>{size}</b>{under}</div>'


def _line(tone: str, name: str, note: str, side: str) -> str:
    """
    One line of a list: a dot for how it is, its name over a note, and its _side on the right.
    """
    return (f'<div class="fr"><span class="dot {tone}"></span><div class="fm"><div class="fn">{name}</div>'
            f'<div class="ff">{note}</div></div>{side}</div>')


def _key_words(report: TableReport) -> str:
    """
    A table's key as a line says it: 'pk + sk', or 'pk' without a sort key.
    """
    info = report.info
    return " + ".join(key for key in (info.partition_key, info.sort_key) if key) or "keys unknown"


def _table_note(report: TableReport) -> str:
    info = report.info
    if "describe" in info.errors:
        return f"couldn't be described ({info.errors['describe']})"
    indexes = len(info.indexes)
    parts = [f"key {_key_words(report)}", f"{_count(info.item_count)} items",
             f"{indexes} index{'' if indexes == 1 else 'es'}" if indexes else "",
             "stream on" if info.stream else "", f"{len(info.replicas)} replicas" if info.replicas else ""]
    return " · ".join(filter(None, parts))


def _table_face(report: TableReport, *, warnings: int, cost: float, words: list[str]) -> str:
    """
    A table's line in the Tables tab: a dot for what's wrong with it, its name and billing, its key, items and
    indexes, and what it costs a month and how big it is.
    """
    info = report.info
    broken = "describe" in info.errors or (info.status and info.status != "ACTIVE")
    tone = "bad" if broken else "warn" if warnings else "ok"
    billing = "on-demand" if info.on_demand else "provisioned"
    tag = f'<span class="tag">{_esc(billing)}</span>'
    flag = f" · {warnings} warning{'' if warnings == 1 else 's'}" if warnings else ""
    return _line(tone, _marked(info.name, words) + tag, _marked(_clip(_table_note(report) + flag, _NOTE_CHARS), words),
                 _side(_esc(human_money(cost)), _esc(f"{human_size(info.size_bytes)} · a month")))


def _item_face(item: dict[str, Any], keys: list[str], *, named_by: list[str], words: list[str]) -> str:
    """
    An item's line in the Items tab: its values of the named_by keys as its name, its first few attributes that
    aren't keys under them, and its size and how many attributes it has (amber near DynamoDB's 400 KB limit).
    """
    size = item_size(item)
    name = " · ".join(format_value(item.get(key), _VALUE_CHARS * 2) for key in named_by if key in item) or "(no key)"
    others = [f"{attribute}={format_value(value, _VALUE_CHARS)}" for attribute, value in item.items()
              if attribute not in keys][:_NOTE_ATTRIBUTES]
    tone = "warn" if size > _LARGE_ITEM else "ok"
    return _line(tone, _marked(name, words), _marked(_clip("  ".join(others), _NOTE_CHARS), words),
                 _side(_esc(human_size(size)), _esc(f"{len(item)} attributes"), "warn" if size > _LARGE_ITEM else ""))


def _types_words(attribute: AttributeProfile) -> str:
    return " or ".join(TYPE_NAMES.get(kind, kind) for kind, _ in attribute.types.most_common())


def _attribute_face(attribute: AttributeProfile, *, items: int, role: str, words: list[str]) -> str:
    """
    An attribute's line in the Schema tab, indented under its map: a dot (amber when it holds more than one type),
    its name and role, its types and examples, and how many items have it.
    """
    fill = _share(attribute.count, items)
    tone = "warn" if len(attribute.types) > 1 else "ok"
    indent = "&nbsp;&nbsp;&nbsp;" * attribute.depth
    tag = f'<span class="tag">{_esc(role)}</span>' if role else ""
    examples = ", ".join(format_value(value, _VALUE_CHARS) for value in attribute.examples)
    note = " · ".join(filter(None, [_types_words(attribute), examples]))
    distinct = f"{attribute.distinct:,}{'+' if attribute.distinct_capped else ''} distinct"
    return _line(tone, indent + _marked(attribute.name, words) + tag, _marked(_clip(note, _NOTE_CHARS), words),
                 _side(_esc(f"{fill:.0%}"), _esc(distinct), "" if fill >= 1 else "warn" if fill < 0.5 else ""))


def _stats_html(cards: list[tuple[str, str, str]]) -> str:
    """
    The cards beside the title: (label, value, tone) each.
    """
    html: list[Any] = [f'<div class="ddx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b></div>'
                       for label, value, tone in cards]
    return f'<div class="ddx-stats">{"".join(html)}</div>'
