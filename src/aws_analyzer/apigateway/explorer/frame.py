"""
What the explorer window is drawn with: its tabs, stylesheet and colours (the Step Functions explorer's console look,
on the windows' terminal theme), the sentences from reports reworded for the window, and the faces of its lines (an
API, a route, a call) and the Calls tab's timeline.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..._kit.fmt import _fmt_dt, _plural, human_money
from ..._kit.render import _CALL_RE
from ..._kit.text import _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..blocks import _ms
from ..helpers import status_tone
from ..models import Api, ApiNumbers, Call, Route, RouteNumbers


_EXPLORER_TABS = (
    ("apis", "APIs", "Every API in the region and how it's doing: click one to open it",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("overview", "Overview", "How the API is doing, what needs attention, its stages and its routes",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("routes", "Routes", "Its routes as a tree: click one to see where a request goes and who can call it",
     "<circle cx='6' cy='6' r='2.5'/><circle cx='6' cy='18' r='2.5'/><circle cx='18' cy='12' r='2.5'/>"
     "<path d='M8.5 6h3a3 3 0 0 1 3 3v.5M8.5 18h3a3 3 0 0 0 3-3v-.5'/>"),
    ("calls", "Calls", "Its calls from the stage's access log: click one to see where its time went and the Lambda "
     "run behind it", "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/><path d='M7 9h6M7 12.5h10M7 16h4'/>"),
    ("performance", "Performance", "How long its calls take, route by route, and the slowest calls",
     "<circle cx='12' cy='13' r='7.5'/><path d='M12 13V9M9.5 2.5h5'/>"),
    ("stages", "Stages", "Each stage's URL, throttling, logs, tracing and cache",
     "<path d='M4 7h10M18 7h2M4 17h4M12 17h8'/><circle cx='16' cy='7' r='2'/><circle cx='10' cy='17' r='2'/>"),
)

_API_TABS = ("overview", "routes", "calls", "performance", "stages")

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h4M16 12h4"/><rect x="8" y="5" width="8" '
         'height="14" rx="2"/><path d="M11 9.5 13 12l-2 2.5"/></svg>')

_CALL_PAGE = 40

_TIMELINE_BARS = 48

_STATUS_ICONS = {"bad": "✕", "warn": "!", "ok": "✓", "": "•"}

_CALL_CHIPS = (("", "All", "all"), ("5xx", "5xx", "bad"), ("4xx", "4xx", "warn"), ("slow", "Slow", "warn"),
               ("429", "Throttled", "warn"), ("2xx", "OK", "ok"))

_SINCE_CHOICES = (("Last hour", "1h"), ("Last 24 hours", "24h"), ("Last 7 days", "7d"))

_METHOD_TONES = {"GET": "g", "POST": "a", "PUT": "b", "PATCH": "b", "DELETE": "r"}


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .agx-i-<key> sets --ax-icon, which a tab's ::before draws.
    """
    return "\n".join(f".agx-app .agx-i-{key}{{--ax-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--ax-accent:#60a5fa;--ax-accent-2:#f472b6;--ax-soft:rgba(96,165,250,.15);--ax-ring:rgba(96,165,250,"
                  ".38);--ax-raised:rgba(255,255,255,.11);--ax-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--ax-ink-bad:#f87171;--ax-ink-warn:#fbbf24;--ax-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--ax-accent:var(--agt-accent);--ax-button:var(--agt-accent);--ax-accent-2:var(--agt-prompt);"
                    "--ax-soft:var(--agt-hover);--ax-ring:var(--agt-accent);--ax-line:var(--agt-line);"
                    "--ax-line-2:var(--agt-line);--ax-tint:transparent;--ax-tint-2:var(--agt-hover);--ax-bg:var(--agt-bg);"
                    "--ax-surface:transparent;--ax-raised:var(--agt-hover);--ax-shadow:none;--ax-ok:var(--agt-ok);"
                    "--ax-warn:var(--agt-warn);--ax-bad:var(--agt-bad);--ax-ink-ok:var(--agt-ok);"
                    "--ax-ink-warn:var(--agt-warn);--ax-ink-bad:var(--agt-bad)")

_API_GATEWAY_PURPLE = "#8c4fff"

