"""
What the Athena explorer window is drawn with: its tabs, stylesheet and colours (the OpenSearch explorer's console
look, on the windows' terminal theme), the sentences from reports reworded for the window, and the faces of its lines
(a query, a kind of query, a kind of failure, a table, a crawler).
"""

from __future__ import annotations

import re
from typing import Any

from ..._kit.fmt import _duration, _fmt_dt, human_age, human_money, human_size
from ..._kit.render import _CALL_RE
from ..._kit.text import _clip, _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..helpers import STATE_TONES, one_line, schedule_period
from ..models import Crawler, FailureGroup, Query, QueryShape, Table


_EXPLORER_TABS = (
    ("overview", "Overview", "What queries cost and which fail, each workgroup's limits, and what to fix first",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("queries", "Queries", "Every query run in the time picked: click one for its SQL, numbers and saved results",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("costs", "Costs", "The same query's runs added up, costliest first: click one for its newest run",
     "<circle cx='12' cy='12' r='8.5'/><path d='M14.8 9.2c-.6-.8-1.6-1.2-2.8-1.2-1.7 0-2.8.8-2.8 2s1.1 1.7 2.8 2 "
     "2.8.8 2.8 2-1.1 2-2.8 2c-1.2 0-2.2-.4-2.8-1.2M12 6.5v11'/>"),
    ("failures", "Failures", "Why queries failed, by kind, with the fix: click one for its newest failed query",
     "<path d='M12 3.5 21 19.5H3z'/><path d='M12 9.5v4.5M12 16.8h.01'/>"),
    ("catalog", "Catalog", "Every Glue table and whether its S3 folder holds files: click one for its partitions, "
     "size, queries and the SQL to copy",
     "<ellipse cx='12' cy='6' rx='7.5' ry='2.8'/><path d='M4.5 6v12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6'/>"
     "<path d='M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8'/>"),
    ("crawlers", "Crawlers", "Every Glue crawler, its schedule and its last crawl: click one for what's wrong with it",
     "<circle cx='12' cy='12' r='3'/><path d='M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2.1 2.1M15.9 15.9 18 18"
     "M6 18l2.1-2.1M15.9 8.1 18 6'/>"),
)

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 9 12 4l8.5 5"/><path d="M5.5 9.5v8M9.5 9.5v8'
         'M14.5 9.5v8M18.5 9.5v8M3.5 20h17"/></svg>')

_SINCE_CHOICES = (("Last 24 hours", "24h"), ("Last 7 days", "7d"), ("Last 30 days", "30d"))

_SINCE_DAYS = {"24h": 1, "7d": 7, "30d": 30}

_SQL_CHARS = 110

_NOTE_CHARS = 120


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .atx-i-<key> sets --tx-icon, which a tab's ::before draws.
    """
    return "\n".join(f".atx-app .atx-i-{key}{{--tx-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--tx-accent:#60a5fa;--tx-accent-2:#f472b6;--tx-soft:rgba(96,165,250,.15);--tx-ring:rgba(96,165,250,"
                  ".38);--tx-raised:rgba(255,255,255,.11);--tx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--tx-ink-bad:#f87171;--tx-ink-warn:#fbbf24;--tx-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--tx-accent:var(--att-accent);--tx-button:var(--att-accent);--tx-accent-2:var(--att-prompt);"
                    "--tx-soft:var(--att-hover);--tx-ring:var(--att-accent);--tx-line:var(--att-line);"
                    "--tx-line-2:var(--att-line);--tx-tint:transparent;--tx-tint-2:var(--att-hover);--tx-bg:var(--att-bg);"
                    "--tx-surface:transparent;--tx-raised:var(--att-hover);--tx-shadow:none;--tx-ok:var(--att-ok);"
                    "--tx-warn:var(--att-warn);--tx-bad:var(--att-bad);--tx-ink-ok:var(--att-ok);"
                    "--tx-ink-warn:var(--att-warn);--tx-ink-bad:var(--att-bad)")

_ATHENA_PURPLE = "#8c4fff"

_TERMINAL_CSS = _terminal_theme((".atx-app",), "att") + """
.atx-app.atx-app.atx-app.atx-app{""" + _TERMINAL_TOKENS + """}
.atx-app.atx-app{font-family:var(--att-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.atx-app.atx-app .widget-html-content,.atx-app.atx-app .widget-label,.atx-app.atx-app input,.atx-app.atx-app select{font-family:var(--att-mono)}
.atx-app.atx-app button.jupyter-button{font-family:var(--att-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.atx-app.atx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--att-accent);color:var(--att-accent)}
.atx-app.atx-app button.jupyter-button.mod-primary,.atx-app.atx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--att-accent);color:var(--att-accent);font-weight:700;filter:none}
.atx-app.atx-app .widget-text input,.atx-app.atx-app .jupyter-widget-text input,.atx-app.atx-app .widget-dropdown>select,.atx-app.atx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.atx-app.atx-app .widget-text input:focus,.atx-app.atx-app .jupyter-widget-text input:focus,.atx-app.atx-app .widget-dropdown>select:focus,.atx-app.atx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.atx-app.atx-app .widget-toggle-buttons,.atx-app.atx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.atx-app.atx-app .widget-toggle-buttons .widget-toggle-button,.atx-app.atx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--att-line);border-radius:6px;opacity:1;color:var(--att-dim)}
.atx-app.atx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.atx-app.atx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--att-accent);color:var(--att-accent);font-weight:700;box-shadow:none}
.atx-app .atx-logo{width:30px;height:30px;border-radius:7px;color:""" + _ATHENA_PURPLE + """;background:transparent;box-shadow:inset 0 0 0 1px var(--att-line)}
.atx-app .atx-name{font-size:14px;font-weight:700}
.atx-app .atx-name::before{content:"\\276F  ";color:var(--att-prompt)}
.atx-app .atx-name span{color:var(--att-comment);opacity:1;font-size:12px}
.atx-app .atx-sub{color:var(--att-comment);opacity:1;font-size:11.5px}
.atx-app .atx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.atx-app .atx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--att-comment);opacity:1}
.atx-app .atx-stat .l::before{content:"# "}
.atx-app .atx-stat b{font-size:14px}
.atx-app .atx-stat.warn{border-color:var(--att-warn);background:transparent}
.atx-app .atx-stat.warn b{color:var(--att-warn)}
.atx-app .atx-stat.bad{border-color:var(--att-bad);background:transparent}
.atx-app .atx-stat.bad b{color:var(--att-bad)}
.atx-app .atx-stat.ok{border-color:var(--att-line)}
.atx-app .atx-stat.ok b{color:var(--att-ok)}
.atx-app.atx-app .atx-trig>.atx-trig-b{border-radius:6px;box-shadow:none}
.atx-app.atx-app .atx-open .atx-trig>.atx-trig-b{box-shadow:none}
.atx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--att-comment);opacity:1}
.atx-app .fxl::before{content:"# "}
.atx-app .fxi,.atx-app .opi{font-family:var(--att-mono);color:var(--att-comment);opacity:1}
.atx-app.atx-app .atx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.atx-app.atx-app .atx-opt>.atx-opt-b,.atx-app.atx-app .atx-row>.atx-row-b{border-radius:5px}
.atx-app.atx-app .atx-opt.atx-on>.atx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--att-accent)}
.atx-app.atx-app .atx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--att-line)}
.atx-app.atx-app button.atx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--att-dim)}
.atx-app.atx-app button.atx-tab:hover:enabled{background:transparent;color:var(--att-accent);opacity:1}
.atx-app.atx-app button.atx-tab.atx-on,.atx-app.atx-app button.atx-tab.atx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--att-accent);color:var(--att-accent);font-weight:700}
.atx-app.atx-app button.atx-small,.atx-app.atx-app button.atx-chip,.atx-app.atx-app button.atx-x{border-radius:6px}
.atx-app.atx-app button.atx-chip.atx-on,.atx-app.atx-app button.atx-chip.atx-on:hover:enabled{background:transparent;border-color:var(--att-accent);font-weight:700}
.atx-app.atx-app .atx-left,.atx-app.atx-app .atx-machines{border-radius:8px}
.atx-app.atx-app .atx-machines{border:1px solid var(--att-line);padding:0 4px 4px 0;overflow:visible}
.atx-app.atx-app .atx-lhead{border-bottom:1px solid var(--att-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.atx-app.atx-app button.atx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--att-comment);opacity:1}
.atx-app.atx-app button.atx-col.atx-c-name::before{content:"# "}
.atx-app.atx-app button.atx-col:hover:enabled{background:transparent;color:var(--att-accent)}
.atx-app.atx-app button.atx-col.atx-on{color:var(--att-accent);font-weight:700}
.atx-app.atx-app .atx-row.atx-on>.atx-row-b,.atx-app.atx-app .atx-row.atx-on>.atx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--att-accent)}
.atx-app .tag,.atx-app .ei{border-radius:4px}
.atx-app .tag{font-weight:400;letter-spacing:0;color:var(--att-comment)}
.atx-app .tag.x{background:rgba(140,79,255,.12);color:""" + _ATHENA_PURPLE + """}
.atx-app .ef,.atx-app .ff,.atx-app .ez,.atx-app .fz{color:var(--att-comment);opacity:1}
.atx-app .atx-pager .widget-html-content,.atx-app .atx-pager .jupyter-widget-html-content{color:var(--att-dim);opacity:1}
.atx-app.atx-app .atx-pager{border-top:0}
.atx-app .atx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--att-comment);opacity:1}
.atx-app .atx-label::before{content:"# "}
.atx-app .ath .card{border-radius:6px}
.atx-app .ath .note,.atx-app .ath .fi{border-radius:3px 6px 6px 3px}
.atx-app .ath pre{border-radius:6px}
.atx-app .atx-status{color:var(--att-dim)}
.atx-app .atx-status .st{opacity:1}
.atx-app .atx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--att-ok)}
.atx-app .atx-status code{border-radius:4px;color:var(--att-s)}
.atx-app .atx-hint{border-radius:6px}
.atx-app .spin{border-top-color:var(--att-accent)}"""

_BASE_CSS = """.atx-app{--tx-accent:#2563eb;--tx-button:#2563eb;--tx-accent-2:#db2777;--tx-soft:rgba(37,99,235,.10);--tx-ring:rgba(37,99,235,.28);--tx-line:rgba(127,127,127,.22);--tx-line-2:rgba(127,127,127,.36);--tx-tint:rgba(127,127,127,.06);--tx-tint-2:rgba(127,127,127,.11);--tx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--tx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--tx-raised:var(--tx-surface);--tx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--tx-ok:#10b981;--tx-warn:#f59e0b;--tx-bad:#ef4444;--tx-ink-bad:#dc2626;--tx-ink-warn:#b45309;--tx-ink-ok:#047857}
body[data-jp-theme-light="false"] .atx-app,body.vscode-dark .atx-app,body.vscode-high-contrast .atx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .atx-app{""" + _EXPLORER_DARK + """}}
.atx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--tx-line);border-radius:18px;padding:14px 16px 10px;background:var(--tx-bg);box-shadow:var(--tx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.atx-app *{box-sizing:border-box}
.atx-app .widget-html-content,.atx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.atx-app.atx-app .atx-flat>*{margin:0}
.atx-app.atx-app button.jupyter-button{color:inherit;background:var(--tx-tint);border:1px solid var(--tx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.atx-app.atx-app button.jupyter-button:hover:enabled{background:var(--tx-tint-2);border-color:var(--tx-line-2);box-shadow:none}
.atx-app.atx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.atx-app.atx-app button.jupyter-button:focus-visible{outline:2px solid var(--tx-ring);outline-offset:-1px}
.atx-app.atx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.atx-app.atx-app button.jupyter-button.mod-primary{background:var(--tx-button);border-color:transparent;color:#fff;font-weight:600}
.atx-app.atx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--tx-button);filter:brightness(1.08)}
.atx-app.atx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.atx-app.atx-app .widget-text input,.atx-app.atx-app .jupyter-widget-text input,.atx-app.atx-app .widget-dropdown>select,.atx-app.atx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--tx-line-2);border-radius:9px;background-color:var(--tx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.atx-app.atx-app .widget-dropdown>select,.atx-app.atx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.atx-app.atx-app .widget-text input:focus,.atx-app.atx-app .jupyter-widget-text input:focus,.atx-app.atx-app .widget-dropdown>select:focus,.atx-app.atx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--tx-accent);box-shadow:0 0 0 3px var(--tx-soft)}
.atx-app.atx-app .widget-text,.atx-app.atx-app .widget-dropdown,.atx-app.atx-app .jupyter-widget-text,.atx-app.atx-app .jupyter-widget-dropdown{margin:0;height:auto}
.atx-app.atx-app .widget-toggle-buttons,.atx-app.atx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--tx-tint-2);gap:2px;flex:0 0 auto;margin:0}
.atx-app.atx-app .widget-toggle-buttons .widget-toggle-button,.atx-app.atx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.atx-app.atx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.atx-app.atx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--tx-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.atx-app.atx-app .widget-checkbox input[type=checkbox],.atx-app.atx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--tx-accent)}
.atx-app.atx-app .atx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--tx-line);overflow:visible}
.atx-app.atx-app .atx-top{align-items:center;gap:12px;overflow:visible}
.atx-app.atx-app .atx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.atx-app .atx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.atx-app .atx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--tx-accent),var(--tx-accent-2));box-shadow:0 2px 8px var(--tx-ring);flex:0 0 auto}
.atx-app .atx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app .atx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.atx-app .atx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app .atx-stats{display:flex;flex-wrap:wrap;gap:8px}
.atx-app .atx-stat{border:1px solid var(--tx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--tx-tint)}
.atx-app .atx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.atx-app .atx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.atx-app .atx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.atx-app .atx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.atx-app .atx-stat.ok{border-color:rgba(16,185,129,.55)}
.atx-app .atx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--tx-tint-2) 30%,var(--tx-line) 50%,var(--tx-tint-2) 70%);animation:atx-glow 1.3s ease-in-out infinite}
.atx-app.atx-app .atx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.atx-app.atx-app .atx-field.atx-open{z-index:41}
.atx-app.atx-app .atx-trig{position:relative;min-height:52px;overflow:visible}
.atx-app.atx-app .atx-trig>.atx-trig-b,.atx-app.atx-app .atx-opt>.atx-opt-b,.atx-app.atx-app .atx-row>.atx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.atx-app.atx-app .atx-trig>.atx-trig-b{border-color:var(--tx-line-2);border-radius:12px;background:var(--tx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.atx-app.atx-app .atx-trig>.atx-trig-b:hover:enabled{border-color:var(--tx-accent);background:var(--tx-surface)}
.atx-app.atx-app .atx-open .atx-trig>.atx-trig-b{border-color:var(--tx-accent);box-shadow:0 0 0 3px var(--tx-soft)}
.atx-app.atx-app .atx-trig>.atx-trig-b:active:enabled,.atx-app.atx-app .atx-opt>.atx-opt-b:active:enabled,.atx-app.atx-app .atx-row>.atx-row-b:active:enabled{transform:none}
.atx-app.atx-app .atx-trig>.atx-face,.atx-app.atx-app .atx-opt>.atx-opt-t,.atx-app.atx-app .atx-row>.atx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.atx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.atx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.atx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.atx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.atx-app .fxi,.atx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.atx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.atx-app .atx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--tx-accent)}
.atx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.atx-app .dot.ok{background:var(--tx-ok)}
.atx-app .dot.warn{background:var(--tx-warn)}
.atx-app .dot.bad{background:var(--tx-bad)}
.atx-app.atx-app .atx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--tx-line-2);border-radius:14px;background:var(--tx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.atx-app.atx-app .atx-pop>*{margin:0}
.atx-app.atx-app .atx-pop .atx-x{margin-left:6px}
.atx-app.atx-app .atx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.atx-app.atx-app .atx-opts>*{flex:0 0 auto}
.atx-app.atx-app .atx-opt{position:relative;margin:0 0 2px;overflow:visible}
.atx-app.atx-app .atx-opt>.atx-opt-b{border-radius:10px}
.atx-app.atx-app .atx-opt>.atx-opt-b:hover:enabled{background:var(--tx-tint-2)}
.atx-app.atx-app .atx-opt.atx-on>.atx-opt-b{background:var(--tx-soft);border-color:var(--tx-ring)}
.atx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.atx-app .op .dot{margin-top:6px}
.atx-app .opb{flex:1 1 auto;min-width:0}
.atx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.atx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.atx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--tx-line);line-height:1.4}
.atx-app .opf.warn{opacity:1;color:var(--tx-ink-warn)}
.atx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.atx-app.atx-app .atx-backdrop,.atx-app.atx-app .atx-backdrop:hover:enabled,.atx-app.atx-app .atx-backdrop:active:enabled,.atx-app.atx-app .atx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.atx-app.atx-app .atx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--tx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.atx-app.atx-app button.atx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.atx-app.atx-app button.atx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--tx-icon) center/contain no-repeat;mask:var(--tx-icon) center/contain no-repeat}
.atx-app.atx-app button.atx-tab:hover:enabled{background:var(--tx-tint);opacity:.95}
.atx-app.atx-app button.atx-tab.atx-on,.atx-app.atx-app button.atx-tab.atx-on:hover:enabled{background:var(--tx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.atx-app.atx-app button.atx-tab.atx-on::before{background:var(--tx-accent)}
.atx-app.atx-app button.atx-tab.atx-alert::after,.atx-app.atx-app button.atx-tab.atx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--tx-warn);flex:0 0 auto}
.atx-app.atx-app button.atx-tab.atx-alarm::after{background:var(--tx-bad)}
@container (max-width:520px){.atx-app.atx-app button.atx-tab::before{display:none}}
.atx-app.atx-app .atx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .atx-app.atx-app .atx-page{height:620px}
.atx-app.atx-app .atx-page>*{flex:0 0 auto}
.atx-app.atx-app .atx-page.atx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.atx-app.atx-app .atx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--tx-line);border-radius:14px;padding:10px 10px 6px;background:var(--tx-surface);overflow:hidden}
.atx-app.atx-app .atx-left>*{margin:0;flex:0 0 auto}
.atx-app.atx-app .atx-left,.atx-app.atx-app .atx-right,.atx-app.atx-app .atx-rows{min-height:0}
@media (max-width:900px){.atx-app.atx-app .atx-page.atx-split{flex-wrap:wrap;overflow:hidden auto}.atx-app.atx-app .atx-left{flex:1 1 100%;max-width:100%;height:440px}.atx-app.atx-app .atx-right{flex:1 1 100%;overflow:visible}}
.atx-app.atx-app .atx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.atx-app.atx-app .atx-right>*{flex:0 0 auto;margin:0}
.atx-app.atx-app .atx-find{position:relative;align-items:center}
.atx-app.atx-app .atx-find input,.atx-app.atx-app .atx-find input:focus{padding-left:32px;background:var(--tx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.atx-app.atx-app .atx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.atx-app.atx-app .atx-chips>*{margin:0}
.atx-app.atx-app button.atx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--tx-line);display:inline-flex;align-items:center;gap:6px}
.atx-app.atx-app button.atx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.atx-app.atx-app button.atx-chip.atx-t-all::before{display:none}
.atx-app.atx-app button.atx-chip.atx-t-bad::before{background:var(--tx-bad)}
.atx-app.atx-app button.atx-chip.atx-t-warn::before{background:var(--tx-warn)}
.atx-app.atx-app button.atx-chip.atx-t-ok::before{background:var(--tx-ok)}
.atx-app.atx-app button.atx-chip.atx-on,.atx-app.atx-app button.atx-chip.atx-on:hover:enabled{background:var(--tx-soft);border-color:var(--tx-ring);color:var(--tx-accent);font-weight:650}
.atx-app.atx-app .atx-filters{gap:6px;margin:8px 0 0;align-items:center}
.atx-app.atx-app .atx-filters>*{margin:0}
.atx-app.atx-app .atx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.atx-app.atx-app .atx-rows>*{flex:0 0 auto}
.atx-app.atx-app .atx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.atx-app.atx-app .atx-row>.atx-row-b{border-radius:10px}
.atx-app.atx-app .atx-row>.atx-row-b:hover:enabled{background:var(--tx-tint-2)}
.atx-app.atx-app .atx-row.atx-on>.atx-row-b,.atx-app.atx-app .atx-row.atx-on>.atx-row-b:hover:enabled{background:var(--tx-soft);border-color:var(--tx-ring)}
.atx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.atx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.atx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.atx-app .sl{font-weight:600}
.atx-app .sl.bad{color:var(--tx-ink-bad)}
.atx-app .sl.warn{color:var(--tx-ink-warn)}
.atx-app .sl.ok{color:var(--tx-ink-ok)}
.atx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.atx-app .atx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.atx-app.atx-app .atx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--tx-line);margin-top:4px}
.atx-app.atx-app .atx-pager>*{margin:0}
.atx-app.atx-app button.atx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.atx-app .atx-pager .widget-html-content,.atx-app .atx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app.atx-app .atx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--tx-bg);border-bottom:1px solid var(--tx-line);flex-wrap:wrap}
.atx-app.atx-app .atx-bar>*{margin:0}
.atx-app.atx-app button.atx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.atx-app.atx-app button.atx-ghost{background:transparent;border-color:transparent}
.atx-app.atx-app button.atx-ghost:hover:enabled{background:var(--tx-tint-2)}
.atx-app.atx-app button.atx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.atx-app.atx-app button.atx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--tx-ink-bad)}
.atx-app .atx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.atx-app .atx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.atx-app .atx-status .st{opacity:.72}
.atx-app .atx-status .st.warn{opacity:1;color:var(--tx-ink-warn)}
.atx-app .atx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.atx-app .atx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--tx-ok)}
.atx-app .atx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--tx-tint-2);user-select:all;-webkit-user-select:all}
.atx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--tx-accent);border-radius:50%;animation:atx-spin .8s linear infinite}
@keyframes atx-spin{to{transform:rotate(360deg)}}
.atx-app .skw{padding:6px 2px}
.atx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.atx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--tx-tint) 30%,var(--tx-tint-2) 50%,var(--tx-tint) 70%);animation:atx-glow 1.3s ease-in-out infinite}
.atx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.atx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.atx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes atx-glow{from{background-position:100% 0}to{background-position:0 0}}
.atx-app .atx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--tx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.atx-app .atx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.atx-app *,.atx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.atx-app.atx-app .atx-row{height:52px}
.atx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.atx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--tx-tint-2)}
.atx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--tx-ink-ok)}
.atx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--tx-ink-bad)}
.atx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--tx-ink-warn)}
.atx-app .ei.run{background:rgba(59,130,246,.15);color:var(--tx-accent)}
.atx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.atx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.atx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.atx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--tx-tint-2);margin-left:6px;vertical-align:1px}
.atx-app .tag.x{background:rgba(219,39,119,.12);color:var(--tx-accent-2)}
.atx-app.atx-app .atx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--tx-line)}
.atx-app.atx-app .atx-lhead>*{margin:0;min-width:0}
.atx-app.atx-app button.atx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.atx-app.atx-app button.atx-col.atx-c-name{text-align:left;justify-content:flex-start}
.atx-app.atx-app button.atx-col:hover:enabled{background:transparent;opacity:1;color:var(--tx-accent)}
.atx-app.atx-app button.atx-col.atx-on{opacity:1;color:var(--tx-accent)}
.atx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.atx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.atx-app .mr .num.bad{color:var(--tx-ink-bad);font-weight:650}
.atx-app .mr .num.warn{color:var(--tx-ink-warn);font-weight:650}
.atx-app.atx-app .atx-machines{overflow:hidden auto}
.atx-app.atx-app .atx-machines>*{flex:0 0 auto}
.atx-app .ath h3 .badge{display:none}
.atx-app .ath h3{font-size:17px;margin:6px 0 2px}
.atx-app .ath .card{border-radius:12px;background:var(--tx-tint);border-color:var(--tx-line)}
.atx-app .ath .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.atx-app .ath .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.atx-app .ath .card.ok{border-color:rgba(16,185,129,.55)}
.atx-app .ath .note,.atx-app .ath .fi{border-radius:4px 10px 10px 4px}
.atx-app .ath pre{border-radius:10px}
"""

_OWN_CSS = """
.atx-app .fr .fn .tag{margin-left:6px}
.atx-app .fz b{display:block;font-weight:600;opacity:1}
.atx-app .fz.warn b{color:var(--tx-ink-warn)}
.atx-app .fz.bad b{color:var(--tx-ink-bad)}
.atx-app .fr .fn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.atx-app.atx-app .atx-search{gap:8px;align-items:center;flex-wrap:wrap;padding:0 0 10px;margin:0 0 6px;border-bottom:1px solid var(--tx-line)}
.atx-app.atx-app .atx-search>*{margin:0}
.atx-app.atx-app .atx-search .atx-find{flex:1 1 260px}
"""

_EXPLORER_CSS = "<style>\n" + _BASE_CSS + _OWN_CSS + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "overview": "the Overview tab",
    "queries": "the Queries tab",
    "query": "the Queries tab",
    "expensive": "the Costs tab",
    "failures": "the Failures tab",
    "catalog": "the Catalog tab",
    "table_info": "the Catalog tab",
    "crawlers": "the Crawlers tab",
}

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he \w+ tab)")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (query('a1b2...') -> "the Queries tab", table_info('sales.events') -> "the Catalog tab"), capitalised where it
    starts a sentence. query_code(...), AWS CLI commands and SQL stay as they are.
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


def _query_tone(query: Query) -> str:
    return STATE_TONES.get(query.state, "")


def _query_face(query: Query, *, cost: float, words: list[str]) -> str:
    """
    A query's line in the Queries tab: a dot for how it ended, its SQL, when and where it ran and how long it took,
    and what it cost and scanned.
    """
    took = _duration(query.seconds) if query.total_ms else "-"
    where = " · ".join(filter(None, [_fmt_dt(query.submitted), query.workgroup, query.database, took]))
    state = "" if query.state == "SUCCEEDED" else f" · {query.state.lower()}"
    return _line(_query_tone(query), _marked(one_line(query.sql, _SQL_CHARS), words), _esc(where + state),
                 _side(_esc(human_money(cost)), _esc(human_size(query.scanned_bytes))))


def _shape_face(shape: QueryShape, *, share: float, words: list[str]) -> str:
    """
    A kind of query's line in the Costs tab: its newest run's SQL, how often it ran and when last, and what its runs
    cost together and their share of the bill.
    """
    runs = f"{shape.runs:,} run{'' if shape.runs == 1 else 's'}"
    note = " · ".join(filter(None, [runs, f"last {human_age(shape.last)}", ", ".join(sorted(shape.workgroups)),
                                    "SELECT *" if shape.select_star else ""]))
    tone = "warn" if shape.select_star or shape.runs > 1 and share >= 0.25 else "ok"
    return _line(tone, _marked(one_line(shape.sql, _SQL_CHARS), words), _esc(note),
                 _side(_esc(human_money(shape.cost)), _esc(f"{share:.0%} of the bill")))


def _failure_face(group: FailureGroup, *, words: list[str]) -> str:
    """
    A kind of failure's line in the Failures tab: its kind, what it means, and how many failed and when last.
    """
    return _line("bad", _marked(group.kind, words), _marked(_clip(group.meaning, _NOTE_CHARS), words),
                 _side(_esc(f"{group.count:,} failed"), _esc(f"newest {human_age(group.last)}")))


def _table_tone(table: Table, findings: list[tuple[str, str]]) -> str:
    if table.location is not None and table.location.state == "no bucket":
        return "bad"
    return "warn" if any(level == "warn" for level, _ in findings) else "ok"


def _table_note(table: Table) -> str:
    if table.is_view:
        return "a view"
    keys = ", ".join(k.name for k in table.partition_keys)
    parts = [f"partitioned by {keys}" + (" (projected)" if table.projection else "") if keys else "no partitions"]
    if table.location is not None and table.location.state != "ok":
        parts.append(f"folder {table.location.state}")
    parts.append(table.location_uri)
    return " · ".join(filter(None, parts))


def _table_face(table: Table, *, findings: list[tuple[str, str]], words: list[str]) -> str:
    """
    A table's line in the Catalog tab: a dot for what's wrong with it, its name and format, its partitions and where
    its files are, and how many warnings it has.
    """
    warnings = sum(1 for level, _ in findings if level == "warn")
    tag = f'<span class="tag">{_esc(table.format or ("VIEW" if table.is_view else "?"))}</span>'
    flag = f"{warnings:,} warning{'' if warnings == 1 else 's'}" if warnings else "fine"
    return _line(_table_tone(table, findings), _marked(table.full_name, words) + tag,
                 _marked(_clip(_table_note(table), _NOTE_CHARS), words),
                 _side(_esc(flag), _esc(f"updated {human_age(table.updated)}"), "warn" if warnings else ""))


def _schedule_words(crawler: Crawler) -> str:
    if not crawler.schedule:
        return "on demand"
    period = schedule_period(crawler.schedule)
    words = {1: "hourly", 24: "daily", 168: "weekly", 744: "monthly"}
    word = words.get(int(period.total_seconds() // 3600), crawler.schedule) if period else crawler.schedule
    return f"{word}, paused" if crawler.schedule_state == "PAUSED" else word


def _crawler_face(crawler: Crawler, *, level: str, words: list[str]) -> str:
    """
    A crawler's line in the Crawlers tab: a dot for what's wrong with it, its name, how often it runs, into which
    database and from where, and how its last crawl went and when.
    """
    status = "running" if crawler.state == "RUNNING" else (crawler.last_status or "never ran").lower()
    tone = "bad" if crawler.last_status == "FAILED" else level or "ok"
    note = " · ".join(filter(None, [_schedule_words(crawler), crawler.database, ", ".join(crawler.targets)]))
    side_tone = "bad" if crawler.last_status == "FAILED" else ""
    return _line(tone, _marked(crawler.name, words), _marked(_clip(note, _NOTE_CHARS), words),
                 _side(_esc(status), _esc(human_age(crawler.last_start)), side_tone))


def _stats_html(cards: list[tuple[str, str, str]]) -> str:
    """
    The cards beside the title: (label, value, tone) each.
    """
    html: list[Any] = [f'<div class="atx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b></div>'
                       for label, value, tone in cards]
    return f'<div class="atx-stats">{"".join(html)}</div>'
