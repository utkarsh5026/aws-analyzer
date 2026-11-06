"""
What the OpenSearch explorer window is drawn with: its tabs, stylesheet and colours (the API Gateway explorer's
console look, on the windows' terminal theme), the sentences from reports reworded for the window, and the faces of
its lines (a domain or collection, an index, a document).
"""

from __future__ import annotations

import re
from typing import Any

from ..._kit.fmt import _count, human_money, human_size
from ..._kit.render import _CALL_RE
from ..._kit.text import _clip, _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..helpers import values_at
from ..models import Collection, Domain, IndexInfo
from ..analysis.parsing import source_of
from ..analysis.queries import describe_vector_field
from ..view import _COLLECTION_KINDS, _NETWORK, _hit_text, _node_label, _state_tone


_EXPLORER_TABS = (
    ("stores", "Domains", "Every domain and Serverless collection in the region: click one to open it",
     "<ellipse cx='12' cy='6' rx='7.5' ry='2.8'/><path d='M4.5 6v12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6'/>"
     "<path d='M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8'/>"),
    ("overview", "Overview", "The domain's or collection's indexes, their memory and what needs attention",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("indexes", "Indexes", "Its indexes: click one to see its vector fields, the memory they need and the query to copy",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("documents", "Documents", "A sample of the index's documents and how healthy their vectors are: click one to "
     "see it", "<path d='M7 3.5h7l4.5 4.5v12.5H7z'/><path d='M14 3.5V8h4.5M10 12.5h6M10 16h6'/>"),
    ("search", "Search", "The documents nearest a question, or nearest one of its documents",
     "<circle cx='11' cy='11' r='6.5'/><path d='m20 20-4-4'/>"),
)

_STORE_TABS = ("overview", "indexes", "documents", "search")

_INDEX_TABS = ("documents", "search")

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.5-4.5"/>'
         '<path d="M7.5 12.5c1-2.5 2.5-3.5 6-3.5"/></svg>')

_KIND_TAGS = {"VECTORSEARCH": "VECTOR", "SEARCH": "SEARCH", "TIMESERIES": "TIME SERIES"}


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .osx-i-<key> sets --ox-icon, which a tab's ::before draws.
    """
    return "\n".join(f".osx-app .osx-i-{key}{{--ox-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--ox-accent:#60a5fa;--ox-accent-2:#f472b6;--ox-soft:rgba(96,165,250,.15);--ox-ring:rgba(96,165,250,"
                  ".38);--ox-raised:rgba(255,255,255,.11);--ox-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--ox-ink-bad:#f87171;--ox-ink-warn:#fbbf24;--ox-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--ox-accent:var(--ost-accent);--ox-button:var(--ost-accent);--ox-accent-2:var(--ost-prompt);"
                    "--ox-soft:var(--ost-hover);--ox-ring:var(--ost-accent);--ox-line:var(--ost-line);"
                    "--ox-line-2:var(--ost-line);--ox-tint:transparent;--ox-tint-2:var(--ost-hover);--ox-bg:var(--ost-bg);"
                    "--ox-surface:transparent;--ox-raised:var(--ost-hover);--ox-shadow:none;--ox-ok:var(--ost-ok);"
                    "--ox-warn:var(--ost-warn);--ox-bad:var(--ost-bad);--ox-ink-ok:var(--ost-ok);"
                    "--ox-ink-warn:var(--ost-warn);--ox-ink-bad:var(--ost-bad)")

_OPENSEARCH_PURPLE = "#8c4fff"

_TERMINAL_CSS = _terminal_theme((".osx-app",), "ost") + """
.osx-app.osx-app.osx-app.osx-app{""" + _TERMINAL_TOKENS + """}
.osx-app.osx-app{font-family:var(--ost-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.osx-app.osx-app .widget-html-content,.osx-app.osx-app .widget-label,.osx-app.osx-app input,.osx-app.osx-app select{font-family:var(--ost-mono)}
.osx-app.osx-app button.jupyter-button{font-family:var(--ost-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.osx-app.osx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--ost-accent);color:var(--ost-accent)}
.osx-app.osx-app button.jupyter-button.mod-primary,.osx-app.osx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--ost-accent);color:var(--ost-accent);font-weight:700;filter:none}
.osx-app.osx-app .widget-text input,.osx-app.osx-app .jupyter-widget-text input,.osx-app.osx-app .widget-dropdown>select,.osx-app.osx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.osx-app.osx-app .widget-text input:focus,.osx-app.osx-app .jupyter-widget-text input:focus,.osx-app.osx-app .widget-dropdown>select:focus,.osx-app.osx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.osx-app.osx-app .widget-toggle-buttons,.osx-app.osx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.osx-app.osx-app .widget-toggle-buttons .widget-toggle-button,.osx-app.osx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--ost-line);border-radius:6px;opacity:1;color:var(--ost-dim)}
.osx-app.osx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.osx-app.osx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--ost-accent);color:var(--ost-accent);font-weight:700;box-shadow:none}
.osx-app .osx-logo{width:30px;height:30px;border-radius:7px;color:""" + _OPENSEARCH_PURPLE + """;background:transparent;box-shadow:inset 0 0 0 1px var(--ost-line)}
.osx-app .osx-name{font-size:14px;font-weight:700}
.osx-app .osx-name::before{content:"\\276F  ";color:var(--ost-prompt)}
.osx-app .osx-name span{color:var(--ost-comment);opacity:1;font-size:12px}
.osx-app .osx-sub{color:var(--ost-comment);opacity:1;font-size:11.5px}
.osx-app .osx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.osx-app .osx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ost-comment);opacity:1}
.osx-app .osx-stat .l::before{content:"# "}
.osx-app .osx-stat b{font-size:14px}
.osx-app .osx-stat.warn{border-color:var(--ost-warn);background:transparent}
.osx-app .osx-stat.warn b{color:var(--ost-warn)}
.osx-app .osx-stat.bad{border-color:var(--ost-bad);background:transparent}
.osx-app .osx-stat.bad b{color:var(--ost-bad)}
.osx-app .osx-stat.ok{border-color:var(--ost-line)}
.osx-app .osx-stat.ok b{color:var(--ost-ok)}
.osx-app.osx-app .osx-trig>.osx-trig-b{border-radius:6px;box-shadow:none}
.osx-app.osx-app .osx-open .osx-trig>.osx-trig-b{box-shadow:none}
.osx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ost-comment);opacity:1}
.osx-app .fxl::before{content:"# "}
.osx-app .fxi,.osx-app .opi{font-family:var(--ost-mono);color:var(--ost-comment);opacity:1}
.osx-app.osx-app .osx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.osx-app.osx-app .osx-opt>.osx-opt-b,.osx-app.osx-app .osx-row>.osx-row-b{border-radius:5px}
.osx-app.osx-app .osx-opt.osx-on>.osx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--ost-accent)}
.osx-app.osx-app .osx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--ost-line)}
.osx-app.osx-app button.osx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--ost-dim)}
.osx-app.osx-app button.osx-tab:hover:enabled{background:transparent;color:var(--ost-accent);opacity:1}
.osx-app.osx-app button.osx-tab.osx-on,.osx-app.osx-app button.osx-tab.osx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--ost-accent);color:var(--ost-accent);font-weight:700}
.osx-app.osx-app button.osx-small,.osx-app.osx-app button.osx-chip,.osx-app.osx-app button.osx-x{border-radius:6px}
.osx-app.osx-app button.osx-chip.osx-on,.osx-app.osx-app button.osx-chip.osx-on:hover:enabled{background:transparent;border-color:var(--ost-accent);font-weight:700}
.osx-app.osx-app .osx-left,.osx-app.osx-app .osx-machines{border-radius:8px}
.osx-app.osx-app .osx-machines{border:1px solid var(--ost-line);padding:0 4px 4px 0;overflow:visible}
.osx-app.osx-app .osx-lhead{border-bottom:1px solid var(--ost-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.osx-app.osx-app button.osx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--ost-comment);opacity:1}
.osx-app.osx-app button.osx-col.osx-c-name::before{content:"# "}
.osx-app.osx-app button.osx-col:hover:enabled{background:transparent;color:var(--ost-accent)}
.osx-app.osx-app button.osx-col.osx-on{color:var(--ost-accent);font-weight:700}
.osx-app.osx-app .osx-row.osx-on>.osx-row-b,.osx-app.osx-app .osx-row.osx-on>.osx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--ost-accent)}
.osx-app .tag,.osx-app .ei{border-radius:4px}
.osx-app .tag{font-weight:400;letter-spacing:0;color:var(--ost-comment)}
.osx-app .tag.x{background:rgba(140,79,255,.12);color:""" + _OPENSEARCH_PURPLE + """}
.osx-app .ef,.osx-app .ff,.osx-app .ez,.osx-app .fz{color:var(--ost-comment);opacity:1}
.osx-app .osx-pager .widget-html-content,.osx-app .osx-pager .jupyter-widget-html-content{color:var(--ost-dim);opacity:1}
.osx-app.osx-app .osx-pager{border-top:0}
.osx-app .osx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--ost-comment);opacity:1}
.osx-app .osx-label::before{content:"# "}
.osx-app .osv .card{border-radius:6px}
.osx-app .osv .note,.osx-app .osv .fi{border-radius:3px 6px 6px 3px}
.osx-app .osv pre{border-radius:6px}
.osx-app .osx-status{color:var(--ost-dim)}
.osx-app .osx-status .st{opacity:1}
.osx-app .osx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--ost-ok)}
.osx-app .osx-status code{border-radius:4px;color:var(--ost-s)}
.osx-app .osx-hint{border-radius:6px}
.osx-app .spin{border-top-color:var(--ost-accent)}"""

_BASE_CSS = """.osx-app{--ox-accent:#2563eb;--ox-button:#2563eb;--ox-accent-2:#db2777;--ox-soft:rgba(37,99,235,.10);--ox-ring:rgba(37,99,235,.28);--ox-line:rgba(127,127,127,.22);--ox-line-2:rgba(127,127,127,.36);--ox-tint:rgba(127,127,127,.06);--ox-tint-2:rgba(127,127,127,.11);--ox-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--ox-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--ox-raised:var(--ox-surface);--ox-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--ox-ok:#10b981;--ox-warn:#f59e0b;--ox-bad:#ef4444;--ox-ink-bad:#dc2626;--ox-ink-warn:#b45309;--ox-ink-ok:#047857}
body[data-jp-theme-light="false"] .osx-app,body.vscode-dark .osx-app,body.vscode-high-contrast .osx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .osx-app{""" + _EXPLORER_DARK + """}}
.osx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--ox-line);border-radius:18px;padding:14px 16px 10px;background:var(--ox-bg);box-shadow:var(--ox-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.osx-app *{box-sizing:border-box}
.osx-app .widget-html-content,.osx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.osx-app.osx-app .osx-flat>*{margin:0}
.osx-app.osx-app button.jupyter-button{color:inherit;background:var(--ox-tint);border:1px solid var(--ox-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.osx-app.osx-app button.jupyter-button:hover:enabled{background:var(--ox-tint-2);border-color:var(--ox-line-2);box-shadow:none}
.osx-app.osx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.osx-app.osx-app button.jupyter-button:focus-visible{outline:2px solid var(--ox-ring);outline-offset:-1px}
.osx-app.osx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.osx-app.osx-app button.jupyter-button.mod-primary{background:var(--ox-button);border-color:transparent;color:#fff;font-weight:600}
.osx-app.osx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--ox-button);filter:brightness(1.08)}
.osx-app.osx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.osx-app.osx-app .widget-text input,.osx-app.osx-app .jupyter-widget-text input,.osx-app.osx-app .widget-dropdown>select,.osx-app.osx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--ox-line-2);border-radius:9px;background-color:var(--ox-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.osx-app.osx-app .widget-dropdown>select,.osx-app.osx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.osx-app.osx-app .widget-text input:focus,.osx-app.osx-app .jupyter-widget-text input:focus,.osx-app.osx-app .widget-dropdown>select:focus,.osx-app.osx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--ox-accent);box-shadow:0 0 0 3px var(--ox-soft)}
.osx-app.osx-app .widget-text,.osx-app.osx-app .widget-dropdown,.osx-app.osx-app .jupyter-widget-text,.osx-app.osx-app .jupyter-widget-dropdown{margin:0;height:auto}
.osx-app.osx-app .widget-toggle-buttons,.osx-app.osx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--ox-tint-2);gap:2px;flex:0 0 auto;margin:0}
.osx-app.osx-app .widget-toggle-buttons .widget-toggle-button,.osx-app.osx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.osx-app.osx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.osx-app.osx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--ox-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.osx-app.osx-app .widget-checkbox input[type=checkbox],.osx-app.osx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--ox-accent)}
.osx-app.osx-app .osx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--ox-line);overflow:visible}
.osx-app.osx-app .osx-top{align-items:center;gap:12px;overflow:visible}
.osx-app.osx-app .osx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.osx-app .osx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.osx-app .osx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--ox-accent),var(--ox-accent-2));box-shadow:0 2px 8px var(--ox-ring);flex:0 0 auto}
.osx-app .osx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app .osx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.osx-app .osx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app .osx-stats{display:flex;flex-wrap:wrap;gap:8px}
.osx-app .osx-stat{border:1px solid var(--ox-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--ox-tint)}
.osx-app .osx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.osx-app .osx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.osx-app .osx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.osx-app .osx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.osx-app .osx-stat.ok{border-color:rgba(16,185,129,.55)}
.osx-app .osx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--ox-tint-2) 30%,var(--ox-line) 50%,var(--ox-tint-2) 70%);animation:osx-glow 1.3s ease-in-out infinite}
.osx-app.osx-app .osx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.osx-app.osx-app .osx-field.osx-open{z-index:41}
.osx-app.osx-app .osx-trig{position:relative;min-height:52px;overflow:visible}
.osx-app.osx-app .osx-trig>.osx-trig-b,.osx-app.osx-app .osx-opt>.osx-opt-b,.osx-app.osx-app .osx-row>.osx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.osx-app.osx-app .osx-trig>.osx-trig-b{border-color:var(--ox-line-2);border-radius:12px;background:var(--ox-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.osx-app.osx-app .osx-trig>.osx-trig-b:hover:enabled{border-color:var(--ox-accent);background:var(--ox-surface)}
.osx-app.osx-app .osx-open .osx-trig>.osx-trig-b{border-color:var(--ox-accent);box-shadow:0 0 0 3px var(--ox-soft)}
.osx-app.osx-app .osx-trig>.osx-trig-b:active:enabled,.osx-app.osx-app .osx-opt>.osx-opt-b:active:enabled,.osx-app.osx-app .osx-row>.osx-row-b:active:enabled{transform:none}
.osx-app.osx-app .osx-trig>.osx-face,.osx-app.osx-app .osx-opt>.osx-opt-t,.osx-app.osx-app .osx-row>.osx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.osx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.osx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.osx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.osx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.osx-app .fxi,.osx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.osx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.osx-app .osx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--ox-accent)}
.osx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.osx-app .dot.ok{background:var(--ox-ok)}
.osx-app .dot.warn{background:var(--ox-warn)}
.osx-app .dot.bad{background:var(--ox-bad)}
.osx-app.osx-app .osx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--ox-line-2);border-radius:14px;background:var(--ox-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.osx-app.osx-app .osx-pop>*{margin:0}
.osx-app.osx-app .osx-pop .osx-x{margin-left:6px}
.osx-app.osx-app .osx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.osx-app.osx-app .osx-opts>*{flex:0 0 auto}
.osx-app.osx-app .osx-opt{position:relative;margin:0 0 2px;overflow:visible}
.osx-app.osx-app .osx-opt>.osx-opt-b{border-radius:10px}
.osx-app.osx-app .osx-opt>.osx-opt-b:hover:enabled{background:var(--ox-tint-2)}
.osx-app.osx-app .osx-opt.osx-on>.osx-opt-b{background:var(--ox-soft);border-color:var(--ox-ring)}
.osx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.osx-app .op .dot{margin-top:6px}
.osx-app .opb{flex:1 1 auto;min-width:0}
.osx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.osx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.osx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--ox-line);line-height:1.4}
.osx-app .opf.warn{opacity:1;color:var(--ox-ink-warn)}
.osx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.osx-app.osx-app .osx-backdrop,.osx-app.osx-app .osx-backdrop:hover:enabled,.osx-app.osx-app .osx-backdrop:active:enabled,.osx-app.osx-app .osx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.osx-app.osx-app .osx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--ox-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.osx-app.osx-app button.osx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.osx-app.osx-app button.osx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--ox-icon) center/contain no-repeat;mask:var(--ox-icon) center/contain no-repeat}
.osx-app.osx-app button.osx-tab:hover:enabled{background:var(--ox-tint);opacity:.95}
.osx-app.osx-app button.osx-tab.osx-on,.osx-app.osx-app button.osx-tab.osx-on:hover:enabled{background:var(--ox-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.osx-app.osx-app button.osx-tab.osx-on::before{background:var(--ox-accent)}
.osx-app.osx-app button.osx-tab.osx-alert::after,.osx-app.osx-app button.osx-tab.osx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--ox-warn);flex:0 0 auto}
.osx-app.osx-app button.osx-tab.osx-alarm::after{background:var(--ox-bad)}
@container (max-width:520px){.osx-app.osx-app button.osx-tab::before{display:none}}
.osx-app.osx-app .osx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .osx-app.osx-app .osx-page{height:620px}
.osx-app.osx-app .osx-page>*{flex:0 0 auto}
.osx-app.osx-app .osx-page.osx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.osx-app.osx-app .osx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--ox-line);border-radius:14px;padding:10px 10px 6px;background:var(--ox-surface);overflow:hidden}
.osx-app.osx-app .osx-left>*{margin:0;flex:0 0 auto}
.osx-app.osx-app .osx-left,.osx-app.osx-app .osx-right,.osx-app.osx-app .osx-rows{min-height:0}
@media (max-width:900px){.osx-app.osx-app .osx-page.osx-split{flex-wrap:wrap;overflow:hidden auto}.osx-app.osx-app .osx-left{flex:1 1 100%;max-width:100%;height:440px}.osx-app.osx-app .osx-right{flex:1 1 100%;overflow:visible}}
.osx-app.osx-app .osx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.osx-app.osx-app .osx-right>*{flex:0 0 auto;margin:0}
.osx-app.osx-app .osx-find{position:relative;align-items:center}
.osx-app.osx-app .osx-find input,.osx-app.osx-app .osx-find input:focus{padding-left:32px;background:var(--ox-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.osx-app.osx-app .osx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.osx-app.osx-app .osx-chips>*{margin:0}
.osx-app.osx-app button.osx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--ox-line);display:inline-flex;align-items:center;gap:6px}
.osx-app.osx-app button.osx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.osx-app.osx-app button.osx-chip.osx-t-all::before{display:none}
.osx-app.osx-app button.osx-chip.osx-t-bad::before{background:var(--ox-bad)}
.osx-app.osx-app button.osx-chip.osx-t-warn::before{background:var(--ox-warn)}
.osx-app.osx-app button.osx-chip.osx-t-ok::before{background:var(--ox-ok)}
.osx-app.osx-app button.osx-chip.osx-on,.osx-app.osx-app button.osx-chip.osx-on:hover:enabled{background:var(--ox-soft);border-color:var(--ox-ring);color:var(--ox-accent);font-weight:650}
.osx-app.osx-app .osx-filters{gap:6px;margin:8px 0 0;align-items:center}
.osx-app.osx-app .osx-filters>*{margin:0}
.osx-app.osx-app .osx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.osx-app.osx-app .osx-rows>*{flex:0 0 auto}
.osx-app.osx-app .osx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.osx-app.osx-app .osx-row>.osx-row-b{border-radius:10px}
.osx-app.osx-app .osx-row>.osx-row-b:hover:enabled{background:var(--ox-tint-2)}
.osx-app.osx-app .osx-row.osx-on>.osx-row-b,.osx-app.osx-app .osx-row.osx-on>.osx-row-b:hover:enabled{background:var(--ox-soft);border-color:var(--ox-ring)}
.osx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.osx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.osx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.osx-app .sl{font-weight:600}
.osx-app .sl.bad{color:var(--ox-ink-bad)}
.osx-app .sl.warn{color:var(--ox-ink-warn)}
.osx-app .sl.ok{color:var(--ox-ink-ok)}
.osx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.osx-app .osx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.osx-app.osx-app .osx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--ox-line);margin-top:4px}
.osx-app.osx-app .osx-pager>*{margin:0}
.osx-app.osx-app button.osx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.osx-app .osx-pager .widget-html-content,.osx-app .osx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app.osx-app .osx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--ox-bg);border-bottom:1px solid var(--ox-line);flex-wrap:wrap}
.osx-app.osx-app .osx-bar>*{margin:0}
.osx-app.osx-app button.osx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.osx-app.osx-app button.osx-ghost{background:transparent;border-color:transparent}
.osx-app.osx-app button.osx-ghost:hover:enabled{background:var(--ox-tint-2)}
.osx-app.osx-app button.osx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.osx-app.osx-app button.osx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--ox-ink-bad)}
.osx-app .osx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.osx-app .osx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.osx-app .osx-status .st{opacity:.72}
.osx-app .osx-status .st.warn{opacity:1;color:var(--ox-ink-warn)}
.osx-app .osx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.osx-app .osx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--ox-ok)}
.osx-app .osx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--ox-tint-2);user-select:all;-webkit-user-select:all}
.osx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--ox-accent);border-radius:50%;animation:osx-spin .8s linear infinite}
@keyframes osx-spin{to{transform:rotate(360deg)}}
.osx-app .skw{padding:6px 2px}
.osx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.osx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--ox-tint) 30%,var(--ox-tint-2) 50%,var(--ox-tint) 70%);animation:osx-glow 1.3s ease-in-out infinite}
.osx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.osx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.osx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes osx-glow{from{background-position:100% 0}to{background-position:0 0}}
.osx-app .osx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--ox-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.osx-app .osx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.osx-app *,.osx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.osx-app.osx-app .osx-row{height:52px}
.osx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.osx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--ox-tint-2)}
.osx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--ox-ink-ok)}
.osx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--ox-ink-bad)}
.osx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--ox-ink-warn)}
.osx-app .ei.run{background:rgba(59,130,246,.15);color:var(--ox-accent)}
.osx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.osx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.osx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.osx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--ox-tint-2);margin-left:6px;vertical-align:1px}
.osx-app .tag.x{background:rgba(219,39,119,.12);color:var(--ox-accent-2)}
.osx-app.osx-app .osx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--ox-line)}
.osx-app.osx-app .osx-lhead>*{margin:0;min-width:0}
.osx-app.osx-app button.osx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.osx-app.osx-app button.osx-col.osx-c-name{text-align:left;justify-content:flex-start}
.osx-app.osx-app button.osx-col:hover:enabled{background:transparent;opacity:1;color:var(--ox-accent)}
.osx-app.osx-app button.osx-col.osx-on{opacity:1;color:var(--ox-accent)}
.osx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.osx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.osx-app .mr .num.bad{color:var(--ox-ink-bad);font-weight:650}
.osx-app .mr .num.warn{color:var(--ox-ink-warn);font-weight:650}
.osx-app.osx-app .osx-machines{overflow:hidden auto}
.osx-app.osx-app .osx-machines>*{flex:0 0 auto}
.osx-app .osv h3 .badge{display:none}
.osx-app .osv h3{font-size:17px;margin:6px 0 2px}
.osx-app .osv .card{border-radius:12px;background:var(--ox-tint);border-color:var(--ox-line)}
.osx-app .osv .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.osx-app .osv .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.osx-app .osv .card.ok{border-color:rgba(16,185,129,.55)}
.osx-app .osv .note,.osx-app .osv .fi{border-radius:4px 10px 10px 4px}
.osx-app .osv pre{border-radius:10px}
"""

_OWN_CSS = """
.osx-app.osx-app .osx-docs .osx-row,.osx-app.osx-app .osx-indexes .osx-row{height:52px}
.osx-app .fr .fn .tag{margin-left:6px}
.osx-app .fz b{display:block;font-weight:600;opacity:1}
.osx-app .fz.warn b{color:var(--ox-ink-warn)}
.osx-app.osx-app .osx-search{gap:8px;align-items:center;flex-wrap:wrap;padding:0 0 10px;margin:0 0 6px;border-bottom:1px solid var(--ox-line)}
.osx-app.osx-app .osx-search>*{margin:0}
.osx-app.osx-app .osx-search .osx-find{flex:1 1 260px}
"""

_EXPLORER_CSS = "<style>\n" + _BASE_CSS + _OWN_CSS + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "overview": "the Domains tab",
    "indexes": "the Overview tab",
    "index_info": "the Indexes tab",
    "sample": "the Documents tab",
    "search": "the Search tab",
}

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he \w+ tab)")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (index_info('vectors-prod/docs') -> "the Indexes tab"), capitalised where it starts a sentence. A call with a
    filter (where=, which the window can't set), opensearch-py calls and AWS CLI commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        if call.startswith(".") or name not in _IN_WINDOW or "where=" in call:
            return call
        return _IN_WINDOW[name]

    swapped = _CALL_RE.sub(swap, str(text or ""))
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), swapped)


def _store_tone(store: Domain | Collection, warnings: int) -> str:
    """
    A domain's or collection's dot: red when it failed, amber when it's changing or has warnings, green otherwise.
    """
    status = _state_tone(store.status)
    if status:
        return status
    return "warn" if warnings else "ok"


def _store_note(store: Domain | Collection) -> str:
    """
    What a domain's or collection's line says under its name: the engine and nodes, or the collection's ID, its
    standby replicas and where it answers.
    """
    if isinstance(store, Domain):
        where = f"VPC {store.vpc}" if store.vpc else "public"
        return " · ".join(filter(None, [f"{store.engine} {store.version}".strip(), _node_label(store), where]))
    network = _NETWORK.get(store.network or "", "")
    standby = "standby replicas" if store.standby else "no standby (dev)"
    return " · ".join(filter(None, [store.id, standby, network]))


def _store_tag(store: Domain | Collection) -> str:
    if isinstance(store, Domain):
        return '<span class="tag">DOMAIN</span>'
    label = _KIND_TAGS.get(store.kind, store.kind or "COLLECTION")
    return f'<span class="tag{" x" if store.vector else ""}">{_esc(label)}</span>'


def _store_face(store: Domain | Collection, *, warnings: int, knn_memory: int | None, cost: float | None,
                words: list[str]) -> str:
    """
    A domain's or collection's line in the Domains tab: a dot for how it is, its name and kind, then its status,
    the memory its data nodes have for vectors, its estimated monthly cost and its warnings.
    """
    tone = _store_tone(store, warnings)
    status_tone = _state_tone(store.status)
    kind = _COLLECTION_KINDS.get(getattr(store, "kind", ""), "")
    memory = human_size(knn_memory) if knn_memory else "-"
    money = human_money(cost) if cost is not None else ("Serverless" if kind else "-")
    return (f'<div class="mr"><span class="dot {tone}"></span><div class="em"><div class="en">'
            f'{_marked(store.name, words)}{_store_tag(store)}</div><div class="ef">{_esc(_store_note(store))}</div>'
            f'</div><span class="num {status_tone}">{_esc(store.status or "?")}</span>'
            f'<span class="num">{_esc(memory)}</span><span class="num">{_esc(money)}</span>'
            f'<span class="num {"warn" if warnings else ""}">{warnings:,}</span></div>')


def _index_tone(info: IndexInfo, warnings: int) -> str:
    health = _state_tone(info.health or "")
    if health:
        return health
    return "warn" if warnings else "ok"


def _index_note(info: IndexInfo) -> str:
    """
    What an index's line says under its name: its first vector field in a few words, or that it has none.
    """
    if not info.vectors:
        return "no vector field"
    more = f" (+{len(info.vectors) - 1} more)" if len(info.vectors) > 1 else ""
    return f"{info.vectors[0].path}: {describe_vector_field(info.vectors[0])}{more}"


def _index_face(info: IndexInfo, *, warnings: int, words: list[str]) -> str:
    """
    An index's line in the Indexes tab: a dot for its health or warnings, its name, its first vector field (or that
    it has none), and its documents and size.
    """
    tone = _index_tone(info, warnings)
    flag = f" · {warnings:,} warning{'s' if warnings != 1 else ''}" if warnings else ""
    return (f'<div class="fr"><span class="dot {tone}"></span><div class="fm"><div class="fn">'
            f'{_marked(info.name, words)}</div><div class="ff">{_esc(_index_note(info))}</div></div>'
            f'<div class="fz {"warn" if warnings else ""}"><b>{_esc(_count(info.documents))} docs</b>'
            f'{_esc(human_size(info.size_bytes) + flag)}</div></div>')


def _document_face(doc: dict[str, Any], *, text_field: str | None, vector_field: str | None,
                   words: list[str]) -> str:
    """
    A document's line in the Documents tab: its ID and the start of its text (else where it came from), and its
    vector's size, or that it has none.
    """
    doc_id = str(doc.get("_id", ""))
    said = _hit_text(doc, text_field, 140) if text_field else _clip(source_of(doc), 140)
    vectors = values_at(doc, vector_field) if vector_field else []
    is_missing = bool(vector_field) and not vectors
    shape = f"{len(vectors[0]):,} dims" if vectors and isinstance(vectors[0], list) else ""
    return (f'<div class="fr"><span class="dot {"warn" if is_missing else "ok"}"></span><div class="fm">'
            f'<div class="fn">{_marked(doc_id, words)}</div><div class="ff">{_marked(said, words)}</div></div>'
            f'<div class="fz {"warn" if is_missing else ""}">{"<b>no vector</b>" if is_missing else _esc(shape)}</div>'
            "</div>")