_TERMINAL_CSS = _terminal_theme((".agx-app",), "agt") + """
.agx-app.agx-app.agx-app.agx-app{""" + _TERMINAL_TOKENS + """}
.agx-app.agx-app{font-family:var(--agt-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.agx-app.agx-app .widget-html-content,.agx-app.agx-app .widget-label,.agx-app.agx-app input,.agx-app.agx-app select{font-family:var(--agt-mono)}
.agx-app.agx-app button.jupyter-button{font-family:var(--agt-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.agx-app.agx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--agt-accent);color:var(--agt-accent)}
.agx-app.agx-app button.jupyter-button.mod-primary,.agx-app.agx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--agt-accent);color:var(--agt-accent);font-weight:700;filter:none}
.agx-app.agx-app .widget-text input,.agx-app.agx-app .jupyter-widget-text input,.agx-app.agx-app .widget-dropdown>select,.agx-app.agx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.agx-app.agx-app .widget-text input:focus,.agx-app.agx-app .jupyter-widget-text input:focus,.agx-app.agx-app .widget-dropdown>select:focus,.agx-app.agx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.agx-app.agx-app .widget-toggle-buttons,.agx-app.agx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.agx-app.agx-app .widget-toggle-buttons .widget-toggle-button,.agx-app.agx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--agt-line);border-radius:6px;opacity:1;color:var(--agt-dim)}
.agx-app.agx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.agx-app.agx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--agt-accent);color:var(--agt-accent);font-weight:700;box-shadow:none}
.agx-app .agx-logo{width:30px;height:30px;border-radius:7px;color:""" + _API_GATEWAY_PURPLE + """;background:transparent;box-shadow:inset 0 0 0 1px var(--agt-line)}
.agx-app .agx-name{font-size:14px;font-weight:700}
.agx-app .agx-name::before{content:"\\276F  ";color:var(--agt-prompt)}
.agx-app .agx-name span{color:var(--agt-comment);opacity:1;font-size:12px}
.agx-app .agx-sub{color:var(--agt-comment);opacity:1;font-size:11.5px}
.agx-app .agx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.agx-app .agx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--agt-comment);opacity:1}
.agx-app .agx-stat .l::before{content:"# "}
.agx-app .agx-stat b{font-size:14px}
.agx-app .agx-stat.warn{border-color:var(--agt-warn);background:transparent}
.agx-app .agx-stat.warn b{color:var(--agt-warn)}
.agx-app .agx-stat.bad{border-color:var(--agt-bad);background:transparent}
.agx-app .agx-stat.bad b{color:var(--agt-bad)}
.agx-app .agx-stat.ok{border-color:var(--agt-line)}
.agx-app .agx-stat.ok b{color:var(--agt-ok)}
.agx-app.agx-app .agx-trig>.agx-trig-b{border-radius:6px;box-shadow:none}
.agx-app.agx-app .agx-open .agx-trig>.agx-trig-b{box-shadow:none}
.agx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--agt-comment);opacity:1}
.agx-app .fxl::before{content:"# "}
.agx-app .fxi,.agx-app .opi{font-family:var(--agt-mono);color:var(--agt-comment);opacity:1}
.agx-app.agx-app .agx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.agx-app.agx-app .agx-opt>.agx-opt-b,.agx-app.agx-app .agx-row>.agx-row-b{border-radius:5px}
.agx-app.agx-app .agx-opt.agx-on>.agx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--agt-accent)}
.agx-app.agx-app .agx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--agt-line)}
.agx-app.agx-app button.agx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--agt-dim)}
.agx-app.agx-app button.agx-tab:hover:enabled{background:transparent;color:var(--agt-accent);opacity:1}
.agx-app.agx-app button.agx-tab.agx-on,.agx-app.agx-app button.agx-tab.agx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--agt-accent);color:var(--agt-accent);font-weight:700}
.agx-app.agx-app button.agx-small,.agx-app.agx-app button.agx-chip,.agx-app.agx-app button.agx-x{border-radius:6px}
.agx-app.agx-app button.agx-chip.agx-on,.agx-app.agx-app button.agx-chip.agx-on:hover:enabled{background:transparent;border-color:var(--agt-accent);font-weight:700}
.agx-app.agx-app .agx-left,.agx-app.agx-app .agx-machines{border-radius:8px}
.agx-app.agx-app .agx-machines{border:1px solid var(--agt-line);padding:0 4px 4px 0;overflow:visible}
.agx-app.agx-app .agx-lhead{border-bottom:1px solid var(--agt-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.agx-app.agx-app button.agx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--agt-comment);opacity:1}
.agx-app.agx-app button.agx-col.agx-c-name::before{content:"# "}
.agx-app.agx-app button.agx-col:hover:enabled{background:transparent;color:var(--agt-accent)}
.agx-app.agx-app button.agx-col.agx-on{color:var(--agt-accent);font-weight:700}
.agx-app.agx-app .agx-row.agx-on>.agx-row-b,.agx-app.agx-app .agx-row.agx-on>.agx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--agt-accent)}
.agx-app .tag,.agx-app .ei{border-radius:4px}
.agx-app .tag{font-weight:400;letter-spacing:0;color:var(--agt-comment)}
.agx-app .tag.x{background:rgba(140,79,255,.12);color:""" + _API_GATEWAY_PURPLE + """}
.agx-app .ef,.agx-app .ff,.agx-app .ez,.agx-app .fz{color:var(--agt-comment);opacity:1}
.agx-app .agx-pager .widget-html-content,.agx-app .agx-pager .jupyter-widget-html-content{color:var(--agt-dim);opacity:1}
.agx-app.agx-app .agx-pager{border-top:0}
.agx-app .agx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--agt-comment);opacity:1}
.agx-app .agx-label::before{content:"# "}
.agx-app .apg .card{border-radius:6px}
.agx-app .apg .note,.agx-app .apg .fi{border-radius:3px 6px 6px 3px}
.agx-app .apg pre{border-radius:6px}
.agx-app .agx-status{color:var(--agt-dim)}
.agx-app .agx-status .st{opacity:1}
.agx-app .agx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--agt-ok)}
.agx-app .agx-status code{border-radius:4px;color:var(--agt-s)}
.agx-app .agx-hint{border-radius:6px}
.agx-app .spin{border-top-color:var(--agt-accent)}"""

