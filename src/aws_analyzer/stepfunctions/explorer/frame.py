"""
What the explorer window is drawn with: its tabs, stylesheet and colours, the sentences from reports reworded for the
window, and the faces of its lines (a state machine, an execution).
"""

from __future__ import annotations

import re

from ..._kit.fmt import _duration, _fmt_dt, human_age, human_money
from ..._kit.render import _CALL_RE
from ..._kit.text import _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..helpers import STATUS_TONES, STATUS_WORDS
from ..models import Execution, MachineNumbers, StateMachine


_EXPLORER_TABS = (
    ("machines", "State machines", "Every state machine in the region and how it's doing: click one to open it",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("overview", "Overview", "How the state machine is doing, what its definition risks, and every state",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("executions", "Executions", "Its executions: click one for its waterfall and one log of what happened",
     "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/><path d='M7 9h6M7 12.5h10M7 16h4'/>"),
    ("graph", "Graph", "The state machine drawn as a graph, coloured by an execution's path or by where executions "
     "stop: click a state to see what happened there",
     "<rect x='8.5' y='3' width='7' height='5' rx='1.3'/><rect x='3' y='16' width='7' height='5' rx='1.3'/>"
     "<rect x='14' y='16' width='7' height='5' rx='1.3'/><path d='M12 8v4M6.5 16v-2h11v2'/>"),
    ("failures", "Failures", "Where and why its executions fail, the most common first",
     "<path d='M12 4.2 20.8 19.5H3.2z'/><path d='M12 10v4.2M12 17h.01'/>"),
    ("definition", "Definition", "How it flows: each state, what it does and where it goes next, and a Mermaid "
     "flowchart", "<circle cx='6' cy='6' r='2.5'/><circle cx='6' cy='18' r='2.5'/><circle cx='18' cy='12' r='2.5'/>"
                  "<path d='M6 8.5v7M8.4 7.2l7.2 3.6M8.4 16.8l7.2-3.6'/>"),
)

_MACHINE_TABS = ("overview", "executions", "graph", "failures", "definition")

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3.5" width="6" height="5" rx="1.4"/>'
         '<rect x="14.5" y="9.5" width="6" height="5" rx="1.4"/><rect x="3.5" y="15.5" width="6" height="5" '
         'rx="1.4"/><path d="M6.5 8.5v7M9.5 6h3a2 2 0 0 1 2 2v1.5M14.5 12h-2a2 2 0 0 0-2 2v4"/></svg>')

_EXECUTION_PAGE = 40

_STATUS_ICONS = {"SUCCEEDED": "✓", "FAILED": "✕", "TIMED_OUT": "⏱", "ABORTED": "⊘", "RUNNING": "•",
                 "PENDING_REDRIVE": "↻"}

_STATUS_CHIPS = (("", "All", "all"), ("FAILED", "Failed", "bad"), ("TIMED_OUT", "Timed out", "bad"),
                 ("ABORTED", "Aborted", "warn"), ("RUNNING", "Running", ""), ("SUCCEEDED", "Succeeded", "ok"))

_SINCE_CHOICES = (("Last 24 hours", "24h"), ("Last 7 days", "7d"), ("Last 30 days", "30d"),
                  ("Last 90 days", "90d"))


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .sfx-i-<key> sets --sx-icon, which a tab's ::before draws.
    """
    return "\n".join(f".sfx-app .sfx-i-{key}{{--sx-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--sx-accent:#60a5fa;--sx-accent-2:#f472b6;--sx-soft:rgba(96,165,250,.15);--sx-ring:rgba(96,165,250,"
                  ".38);--sx-raised:rgba(255,255,255,.11);--sx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--sx-ink-bad:#f87171;--sx-ink-warn:#fbbf24;--sx-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--sx-accent:var(--sft-accent);--sx-button:var(--sft-accent);--sx-accent-2:var(--sft-prompt);"
                    "--sx-soft:var(--sft-hover);--sx-ring:var(--sft-accent);--sx-line:var(--sft-line);"
                    "--sx-line-2:var(--sft-line);--sx-tint:transparent;--sx-tint-2:var(--sft-hover);--sx-bg:var(--sft-bg);"
                    "--sx-surface:transparent;--sx-raised:var(--sft-hover);--sx-shadow:none;--sx-ok:var(--sft-ok);"
                    "--sx-warn:var(--sft-warn);--sx-bad:var(--sft-bad);--sx-ink-ok:var(--sft-ok);"
                    "--sx-ink-warn:var(--sft-warn);--sx-ink-bad:var(--sft-bad)")

_STEP_FUNCTIONS_PINK = "#e7157b"

_TERMINAL_CSS = _terminal_theme((".sfx-app",), "sft") + """
.sfx-app.sfx-app.sfx-app.sfx-app{""" + _TERMINAL_TOKENS + """}
.sfx-app.sfx-app{font-family:var(--sft-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.sfx-app.sfx-app .widget-html-content,.sfx-app.sfx-app .widget-label,.sfx-app.sfx-app input,.sfx-app.sfx-app select{font-family:var(--sft-mono)}
.sfx-app.sfx-app button.jupyter-button{font-family:var(--sft-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.sfx-app.sfx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--sft-accent);color:var(--sft-accent)}
.sfx-app.sfx-app button.jupyter-button.mod-primary,.sfx-app.sfx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--sft-accent);color:var(--sft-accent);font-weight:700;filter:none}
.sfx-app.sfx-app .widget-text input,.sfx-app.sfx-app .jupyter-widget-text input,.sfx-app.sfx-app .widget-dropdown>select,.sfx-app.sfx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.sfx-app.sfx-app .widget-text input:focus,.sfx-app.sfx-app .jupyter-widget-text input:focus,.sfx-app.sfx-app .widget-dropdown>select:focus,.sfx-app.sfx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.sfx-app.sfx-app .widget-toggle-buttons,.sfx-app.sfx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.sfx-app.sfx-app .widget-toggle-buttons .widget-toggle-button,.sfx-app.sfx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--sft-line);border-radius:6px;opacity:1;color:var(--sft-dim)}
.sfx-app.sfx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.sfx-app.sfx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--sft-accent);color:var(--sft-accent);font-weight:700;box-shadow:none}
.sfx-app .sfx-logo{width:30px;height:30px;border-radius:7px;color:""" + _STEP_FUNCTIONS_PINK + """;background:transparent;box-shadow:inset 0 0 0 1px var(--sft-line)}
.sfx-app .sfx-name{font-size:14px;font-weight:700}
.sfx-app .sfx-name::before{content:"\\276F  ";color:var(--sft-prompt)}
.sfx-app .sfx-name span{color:var(--sft-comment);opacity:1;font-size:12px}
.sfx-app .sfx-sub{color:var(--sft-comment);opacity:1;font-size:11.5px}
.sfx-app .sfx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.sfx-app .sfx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--sft-comment);opacity:1}
.sfx-app .sfx-stat .l::before{content:"# "}
.sfx-app .sfx-stat b{font-size:14px}
.sfx-app .sfx-stat.warn{border-color:var(--sft-warn);background:transparent}
.sfx-app .sfx-stat.warn b{color:var(--sft-warn)}
.sfx-app .sfx-stat.bad{border-color:var(--sft-bad);background:transparent}
.sfx-app .sfx-stat.bad b{color:var(--sft-bad)}
.sfx-app .sfx-stat.ok{border-color:var(--sft-line)}
.sfx-app .sfx-stat.ok b{color:var(--sft-ok)}
.sfx-app.sfx-app .sfx-trig>.sfx-trig-b{border-radius:6px;box-shadow:none}
.sfx-app.sfx-app .sfx-open .sfx-trig>.sfx-trig-b{box-shadow:none}
.sfx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--sft-comment);opacity:1}
.sfx-app .fxl::before{content:"# "}
.sfx-app .fxi,.sfx-app .opi{font-family:var(--sft-mono);color:var(--sft-comment);opacity:1}
.sfx-app.sfx-app .sfx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.sfx-app.sfx-app .sfx-opt>.sfx-opt-b,.sfx-app.sfx-app .sfx-row>.sfx-row-b{border-radius:5px}
.sfx-app.sfx-app .sfx-opt.sfx-on>.sfx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--sft-accent)}
.sfx-app.sfx-app .sfx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--sft-line)}
.sfx-app.sfx-app button.sfx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--sft-dim)}
.sfx-app.sfx-app button.sfx-tab:hover:enabled{background:transparent;color:var(--sft-accent);opacity:1}
.sfx-app.sfx-app button.sfx-tab.sfx-on,.sfx-app.sfx-app button.sfx-tab.sfx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--sft-accent);color:var(--sft-accent);font-weight:700}
.sfx-app.sfx-app button.sfx-small,.sfx-app.sfx-app button.sfx-chip,.sfx-app.sfx-app button.sfx-x{border-radius:6px}
.sfx-app.sfx-app button.sfx-chip.sfx-on,.sfx-app.sfx-app button.sfx-chip.sfx-on:hover:enabled{background:transparent;border-color:var(--sft-accent);font-weight:700}
.sfx-app.sfx-app .sfx-left,.sfx-app.sfx-app .sfx-machines{border-radius:8px}
.sfx-app.sfx-app .sfx-machines{border:1px solid var(--sft-line);padding:0 4px 4px 0;overflow:visible}
.sfx-app.sfx-app .sfx-lhead{border-bottom:1px solid var(--sft-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.sfx-app.sfx-app button.sfx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--sft-comment);opacity:1}
.sfx-app.sfx-app button.sfx-col.sfx-c-name::before{content:"# "}
.sfx-app.sfx-app button.sfx-col:hover:enabled{background:transparent;color:var(--sft-accent)}
.sfx-app.sfx-app button.sfx-col.sfx-on{color:var(--sft-accent);font-weight:700}
.sfx-app.sfx-app .sfx-row.sfx-on>.sfx-row-b,.sfx-app.sfx-app .sfx-row.sfx-on>.sfx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--sft-accent)}
.sfx-app .tag,.sfx-app .ei{border-radius:4px}
.sfx-app .tag{font-weight:400;letter-spacing:0;color:var(--sft-comment)}
.sfx-app .tag.x{background:rgba(231,21,123,.1);color:""" + _STEP_FUNCTIONS_PINK + """}
.sfx-app .ef,.sfx-app .ff,.sfx-app .ez,.sfx-app .fz{color:var(--sft-comment);opacity:1}
.sfx-app .sfx-pager .widget-html-content,.sfx-app .sfx-pager .jupyter-widget-html-content{color:var(--sft-dim);opacity:1}
.sfx-app.sfx-app .sfx-pager{border-top:0}
.sfx-app .sfx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--sft-comment);opacity:1}
.sfx-app .sfx-label::before{content:"# "}
.sfx-app .sfn .card{border-radius:6px}
.sfx-app .sfn .note,.sfx-app .sfn .fi{border-radius:3px 6px 6px 3px}
.sfx-app .sfn pre{border-radius:6px}
.sfx-app .sfx-status{color:var(--sft-dim)}
.sfx-app .sfx-status .st{opacity:1}
.sfx-app .sfx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--sft-ok)}
.sfx-app .sfx-status code{border-radius:4px;color:var(--sft-s)}
.sfx-app .sfx-hint{border-radius:6px}
.sfx-app .spin{border-top-color:var(--sft-accent)}"""

_EXPLORER_CSS = """<style>
.sfx-app{--sx-accent:#2563eb;--sx-button:#2563eb;--sx-accent-2:#db2777;--sx-soft:rgba(37,99,235,.10);--sx-ring:rgba(37,99,235,.28);--sx-line:rgba(127,127,127,.22);--sx-line-2:rgba(127,127,127,.36);--sx-tint:rgba(127,127,127,.06);--sx-tint-2:rgba(127,127,127,.11);--sx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--sx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--sx-raised:var(--sx-surface);--sx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--sx-ok:#10b981;--sx-warn:#f59e0b;--sx-bad:#ef4444;--sx-ink-bad:#dc2626;--sx-ink-warn:#b45309;--sx-ink-ok:#047857}
body[data-jp-theme-light="false"] .sfx-app,body.vscode-dark .sfx-app,body.vscode-high-contrast .sfx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .sfx-app{""" + _EXPLORER_DARK + """}}
.sfx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--sx-line);border-radius:18px;padding:14px 16px 10px;background:var(--sx-bg);box-shadow:var(--sx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.sfx-app *{box-sizing:border-box}
.sfx-app .widget-html-content,.sfx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.sfx-app.sfx-app .sfx-flat>*{margin:0}
.sfx-app.sfx-app button.jupyter-button{color:inherit;background:var(--sx-tint);border:1px solid var(--sx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.sfx-app.sfx-app button.jupyter-button:hover:enabled{background:var(--sx-tint-2);border-color:var(--sx-line-2);box-shadow:none}
.sfx-app.sfx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.sfx-app.sfx-app button.jupyter-button:focus-visible{outline:2px solid var(--sx-ring);outline-offset:-1px}
.sfx-app.sfx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.sfx-app.sfx-app button.jupyter-button.mod-primary{background:var(--sx-button);border-color:transparent;color:#fff;font-weight:600}
.sfx-app.sfx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--sx-button);filter:brightness(1.08)}
.sfx-app.sfx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.sfx-app.sfx-app .widget-text input,.sfx-app.sfx-app .jupyter-widget-text input,.sfx-app.sfx-app .widget-dropdown>select,.sfx-app.sfx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--sx-line-2);border-radius:9px;background-color:var(--sx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.sfx-app.sfx-app .widget-dropdown>select,.sfx-app.sfx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.sfx-app.sfx-app .widget-text input:focus,.sfx-app.sfx-app .jupyter-widget-text input:focus,.sfx-app.sfx-app .widget-dropdown>select:focus,.sfx-app.sfx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--sx-accent);box-shadow:0 0 0 3px var(--sx-soft)}
.sfx-app.sfx-app .widget-text,.sfx-app.sfx-app .widget-dropdown,.sfx-app.sfx-app .jupyter-widget-text,.sfx-app.sfx-app .jupyter-widget-dropdown{margin:0;height:auto}
.sfx-app.sfx-app .widget-toggle-buttons,.sfx-app.sfx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--sx-tint-2);gap:2px;flex:0 0 auto;margin:0}
.sfx-app.sfx-app .widget-toggle-buttons .widget-toggle-button,.sfx-app.sfx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.sfx-app.sfx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.sfx-app.sfx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--sx-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.sfx-app.sfx-app .widget-checkbox input[type=checkbox],.sfx-app.sfx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--sx-accent)}
.sfx-app.sfx-app .sfx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--sx-line);overflow:visible}
.sfx-app.sfx-app .sfx-top{align-items:center;gap:12px;overflow:visible}
.sfx-app.sfx-app .sfx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.sfx-app .sfx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.sfx-app .sfx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--sx-accent),var(--sx-accent-2));box-shadow:0 2px 8px var(--sx-ring);flex:0 0 auto}
.sfx-app .sfx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app .sfx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.sfx-app .sfx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app .sfx-stats{display:flex;flex-wrap:wrap;gap:8px}
.sfx-app .sfx-stat{border:1px solid var(--sx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--sx-tint)}
.sfx-app .sfx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.sfx-app .sfx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.sfx-app .sfx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.sfx-app .sfx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.sfx-app .sfx-stat.ok{border-color:rgba(16,185,129,.55)}
.sfx-app .sfx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--sx-tint-2) 30%,var(--sx-line) 50%,var(--sx-tint-2) 70%);animation:sfx-glow 1.3s ease-in-out infinite}
.sfx-app.sfx-app .sfx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.sfx-app.sfx-app .sfx-field.sfx-open{z-index:41}
.sfx-app.sfx-app .sfx-trig{position:relative;min-height:52px;overflow:visible}
.sfx-app.sfx-app .sfx-trig>.sfx-trig-b,.sfx-app.sfx-app .sfx-opt>.sfx-opt-b,.sfx-app.sfx-app .sfx-row>.sfx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.sfx-app.sfx-app .sfx-trig>.sfx-trig-b{border-color:var(--sx-line-2);border-radius:12px;background:var(--sx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.sfx-app.sfx-app .sfx-trig>.sfx-trig-b:hover:enabled{border-color:var(--sx-accent);background:var(--sx-surface)}
.sfx-app.sfx-app .sfx-open .sfx-trig>.sfx-trig-b{border-color:var(--sx-accent);box-shadow:0 0 0 3px var(--sx-soft)}
.sfx-app.sfx-app .sfx-trig>.sfx-trig-b:active:enabled,.sfx-app.sfx-app .sfx-opt>.sfx-opt-b:active:enabled,.sfx-app.sfx-app .sfx-row>.sfx-row-b:active:enabled{transform:none}
.sfx-app.sfx-app .sfx-trig>.sfx-face,.sfx-app.sfx-app .sfx-opt>.sfx-opt-t,.sfx-app.sfx-app .sfx-row>.sfx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.sfx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.sfx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.sfx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.sfx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.sfx-app .fxi,.sfx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.sfx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.sfx-app .sfx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--sx-accent)}
.sfx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.sfx-app .dot.ok{background:var(--sx-ok)}
.sfx-app .dot.warn{background:var(--sx-warn)}
.sfx-app .dot.bad{background:var(--sx-bad)}
.sfx-app.sfx-app .sfx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--sx-line-2);border-radius:14px;background:var(--sx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.sfx-app.sfx-app .sfx-pop>*{margin:0}
.sfx-app.sfx-app .sfx-pop .sfx-x{margin-left:6px}
.sfx-app.sfx-app .sfx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.sfx-app.sfx-app .sfx-opts>*{flex:0 0 auto}
.sfx-app.sfx-app .sfx-opt{position:relative;margin:0 0 2px;overflow:visible}
.sfx-app.sfx-app .sfx-opt>.sfx-opt-b{border-radius:10px}
.sfx-app.sfx-app .sfx-opt>.sfx-opt-b:hover:enabled{background:var(--sx-tint-2)}
.sfx-app.sfx-app .sfx-opt.sfx-on>.sfx-opt-b{background:var(--sx-soft);border-color:var(--sx-ring)}
.sfx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.sfx-app .op .dot{margin-top:6px}
.sfx-app .opb{flex:1 1 auto;min-width:0}
.sfx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.sfx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.sfx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--sx-line);line-height:1.4}
.sfx-app .opf.warn{opacity:1;color:var(--sx-ink-warn)}
.sfx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.sfx-app.sfx-app .sfx-backdrop,.sfx-app.sfx-app .sfx-backdrop:hover:enabled,.sfx-app.sfx-app .sfx-backdrop:active:enabled,.sfx-app.sfx-app .sfx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.sfx-app.sfx-app .sfx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--sx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.sfx-app.sfx-app button.sfx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.sfx-app.sfx-app button.sfx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--sx-icon) center/contain no-repeat;mask:var(--sx-icon) center/contain no-repeat}
.sfx-app.sfx-app button.sfx-tab:hover:enabled{background:var(--sx-tint);opacity:.95}
.sfx-app.sfx-app button.sfx-tab.sfx-on,.sfx-app.sfx-app button.sfx-tab.sfx-on:hover:enabled{background:var(--sx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.sfx-app.sfx-app button.sfx-tab.sfx-on::before{background:var(--sx-accent)}
.sfx-app.sfx-app button.sfx-tab.sfx-alert::after,.sfx-app.sfx-app button.sfx-tab.sfx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--sx-warn);flex:0 0 auto}
.sfx-app.sfx-app button.sfx-tab.sfx-alarm::after{background:var(--sx-bad)}
@container (max-width:520px){.sfx-app.sfx-app button.sfx-tab::before{display:none}}
.sfx-app.sfx-app .sfx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .sfx-app.sfx-app .sfx-page{height:620px}
.sfx-app.sfx-app .sfx-page>*{flex:0 0 auto}
.sfx-app.sfx-app .sfx-page.sfx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.sfx-app.sfx-app .sfx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--sx-line);border-radius:14px;padding:10px 10px 6px;background:var(--sx-surface);overflow:hidden}
.sfx-app.sfx-app .sfx-left>*{margin:0;flex:0 0 auto}
.sfx-app.sfx-app .sfx-left,.sfx-app.sfx-app .sfx-right,.sfx-app.sfx-app .sfx-rows{min-height:0}
@media (max-width:900px){.sfx-app.sfx-app .sfx-page.sfx-split{flex-wrap:wrap;overflow:hidden auto}.sfx-app.sfx-app .sfx-left{flex:1 1 100%;max-width:100%;height:440px}.sfx-app.sfx-app .sfx-right{flex:1 1 100%;overflow:visible}}
.sfx-app.sfx-app .sfx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.sfx-app.sfx-app .sfx-right>*{flex:0 0 auto;margin:0}
.sfx-app.sfx-app .sfx-find{position:relative;align-items:center}
.sfx-app.sfx-app .sfx-find input,.sfx-app.sfx-app .sfx-find input:focus{padding-left:32px;background:var(--sx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.sfx-app.sfx-app .sfx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.sfx-app.sfx-app .sfx-chips>*{margin:0}
.sfx-app.sfx-app button.sfx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--sx-line);display:inline-flex;align-items:center;gap:6px}
.sfx-app.sfx-app button.sfx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.sfx-app.sfx-app button.sfx-chip.sfx-t-all::before{display:none}
.sfx-app.sfx-app button.sfx-chip.sfx-t-bad::before{background:var(--sx-bad)}
.sfx-app.sfx-app button.sfx-chip.sfx-t-warn::before{background:var(--sx-warn)}
.sfx-app.sfx-app button.sfx-chip.sfx-t-ok::before{background:var(--sx-ok)}
.sfx-app.sfx-app button.sfx-chip.sfx-on,.sfx-app.sfx-app button.sfx-chip.sfx-on:hover:enabled{background:var(--sx-soft);border-color:var(--sx-ring);color:var(--sx-accent);font-weight:650}
.sfx-app.sfx-app .sfx-filters{gap:6px;margin:8px 0 0;align-items:center}
.sfx-app.sfx-app .sfx-filters>*{margin:0}
.sfx-app.sfx-app .sfx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.sfx-app.sfx-app .sfx-rows>*{flex:0 0 auto}
.sfx-app.sfx-app .sfx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.sfx-app.sfx-app .sfx-row>.sfx-row-b{border-radius:10px}
.sfx-app.sfx-app .sfx-row>.sfx-row-b:hover:enabled{background:var(--sx-tint-2)}
.sfx-app.sfx-app .sfx-row.sfx-on>.sfx-row-b,.sfx-app.sfx-app .sfx-row.sfx-on>.sfx-row-b:hover:enabled{background:var(--sx-soft);border-color:var(--sx-ring)}
.sfx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.sfx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.sfx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.sfx-app .sl{font-weight:600}
.sfx-app .sl.bad{color:var(--sx-ink-bad)}
.sfx-app .sl.warn{color:var(--sx-ink-warn)}
.sfx-app .sl.ok{color:var(--sx-ink-ok)}
.sfx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.sfx-app .sfx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.sfx-app.sfx-app .sfx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--sx-line);margin-top:4px}
.sfx-app.sfx-app .sfx-pager>*{margin:0}
.sfx-app.sfx-app button.sfx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.sfx-app .sfx-pager .widget-html-content,.sfx-app .sfx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app.sfx-app .sfx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--sx-bg);border-bottom:1px solid var(--sx-line);flex-wrap:wrap}
.sfx-app.sfx-app .sfx-bar>*{margin:0}
.sfx-app.sfx-app button.sfx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.sfx-app.sfx-app button.sfx-ghost{background:transparent;border-color:transparent}
.sfx-app.sfx-app button.sfx-ghost:hover:enabled{background:var(--sx-tint-2)}
.sfx-app.sfx-app button.sfx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.sfx-app.sfx-app button.sfx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--sx-ink-bad)}
.sfx-app .sfx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.sfx-app .sfx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.sfx-app .sfx-status .st{opacity:.72}
.sfx-app .sfx-status .st.warn{opacity:1;color:var(--sx-ink-warn)}
.sfx-app .sfx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.sfx-app .sfx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--sx-ok)}
.sfx-app .sfx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--sx-tint-2);user-select:all;-webkit-user-select:all}
.sfx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--sx-accent);border-radius:50%;animation:sfx-spin .8s linear infinite}
@keyframes sfx-spin{to{transform:rotate(360deg)}}
.sfx-app .skw{padding:6px 2px}
.sfx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.sfx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--sx-tint) 30%,var(--sx-tint-2) 50%,var(--sx-tint) 70%);animation:sfx-glow 1.3s ease-in-out infinite}
.sfx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.sfx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.sfx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes sfx-glow{from{background-position:100% 0}to{background-position:0 0}}
.sfx-app .sfx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--sx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.sfx-app .sfx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.sfx-app *,.sfx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.sfx-app.sfx-app .sfx-row{height:52px}
.sfx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.sfx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--sx-tint-2)}
.sfx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--sx-ink-ok)}
.sfx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--sx-ink-bad)}
.sfx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--sx-ink-warn)}
.sfx-app .ei.run{background:rgba(59,130,246,.15);color:var(--sx-accent)}
.sfx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.sfx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.sfx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.sfx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--sx-tint-2);margin-left:6px;vertical-align:1px}
.sfx-app .tag.x{background:rgba(219,39,119,.12);color:var(--sx-accent-2)}
.sfx-app.sfx-app .sfx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.6fr) repeat(5,minmax(70px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--sx-line)}
.sfx-app.sfx-app .sfx-lhead>*{margin:0;min-width:0}
.sfx-app.sfx-app button.sfx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sfx-app.sfx-app button.sfx-col.sfx-c-name{text-align:left;justify-content:flex-start}
.sfx-app.sfx-app button.sfx-col:hover:enabled{background:transparent;opacity:1;color:var(--sx-accent)}
.sfx-app.sfx-app button.sfx-col.sfx-on{opacity:1;color:var(--sx-accent)}
.sfx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.6fr) repeat(5,minmax(70px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.sfx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.sfx-app .mr .num.bad{color:var(--sx-ink-bad);font-weight:650}
.sfx-app .mr .num.warn{color:var(--sx-ink-warn);font-weight:650}
.sfx-app.sfx-app .sfx-machines{overflow:hidden auto}
.sfx-app.sfx-app .sfx-machines>*{flex:0 0 auto}
.sfx-app.sfx-app .sfx-opens{flex-wrap:wrap;gap:6px;margin:0 0 10px}
.sfx-app.sfx-app .sfx-opens>*{margin:0}
.sfx-app .sfn h3 .badge{display:none}
.sfx-app .sfn h3{font-size:17px;margin:6px 0 2px}
.sfx-app .sfn .card{border-radius:12px;background:var(--sx-tint);border-color:var(--sx-line)}
.sfx-app .sfn .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.sfx-app .sfn .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.sfx-app .sfn .card.ok{border-color:rgba(16,185,129,.55)}
.sfx-app .sfn .note,.sfx-app .sfn .fi{border-radius:4px 10px 10px 4px}
.sfx-app .sfn pre{border-radius:10px}
.sfx-app .sfn .story,.sfx-app .sfn .wf{max-width:none}
.sfx-app.sfx-app .sfx-graph{overflow:hidden auto;padding:0 6px 0 2px;min-height:0}
.sfx-app.sfx-app .sfx-graph>*{flex:0 0 auto;margin:0 0 6px}
.sfx-app.sfx-app .sfx-states{flex:0 0 360px;overflow:hidden auto}
.sfx-app.sfx-app .sfx-states .sfx-rows{flex:0 0 auto;overflow:visible;max-height:none}
.sfx-app.sfx-app .sfx-states .sfx-row{height:38px;margin:0}
.sfx-app .sfx-states .er{height:38px;gap:9px}
.sfx-app .sfx-states .en{font-size:12.5px}
.sfx-app .sfx-states .ef{margin-top:0}
.sfx-app .ei.m-ok{background:rgba(16,185,129,.18)}
.sfx-app .ei.m-failed{background:rgba(239,68,68,.2)}
.sfx-app .ei.m-running{background:rgba(59,130,246,.2)}
.sfx-app .ei.m-skip{background:transparent;border:1.5px dashed rgba(127,127,127,.5)}
.sfx-app .ei.m-h1{background:rgba(245,158,11,.2)}
.sfx-app .ei.m-h2{background:rgba(249,115,22,.28)}
.sfx-app .ei.m-h3{background:rgba(239,68,68,.32)}
.sfx-app .ei{width:14px;height:14px}
.sfx-app .sfx-states .sfn .tt{margin-top:8px}
""" + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "overview": "the State machines tab",
    "state_machine": "the Overview tab",
    "executions": "the Executions tab",
    "failures": "the Failures tab",
    "graph": "the Graph tab",
}

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he \w+ tab)")

_EXECUTION_CALL_RE = re.compile(r"""^execution\(['"](?:[^'"/]*/)?([^'"/:]+)['"]""")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (failures('order-pipeline') -> "the Failures tab", execution('order-pipeline/ord-7f3a') -> "ord-7f3a in the
    Executions tab"), capitalised where it starts a sentence; other calls, Lambda's and AWS CLI commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        one = _EXECUTION_CALL_RE.match(call)
        if one:
            return f"{one.group(1)} in the Executions tab"
        name = call.split("(", 1)[0].lstrip(".")
        return _IN_WINDOW.get(name, call)

    swapped = _CALL_RE.sub(swap, str(text or ""))
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), swapped)


def _status_tone(status: str) -> str:
    return {"RUNNING": "run"}.get(status, STATUS_TONES.get(status, ""))


def _execution_face(execution: Execution, words: list[str]) -> str:
    """
    An execution's line in the list: its status, its name (search words marked), when it started and how it ended,
    and how long it took.
    """
    icon = _STATUS_ICONS.get(execution.status, "?")
    word = STATUS_WORDS.get(execution.status, execution.status.lower())
    took = _duration(execution.seconds) if execution.seconds is not None else "so far"
    redriven = f" · redriven {execution.redrive_count}×" if execution.redrive_count else ""
    return (f'<div class="er"><span class="ei {_status_tone(execution.status)}">{icon}</span><div class="em">'
            f'<div class="en">{_marked(execution.name, words)}</div><div class="ef">{_esc(word)} · started '
            f'{_esc(_fmt_dt(execution.start, seconds=True))}{_esc(redriven)}</div></div><div class="ez">'
            f'{_esc(took)}<br>{_esc(human_age(execution.start))}</div></div>')


def _machine_note(machine: StateMachine) -> str:
    """
    What a state machine's line says under its name: its description, else its states and the functions it calls.
    """
    if machine.description:
        return machine.description
    calls = f" · calls {', '.join(machine.functions)}" if machine.functions else ""
    return f"{len([s for s in machine.states if s.depth == 0])} states{calls}"


def _machine_face(machine: StateMachine, numbers: MachineNumbers | None, cost: float | None, words: list[str]) -> str:
    """
    A state machine's line in the State machines tab: a dot for how it's doing, its name and type, then its
    executions, how many didn't succeed, its average run, its cost and its logging.
    """
    failing = numbers is not None and numbers.unsuccessful > 0
    tone = "bad" if failing and numbers.failed else ("warn" if failing else ("ok" if numbers else ""))
    kind = '<span class="tag x">EXPRESS</span>' if machine.is_express else '<span class="tag">STANDARD</span>'
    started = f"{numbers.started:,}" if numbers else "-"
    unsuccessful = f"{numbers.unsuccessful:,} ({numbers.failure_rate:.1%})" if failing else ("0" if numbers else "-")
    average = _duration((numbers.avg_ms or 0) / 1000) if numbers and numbers.avg_ms is not None else "-"
    return (f'<div class="mr"><span class="dot {tone}"></span><div class="em"><div class="en">'
            f'{_marked(machine.name, words)}{kind}</div><div class="ef">{_esc(_machine_note(machine))}</div></div><span class="num">{started}</span><span class="num {tone if failing else ""}">'
            f'{_esc(unsuccessful)}</span><span class="num">{_esc(average)}</span><span class="num">'
            f'{_esc(human_money(cost))}</span><span class="num">{_esc(machine.log_level.lower())}</span></div>')