_BASE_CSS = """.agx-app{--ax-accent:#2563eb;--ax-button:#2563eb;--ax-accent-2:#db2777;--ax-soft:rgba(37,99,235,.10);--ax-ring:rgba(37,99,235,.28);--ax-line:rgba(127,127,127,.22);--ax-line-2:rgba(127,127,127,.36);--ax-tint:rgba(127,127,127,.06);--ax-tint-2:rgba(127,127,127,.11);--ax-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--ax-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--ax-raised:var(--ax-surface);--ax-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--ax-ok:#10b981;--ax-warn:#f59e0b;--ax-bad:#ef4444;--ax-ink-bad:#dc2626;--ax-ink-warn:#b45309;--ax-ink-ok:#047857}
body[data-jp-theme-light="false"] .agx-app,body.vscode-dark .agx-app,body.vscode-high-contrast .agx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .agx-app{""" + _EXPLORER_DARK + """}}
.agx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--ax-line);border-radius:18px;padding:14px 16px 10px;background:var(--ax-bg);box-shadow:var(--ax-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.agx-app *{box-sizing:border-box}
.agx-app .widget-html-content,.agx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.agx-app.agx-app .agx-flat>*{margin:0}
.agx-app.agx-app button.jupyter-button{color:inherit;background:var(--ax-tint);border:1px solid var(--ax-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.agx-app.agx-app button.jupyter-button:hover:enabled{background:var(--ax-tint-2);border-color:var(--ax-line-2);box-shadow:none}
.agx-app.agx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.agx-app.agx-app button.jupyter-button:focus-visible{outline:2px solid var(--ax-ring);outline-offset:-1px}
.agx-app.agx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.agx-app.agx-app button.jupyter-button.mod-primary{background:var(--ax-button);border-color:transparent;color:#fff;font-weight:600}
.agx-app.agx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--ax-button);filter:brightness(1.08)}
.agx-app.agx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.agx-app.agx-app .widget-text input,.agx-app.agx-app .jupyter-widget-text input,.agx-app.agx-app .widget-dropdown>select,.agx-app.agx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--ax-line-2);border-radius:9px;background-color:var(--ax-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.agx-app.agx-app .widget-dropdown>select,.agx-app.agx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.agx-app.agx-app .widget-text input:focus,.agx-app.agx-app .jupyter-widget-text input:focus,.agx-app.agx-app .widget-dropdown>select:focus,.agx-app.agx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--ax-accent);box-shadow:0 0 0 3px var(--ax-soft)}
.agx-app.agx-app .widget-text,.agx-app.agx-app .widget-dropdown,.agx-app.agx-app .jupyter-widget-text,.agx-app.agx-app .jupyter-widget-dropdown{margin:0;height:auto}
.agx-app.agx-app .widget-toggle-buttons,.agx-app.agx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--ax-tint-2);gap:2px;flex:0 0 auto;margin:0}
.agx-app.agx-app .widget-toggle-buttons .widget-toggle-button,.agx-app.agx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.agx-app.agx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.agx-app.agx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--ax-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.agx-app.agx-app .widget-checkbox input[type=checkbox],.agx-app.agx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--ax-accent)}
.agx-app.agx-app .agx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--ax-line);overflow:visible}
.agx-app.agx-app .agx-top{align-items:center;gap:12px;overflow:visible}
.agx-app.agx-app .agx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.agx-app .agx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.agx-app .agx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--ax-accent),var(--ax-accent-2));box-shadow:0 2px 8px var(--ax-ring);flex:0 0 auto}
.agx-app .agx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .agx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.agx-app .agx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .agx-stats{display:flex;flex-wrap:wrap;gap:8px}
.agx-app .agx-stat{border:1px solid var(--ax-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--ax-tint)}
.agx-app .agx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.agx-app .agx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.agx-app .agx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.agx-app .agx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.agx-app .agx-stat.ok{border-color:rgba(16,185,129,.55)}
.agx-app .agx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--ax-tint-2) 30%,var(--ax-line) 50%,var(--ax-tint-2) 70%);animation:agx-glow 1.3s ease-in-out infinite}
.agx-app.agx-app .agx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.agx-app.agx-app .agx-field.agx-open{z-index:41}
.agx-app.agx-app .agx-trig{position:relative;min-height:52px;overflow:visible}
.agx-app.agx-app .agx-trig>.agx-trig-b,.agx-app.agx-app .agx-opt>.agx-opt-b,.agx-app.agx-app .agx-row>.agx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.agx-app.agx-app .agx-trig>.agx-trig-b{border-color:var(--ax-line-2);border-radius:12px;background:var(--ax-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.agx-app.agx-app .agx-trig>.agx-trig-b:hover:enabled{border-color:var(--ax-accent);background:var(--ax-surface)}
.agx-app.agx-app .agx-open .agx-trig>.agx-trig-b{border-color:var(--ax-accent);box-shadow:0 0 0 3px var(--ax-soft)}
.agx-app.agx-app .agx-trig>.agx-trig-b:active:enabled,.agx-app.agx-app .agx-opt>.agx-opt-b:active:enabled,.agx-app.agx-app .agx-row>.agx-row-b:active:enabled{transform:none}
.agx-app.agx-app .agx-trig>.agx-face,.agx-app.agx-app .agx-opt>.agx-opt-t,.agx-app.agx-app .agx-row>.agx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.agx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.agx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.agx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.agx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.agx-app .fxi,.agx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.agx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.agx-app .agx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--ax-accent)}
.agx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.agx-app .dot.ok{background:var(--ax-ok)}
.agx-app .dot.warn{background:var(--ax-warn)}
.agx-app .dot.bad{background:var(--ax-bad)}
.agx-app.agx-app .agx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--ax-line-2);border-radius:14px;background:var(--ax-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.agx-app.agx-app .agx-pop>*{margin:0}
.agx-app.agx-app .agx-pop .agx-x{margin-left:6px}
.agx-app.agx-app .agx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.agx-app.agx-app .agx-opts>*{flex:0 0 auto}
.agx-app.agx-app .agx-opt{position:relative;margin:0 0 2px;overflow:visible}
.agx-app.agx-app .agx-opt>.agx-opt-b{border-radius:10px}
.agx-app.agx-app .agx-opt>.agx-opt-b:hover:enabled{background:var(--ax-tint-2)}
.agx-app.agx-app .agx-opt.agx-on>.agx-opt-b{background:var(--ax-soft);border-color:var(--ax-ring)}
.agx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.agx-app .op .dot{margin-top:6px}
.agx-app .opb{flex:1 1 auto;min-width:0}
.agx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.agx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.agx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--ax-line);line-height:1.4}
.agx-app .opf.warn{opacity:1;color:var(--ax-ink-warn)}
.agx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.agx-app.agx-app .agx-backdrop,.agx-app.agx-app .agx-backdrop:hover:enabled,.agx-app.agx-app .agx-backdrop:active:enabled,.agx-app.agx-app .agx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.agx-app.agx-app .agx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--ax-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.agx-app.agx-app button.agx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.agx-app.agx-app button.agx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--ax-icon) center/contain no-repeat;mask:var(--ax-icon) center/contain no-repeat}
.agx-app.agx-app button.agx-tab:hover:enabled{background:var(--ax-tint);opacity:.95}
.agx-app.agx-app button.agx-tab.agx-on,.agx-app.agx-app button.agx-tab.agx-on:hover:enabled{background:var(--ax-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.agx-app.agx-app button.agx-tab.agx-on::before{background:var(--ax-accent)}
.agx-app.agx-app button.agx-tab.agx-alert::after,.agx-app.agx-app button.agx-tab.agx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--ax-warn);flex:0 0 auto}
.agx-app.agx-app button.agx-tab.agx-alarm::after{background:var(--ax-bad)}
@container (max-width:520px){.agx-app.agx-app button.agx-tab::before{display:none}}
.agx-app.agx-app .agx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .agx-app.agx-app .agx-page{height:620px}
.agx-app.agx-app .agx-page>*{flex:0 0 auto}
.agx-app.agx-app .agx-page.agx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.agx-app.agx-app .agx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--ax-line);border-radius:14px;padding:10px 10px 6px;background:var(--ax-surface);overflow:hidden}
.agx-app.agx-app .agx-left>*{margin:0;flex:0 0 auto}
.agx-app.agx-app .agx-left,.agx-app.agx-app .agx-right,.agx-app.agx-app .agx-rows{min-height:0}
@media (max-width:900px){.agx-app.agx-app .agx-page.agx-split{flex-wrap:wrap;overflow:hidden auto}.agx-app.agx-app .agx-left{flex:1 1 100%;max-width:100%;height:440px}.agx-app.agx-app .agx-right{flex:1 1 100%;overflow:visible}}
.agx-app.agx-app .agx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.agx-app.agx-app .agx-right>*{flex:0 0 auto;margin:0}
.agx-app.agx-app .agx-find{position:relative;align-items:center}
.agx-app.agx-app .agx-find input,.agx-app.agx-app .agx-find input:focus{padding-left:32px;background:var(--ax-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.agx-app.agx-app .agx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.agx-app.agx-app .agx-chips>*{margin:0}
.agx-app.agx-app button.agx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--ax-line);display:inline-flex;align-items:center;gap:6px}
.agx-app.agx-app button.agx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.agx-app.agx-app button.agx-chip.agx-t-all::before{display:none}
.agx-app.agx-app button.agx-chip.agx-t-bad::before{background:var(--ax-bad)}
.agx-app.agx-app button.agx-chip.agx-t-warn::before{background:var(--ax-warn)}
.agx-app.agx-app button.agx-chip.agx-t-ok::before{background:var(--ax-ok)}
.agx-app.agx-app button.agx-chip.agx-on,.agx-app.agx-app button.agx-chip.agx-on:hover:enabled{background:var(--ax-soft);border-color:var(--ax-ring);color:var(--ax-accent);font-weight:650}
.agx-app.agx-app .agx-filters{gap:6px;margin:8px 0 0;align-items:center}
.agx-app.agx-app .agx-filters>*{margin:0}
.agx-app.agx-app .agx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.agx-app.agx-app .agx-rows>*{flex:0 0 auto}
.agx-app.agx-app .agx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.agx-app.agx-app .agx-row>.agx-row-b{border-radius:10px}
.agx-app.agx-app .agx-row>.agx-row-b:hover:enabled{background:var(--ax-tint-2)}
.agx-app.agx-app .agx-row.agx-on>.agx-row-b,.agx-app.agx-app .agx-row.agx-on>.agx-row-b:hover:enabled{background:var(--ax-soft);border-color:var(--ax-ring)}
.agx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.agx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.agx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.agx-app .sl{font-weight:600}
.agx-app .sl.bad{color:var(--ax-ink-bad)}
.agx-app .sl.warn{color:var(--ax-ink-warn)}
.agx-app .sl.ok{color:var(--ax-ink-ok)}
.agx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.agx-app .agx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.agx-app.agx-app .agx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--ax-line);margin-top:4px}
.agx-app.agx-app .agx-pager>*{margin:0}
.agx-app.agx-app button.agx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.agx-app .agx-pager .widget-html-content,.agx-app .agx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app.agx-app .agx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--ax-bg);border-bottom:1px solid var(--ax-line);flex-wrap:wrap}
.agx-app.agx-app .agx-bar>*{margin:0}
.agx-app.agx-app button.agx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.agx-app.agx-app button.agx-ghost{background:transparent;border-color:transparent}
.agx-app.agx-app button.agx-ghost:hover:enabled{background:var(--ax-tint-2)}
.agx-app.agx-app button.agx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.agx-app.agx-app button.agx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--ax-ink-bad)}
.agx-app .agx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.agx-app .agx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.agx-app .agx-status .st{opacity:.72}
.agx-app .agx-status .st.warn{opacity:1;color:var(--ax-ink-warn)}
.agx-app .agx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.agx-app .agx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--ax-ok)}
.agx-app .agx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--ax-tint-2);user-select:all;-webkit-user-select:all}
.agx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--ax-accent);border-radius:50%;animation:agx-spin .8s linear infinite}
@keyframes agx-spin{to{transform:rotate(360deg)}}
.agx-app .skw{padding:6px 2px}
.agx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.agx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--ax-tint) 30%,var(--ax-tint-2) 50%,var(--ax-tint) 70%);animation:agx-glow 1.3s ease-in-out infinite}
.agx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.agx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.agx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes agx-glow{from{background-position:100% 0}to{background-position:0 0}}
.agx-app .agx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--ax-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.agx-app .agx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.agx-app *,.agx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.agx-app.agx-app .agx-row{height:52px}
.agx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.agx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--ax-tint-2)}
.agx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--ax-ink-ok)}
.agx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--ax-ink-bad)}
.agx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--ax-ink-warn)}
.agx-app .ei.run{background:rgba(59,130,246,.15);color:var(--ax-accent)}
.agx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.agx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.agx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.agx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--ax-tint-2);margin-left:6px;vertical-align:1px}
.agx-app .tag.x{background:rgba(219,39,119,.12);color:var(--ax-accent-2)}
.agx-app.agx-app .agx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.6fr) repeat(5,minmax(70px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--ax-line)}
.agx-app.agx-app .agx-lhead>*{margin:0;min-width:0}
.agx-app.agx-app button.agx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app.agx-app button.agx-col.agx-c-name{text-align:left;justify-content:flex-start}
.agx-app.agx-app button.agx-col:hover:enabled{background:transparent;opacity:1;color:var(--ax-accent)}
.agx-app.agx-app button.agx-col.agx-on{opacity:1;color:var(--ax-accent)}
.agx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.6fr) repeat(5,minmax(70px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.agx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.agx-app .mr .num.bad{color:var(--ax-ink-bad);font-weight:650}
.agx-app .mr .num.warn{color:var(--ax-ink-warn);font-weight:650}
.agx-app.agx-app .agx-machines{overflow:hidden auto}
.agx-app.agx-app .agx-machines>*{flex:0 0 auto}
.agx-app .apg h3 .badge{display:none}
.agx-app .apg h3{font-size:17px;margin:6px 0 2px}
.agx-app .apg .card{border-radius:12px;background:var(--ax-tint);border-color:var(--ax-line)}
.agx-app .apg .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.agx-app .apg .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.agx-app .apg .card.ok{border-color:rgba(16,185,129,.55)}
.agx-app .apg .note,.agx-app .apg .fi{border-radius:4px 10px 10px 4px}
.agx-app .apg pre{border-radius:10px}
"""

_OWN_CSS = """
.agx-app.agx-app .agx-routes{flex:0 0 360px}
.agx-app.agx-app .agx-routes .agx-row{height:34px;margin:0}
.agx-app .rr{display:flex;align-items:center;gap:8px;height:34px;padding:0 10px 0 calc(8px + var(--d,0) * 14px);min-width:0;white-space:nowrap}
.agx-app .rr .rp{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.agx-app .rr .rz{flex:0 0 auto;font-size:11px;opacity:.65;font-variant-numeric:tabular-nums}
.agx-app .rr .rz.bad{color:var(--ax-ink-bad);opacity:1;font-weight:650}
.agx-app .rfold{padding:6px 10px 2px calc(8px + var(--d,0) * 14px);font-size:12px;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agx-app .mth{display:inline-block;flex:0 0 52px;text-align:center;font-size:10px;font-weight:700;border-radius:4px;padding:1px 0;background:var(--ax-tint-2)}
.agx-app .mth.g{background:rgba(16,185,129,.15);color:var(--ax-ink-ok)}
.agx-app .mth.a{background:rgba(245,158,11,.18);color:var(--ax-ink-warn)}
.agx-app .mth.b{background:rgba(59,130,246,.15);color:var(--ax-accent)}
.agx-app .mth.r{background:rgba(239,68,68,.14);color:var(--ax-ink-bad)}
.agx-app .opn-tag{color:var(--ax-ink-warn);font-size:10.5px;font-weight:650;margin-left:6px}
.agx-app .cr{display:grid;grid-template-columns:22px 64px 38px minmax(90px,1.2fr) 56px minmax(0,1.6fr);gap:10px;align-items:center;height:44px;padding:0 10px 0 8px;min-width:0;white-space:nowrap}
.agx-app .cr>span{overflow:hidden;text-overflow:ellipsis;min-width:0}
.agx-app .cr .cs{font-weight:700}
.agx-app .cr .cs.bad,.agx-app .cr .cw.bad{color:var(--ax-ink-bad)}
.agx-app .cr .cs.warn,.agx-app .cr .cw.warn{color:var(--ax-ink-warn)}
.agx-app .cr .cs.ok{color:var(--ax-ink-ok)}
.agx-app .cr .ct{text-align:right;font-variant-numeric:tabular-nums}
.agx-app .cr .cw{font-size:11.5px;opacity:.85}
.agx-app.agx-app .agx-calls .agx-row{height:44px}
.agx-app.agx-app .agx-left.agx-calls{flex:0 0 520px;max-width:56%}
.agx-app .tlb{display:flex;align-items:flex-end;gap:2px;height:42px;margin:8px 0 2px}
.agx-app .tlb i{flex:1 1 0;min-width:2px;border-radius:2px 2px 0 0;background:rgba(96,165,250,.55);display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden}
.agx-app .tlb i b{display:block;background:rgba(239,68,68,.8)}
.agx-app .tlb i.none{background:var(--ax-tint-2);height:2px}
.agx-app .tla{display:flex;justify-content:space-between;font-size:10.5px;opacity:.6}
.agx-app .tls{font-size:12px;margin:4px 0 0}
.agx-app .tls .bad{color:var(--ax-ink-bad)}
.agx-app .tls .warn{color:var(--ax-ink-warn)}
"""

_EXPLORER_CSS = "<style>\n" + _BASE_CSS + _OWN_CSS + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "apis": "the APIs tab",
    "api_info": "the Overview tab",
    "routes": "the Routes tab",
    "stages": "the Stages tab",
    "calls": "the Calls tab",
    "errors": "the Calls tab",
    "performance": "the Performance tab",
}

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he \w+ tab)")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (errors('orders-api') -> "the Calls tab"), capitalised where it starts a sentence; Lambda's calls and AWS CLI
    commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        if call.startswith(".") or name not in _IN_WINDOW:
            return call
        return _IN_WINDOW[name]

    swapped = _CALL_RE.sub(swap, str(text or ""))
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), swapped)



def _api_note(api: Api) -> str:
    """
    What an API's line says under its name: its stages, its routes and the functions they call.
    """
    stages = ", ".join(s.name for s in api.stages) or "not deployed"
    open_routes = len(api.open_routes)
    parts = [stages, _plural(len(api.routes), "route")]
    if open_routes:
        parts.append(f"{open_routes} open to anyone")
    if api.functions:
        parts.append("→ " + ", ".join(api.functions[:3]))
    return " · ".join(parts)


def _api_tone(numbers: ApiNumbers | None) -> str:
    if numbers is None:
        return ""
    if numbers.errors_5xx and numbers.rate_5xx >= 0.01:
        return "bad"
    return "ok" if numbers.requests else ""


def _api_face(api: Api, numbers: ApiNumbers | None, cost: float | None, auth: str, words: list[str]) -> str:
    """
    An API's line in the APIs tab: a dot for how it's doing, its name and kind, then its requests, 5xx share, p95,
    cost and who can call it.
    """
    tone = _api_tone(numbers)
    kind = f'<span class="tag{" x" if api.kind != "REST" else ""}">{_esc(api.kind_label.upper())}</span>'
    requests = f"{numbers.requests:,}" if numbers else "-"
    share = f"{numbers.rate_5xx:.1%}" if numbers and numbers.requests else "-"
    p95 = _ms(numbers.p95_ms) if numbers else "-"
    auth_tone = "warn" if auth.startswith("none") else ""
    return (f'<div class="mr"><span class="dot {tone}"></span><div class="em"><div class="en">'
            f'{_marked(api.name, words)}{kind}</div><div class="ef">{_esc(_api_note(api))}</div></div>'
            f'<span class="num">{requests}</span><span class="num {"bad" if tone == "bad" else ""}">{share}</span>'
            f'<span class="num">{_esc(p95)}</span><span class="num">{_esc(human_money(cost))}</span>'
            f'<span class="num {auth_tone}">{_esc(auth)}</span></div>')


def _route_face(route: Route, leaf: str, numbers: RouteNumbers | None, tone: str, depth: int,  # noqa: PLR0913
                words: list[str]) -> str:
    """
    A route's line in the Routes tab's tree: its method, the last part of its path (`leaf`; the folders are lines
    above it, and a folder's own methods show none), a tag when anyone can call it, and its p95 (red when it comes
    close to the time limit).
    """
    method = route.method or "WS"
    tag = '<span class="opn-tag">OPEN</span>' if route.is_open else ""
    figure = ""
    if numbers is not None and numbers.p95_ms is not None:
        figure = f"{numbers.rate_5xx:.1%} · {_ms(numbers.p95_ms)}" if numbers.errors_5xx else _ms(numbers.p95_ms)
    return (f'<div class="rr" style="--d:{depth}"><span class="mth {_METHOD_TONES.get(method, "")}">{_esc(method)}'
            f'</span><span class="rp">{_marked(leaf, words)}{tag}</span><span class="rz {tone}">{_esc(figure)}</span>'
            "</div>")


def _folder_face(path: str, depth: int) -> str:
    return f'<div class="rfold" style="--d:{depth}">▾ {_esc(path)}</div>'


def _call_face(call: Call, words: list[str]) -> str:
    """
    A call's line in the Calls tab: its status as an icon and a code, when, its route, how long it took and its error
    (or its request ID when it has none).
    """
    tone = status_tone(call.status)
    said = call.why or call.request_id
    return (f'<div class="cr"><span class="ei {tone}">{_STATUS_ICONS.get(tone, "•")}</span>'
            f'<span>{_esc(_fmt_dt(call.time, seconds=True)[-8:])}</span><span class="cs {tone}">'
            f'{_esc(str(call.status or "-"))}</span><span>{_marked(call.route_key, words)}</span><span class="ct">'
            f'{_esc(_ms(call.latency_ms))}</span><span class="cw {tone if call.why else ""}">'
            f'{_marked(said, words)}</span></div>')


def _bar(count: int, failed: int, most: int) -> str:
    """
    One bar of the timeline: as tall as its calls, the failed ones red at its foot.
    """
    if not count:
        return '<i class="none"></i>'
    red = f'<b style="height:{failed / count * 100:.0f}%"></b>' if failed else ""
    return f'<i style="height:{max(4, count / most * 100):.0f}%">{red}</i>'


def _timeline_html(calls: list[Call], since: datetime, until: datetime) -> str:
    """
    The calls over the time read, as bars (the height is how many, red where some failed), with the times under them
    and a line of the failures by status.
    """
    span = max(1.0, (until - since).total_seconds())
    counts, failed = [0] * _TIMELINE_BARS, [0] * _TIMELINE_BARS
    for call in calls:
        slot = min(_TIMELINE_BARS - 1, max(0, int((call.time - since).total_seconds() / span * _TIMELINE_BARS)))
        counts[slot] += 1
        failed[slot] += 1 if (call.status or 0) >= 500 else 0
    most = max(counts) or 1
    bars = "".join(_bar(n, bad, most) for n, bad in zip(counts, failed))
    middle = since + (until - since) / 2
    axis = "".join(f"<span>{_esc(_fmt_dt(moment))}</span>" for moment in (since, middle, until))
    return f'<div class="tlb">{bars}</div><div class="tla">{axis}</div>'


def _failures_line(calls: list[Call]) -> str:
    """
    The failed calls by status, in a line: '57 failed · 504 ×36 · 401 ×14 · ...'.
    """
    by_status: dict[int, int] = {}
    for call in calls:
        if call.failed:
            by_status[call.status or 0] = by_status.get(call.status or 0, 0) + 1
    if not by_status:
        return f'<div class="tls">{len(calls):,} calls, none failed</div>'
    parts = [f'<span class="{status_tone(code)}">{code} ×{count:,}</span>'
             for code, count in sorted(by_status.items(), key=lambda item: -item[1])]
    return f'<div class="tls">{sum(by_status.values()):,} of {len(calls):,} failed · {" · ".join(parts)}</div>'
