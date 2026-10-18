"""
What every tab of the window shares: the tabs, page sizes, run states, the stylesheet, how a
report's sentences are reworded for the window, and times as the lines show them.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ..._kit.fmt import _in_zone, _utcnow, parse_time
from ..._kit.render import _CALL_RE
from ..._kit.window import _icon_url


# ----------------------------------------------------------------------------- the explorer window

# The window's tabs, in order: (key, title, what it holds, the line drawing (24 x 24) shown before the title as a mask
# in the text's colour, so it follows the theme).
_EXPLORER_TABS = (
    ("functions", "Functions", "Every function in the region: search, filter, sort, and click one to open it",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("overview", "Overview", "How the function is doing: what to fix, what calls it, its last 30 days and its cost",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("logs", "Logs", "What it logged, run by run: click a run for its lines, search them, or watch new runs live",
     "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/><path d='M7.5 9.5l3 2.5-3 2.5M12.5 15h4'/>"),
    ("errors", "Errors", "Its errors grouped by cause, with what to do, and the runs that failed",
     "<path d='M12 4.2 20.8 19.5H3.2z'/><path d='M12 10v4.2M12 17h.01'/>"),
    ("performance", "Performance", "Run times, memory used and cold starts, and the slowest runs",
     "<circle cx='12' cy='13.5' r='7'/><path d='M12 13.5V9.8M9.8 3.5h4.4M18.4 7.1l1.4-1.4'/>"),
    ("code", "Code", "Its deployment package as a tree of folders, the source of the file you click to read or copy, "
     "and the .zip to download",
     "<path d='M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5'/>"),
    ("settings", "Settings", "Every setting in plain English, and as Lambda returns it",
     "<path d='M4 7.5h9M17.5 7.5H20M4 16.5h2.5M11 16.5h9'/><circle cx='15' cy='7.5' r='2.5'/>"
     "<circle cx='8.5' cy='16.5' r='2.5'/>"),
)

_FUNCTION_TABS = ("overview", "logs", "errors", "performance", "code", "settings")  # the tabs about one function

_PANEL = "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/>"

_FILES_ICONS = (  # the Code tab's Hide files / Show files button (a panel whose side folds in, or out) and How it
    # works
    ("files-hide", _PANEL + "<path d='M9.5 4.5v15M16 9.5 13.5 12l2.5 2.5'/>"),
    ("files-show", _PANEL + "<path d='M9.5 4.5v15M13.5 9.5 16 12l-2.5 2.5'/>"),
    ("how", "<circle cx='6' cy='6' r='2.5'/><circle cx='6' cy='18' r='2.5'/><circle cx='18' cy='12' r='2.5'/>"
            "<path d='M6 8.5v7M8.4 7.2l7.2 3.6M8.4 16.8l7.2-3.6'/>"),  # How it works: steps that branch
)


_FUNCTION_PAGE = 40  # functions on a page of the list; « ‹ › » move between pages

_RUN_PAGE = 25  # runs on a page of the Logs tab

_CODE_PAGE = 60  # lines (folders and files) on a page of the Code tab's tree


_RUN_STATES = {  # LogRun.status -> (icon, tone, what it means)
    "ok": ("✓", "ok", "ran"), "failed": ("✕", "bad", "failed"), "timeout": ("⏱", "bad", "timed out"),
    "logged": ("!", "warn", "logged an error"), "running": ("•", "run", "no REPORT line yet: still running, or it "
                                                                         "ends after the time range"),
    "outside": ("·", "", "lines that belong to no run in the time range"),
}


def _explorer_rules() -> str:
    """
    The explorer's icons, the tabs' and the Hide files button's: .lmx-i-<key> sets --lx-icon, which ::before draws.
    """
    icons = [(key, paths) for key, _, _, paths in _EXPLORER_TABS] + list(_FILES_ICONS)
    return "\n".join(f".lmx-app .lmx-i-{key}{{--lx-icon:{_icon_url(paths)}}}" for key, paths in icons)


_EXPLORER_DARK = ("--lx-accent:#60a5fa;--lx-accent-2:#a78bfa;--lx-soft:rgba(96,165,250,.15);--lx-ring:rgba(96,165,250,"
                  ".38);--lx-raised:rgba(255,255,255,.11);--lx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--lx-ink-bad:#f87171;--lx-ink-warn:#fbbf24;--lx-ink-ok:#34d399;--lx-code:rgba(255,255,255,.035);"
                  "--lx-tok-k:#c792ea;--lx-tok-s:#a5d6a7;--lx-tok-n:#f78c6c;--lx-tok-f:#82aaff;--lx-tok-c:#7f8c98")

_EXPLORER_CSS = """<style>
.lmx-app{--lx-accent:#2563eb;--lx-button:#2563eb;--lx-accent-2:#7c3aed;--lx-soft:rgba(37,99,235,.10);--lx-ring:rgba(37,99,235,.28);--lx-line:rgba(127,127,127,.22);--lx-line-2:rgba(127,127,127,.36);--lx-tint:rgba(127,127,127,.06);--lx-tint-2:rgba(127,127,127,.11);--lx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--lx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--lx-raised:var(--lx-surface);--lx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--lx-ok:#10b981;--lx-warn:#f59e0b;--lx-bad:#ef4444;--lx-ink-bad:#dc2626;--lx-ink-warn:#b45309;--lx-ink-ok:#047857;--lx-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--lx-code:rgba(127,127,127,.05);--lx-tok-k:#7c3aed;--lx-tok-s:#047857;--lx-tok-n:#c2410c;--lx-tok-f:#2563eb;--lx-tok-c:#64748b}
body[data-jp-theme-light="false"] .lmx-app,body.vscode-dark .lmx-app,body.vscode-high-contrast .lmx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .lmx-app{""" + _EXPLORER_DARK + """}}
.lmx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--lx-line);border-radius:18px;padding:14px 16px 10px;background:var(--lx-bg);box-shadow:var(--lx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.lmx-app *{box-sizing:border-box}
.lmx-app .widget-html-content,.lmx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.lmx-app.lmx-app .lmx-flat>*{margin:0}
.lmx-app.lmx-app button.jupyter-button{color:inherit;background:var(--lx-tint);border:1px solid var(--lx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.lmx-app.lmx-app button.jupyter-button:hover:enabled{background:var(--lx-tint-2);border-color:var(--lx-line-2);box-shadow:none}
.lmx-app.lmx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.lmx-app.lmx-app button.jupyter-button:focus-visible{outline:2px solid var(--lx-ring);outline-offset:-1px}
.lmx-app.lmx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.lmx-app.lmx-app button.jupyter-button.mod-primary{background:var(--lx-button);border-color:transparent;color:#fff;font-weight:600}
.lmx-app.lmx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--lx-button);filter:brightness(1.08)}
.lmx-app.lmx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.lmx-app.lmx-app .widget-text input,.lmx-app.lmx-app .jupyter-widget-text input,.lmx-app.lmx-app .widget-dropdown>select,.lmx-app.lmx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--lx-line-2);border-radius:9px;background-color:var(--lx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.lmx-app.lmx-app .widget-dropdown>select,.lmx-app.lmx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.lmx-app.lmx-app .widget-text input:focus,.lmx-app.lmx-app .jupyter-widget-text input:focus,.lmx-app.lmx-app .widget-dropdown>select:focus,.lmx-app.lmx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .widget-text,.lmx-app.lmx-app .widget-dropdown,.lmx-app.lmx-app .jupyter-widget-text,.lmx-app.lmx-app .jupyter-widget-dropdown{margin:0;height:auto}
.lmx-app.lmx-app .lmx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--lx-line);overflow:visible}
.lmx-app.lmx-app .lmx-top{align-items:center;gap:10px;overflow:visible}
.lmx-app.lmx-app .lmx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.lmx-app .lmx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.lmx-app .lmx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,#fb923c,#ea580c);box-shadow:0 2px 8px rgba(234,88,12,.32);flex:0 0 auto}
.lmx-app .lmx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .lmx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.lmx-app .lmx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app.lmx-app .lmx-region{flex:0 0 auto;width:auto;min-width:170px}
.lmx-app.lmx-app button.lmx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-ghost{background:transparent;border-color:transparent}
.lmx-app.lmx-app button.lmx-ghost:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app button.lmx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--lx-ink-bad)}
.lmx-app .lmx-stats{display:flex;flex-wrap:wrap;gap:8px}
.lmx-app .lmx-stat{border:1px solid var(--lx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--lx-tint)}
.lmx-app .lmx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.lmx-app .lmx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.lmx-app .lmx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.lmx-app .lmx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.lmx-app .lmx-stat.ok{border-color:rgba(16,185,129,.55)}
.lmx-app .lmx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--lx-tint-2) 30%,var(--lx-line) 50%,var(--lx-tint-2) 70%);animation:lmx-glow 1.3s ease-in-out infinite}
.lmx-app.lmx-app .lmx-field{position:relative;overflow:visible;flex:0 0 320px;max-width:100%}
.lmx-app.lmx-app .lmx-field.lmx-open{z-index:41}
.lmx-app.lmx-app .lmx-trig{position:relative;min-height:52px;overflow:visible}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b,.lmx-app.lmx-app .lmx-opt>.lmx-opt-b,.lmx-app.lmx-app .lmx-row>.lmx-row-b,.lmx-app.lmx-app .lmx-run-h>.lmx-run-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b{border-color:var(--lx-line-2);border-radius:12px;background:var(--lx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b:hover:enabled{border-color:var(--lx-accent);background:var(--lx-surface)}
.lmx-app.lmx-app .lmx-open .lmx-trig>.lmx-trig-b{border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b:active:enabled,.lmx-app.lmx-app .lmx-opt>.lmx-opt-b:active:enabled,.lmx-app.lmx-app .lmx-row>.lmx-row-b:active:enabled,.lmx-app.lmx-app .lmx-run-h>.lmx-run-b:active:enabled{transform:none}
.lmx-app.lmx-app .lmx-trig>.lmx-face,.lmx-app.lmx-app .lmx-opt>.lmx-opt-t,.lmx-app.lmx-app .lmx-row>.lmx-row-t,.lmx-app.lmx-app .lmx-run-h>.lmx-run-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.lmx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.lmx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.lmx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.lmx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.lmx-app .fxi,.lmx-app .opi{font-family:var(--lx-mono);font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.lmx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.lmx-app .lmx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--lx-accent)}
.lmx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.45)}
.lmx-app .dot.ok{background:var(--lx-ok)}
.lmx-app .dot.warn{background:var(--lx-warn)}
.lmx-app .dot.bad{background:var(--lx-bad)}
.lmx-app .dot.idle{background:transparent;box-shadow:inset 0 0 0 1.5px rgba(127,127,127,.55)}
.lmx-app.lmx-app .lmx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(500px,calc(100vw - 48px));padding:8px;border:1px solid var(--lx-line-2);border-radius:14px;background:var(--lx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.lmx-app.lmx-app .lmx-pop>*{margin:0}
.lmx-app.lmx-app .lmx-opts{max-height:360px;overflow:hidden auto;margin:6px 0 0}
.lmx-app.lmx-app .lmx-opts>*{flex:0 0 auto}
.lmx-app.lmx-app .lmx-opt{position:relative;margin:0 0 2px;overflow:visible}
.lmx-app.lmx-app .lmx-opt>.lmx-opt-b{border-radius:10px}
.lmx-app.lmx-app .lmx-opt>.lmx-opt-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-opt.lmx-on>.lmx-opt-b{background:var(--lx-soft);border-color:var(--lx-ring)}
.lmx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.lmx-app .op .dot{margin-top:6px}
.lmx-app .opb{flex:1 1 auto;min-width:0}
.lmx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.lmx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.lmx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--lx-line);line-height:1.4}
.lmx-app .opf.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.lmx-app.lmx-app .lmx-backdrop,.lmx-app.lmx-app .lmx-backdrop:hover:enabled,.lmx-app.lmx-app .lmx-backdrop:active:enabled,.lmx-app.lmx-app .lmx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.lmx-app.lmx-app .lmx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--lx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.lmx-app.lmx-app button.lmx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.lmx-app.lmx-app button.lmx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--lx-icon) center/contain no-repeat;mask:var(--lx-icon) center/contain no-repeat}
.lmx-app.lmx-app button.lmx-tab:hover:enabled{background:var(--lx-tint);opacity:.95}
.lmx-app.lmx-app button.lmx-tab.lmx-on,.lmx-app.lmx-app button.lmx-tab.lmx-on:hover:enabled{background:var(--lx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.lmx-app.lmx-app button.lmx-tab.lmx-on::before{background:var(--lx-accent)}
.lmx-app.lmx-app button.lmx-tab.lmx-dim{opacity:.42}
.lmx-app.lmx-app button.lmx-tab.lmx-alert::after,.lmx-app.lmx-app button.lmx-tab.lmx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--lx-warn);flex:0 0 auto}
.lmx-app.lmx-app button.lmx-tab.lmx-alarm::after{background:var(--lx-bad)}
@container (max-width:640px){.lmx-app.lmx-app button.lmx-tab::before{display:none}}
.lmx-app.lmx-app .lmx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden;padding:0}
body[class*=vscode-] .lmx-app.lmx-app .lmx-page{height:620px}
.lmx-app.lmx-app .lmx-page>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-page>.lmx-scroll,.lmx-app.lmx-app .lmx-page>.lmx-split{flex:1 1 auto;min-height:0}
.lmx-app.lmx-app .lmx-scroll{overflow:hidden auto;padding:2px 6px 2px 2px}
.lmx-app.lmx-app .lmx-codehead{max-height:45%;overflow:hidden auto;padding:2px 6px 6px 2px}
.lmx-app.lmx-app .lmx-codehead>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-scroll>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-find{position:relative;align-items:center}
.lmx-app.lmx-app .lmx-find input,.lmx-app.lmx-app .lmx-find input:focus{padding-left:32px;background:var(--lx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.lmx-app.lmx-app .lmx-bar{gap:8px;align-items:center;padding:0 0 8px;flex-wrap:wrap;overflow:visible}
.lmx-app.lmx-app .lmx-bar>*{margin:0}
.lmx-app.lmx-app .lmx-chips{flex-wrap:wrap;gap:6px;margin:0 0 8px}
.lmx-app.lmx-app .lmx-chips>*{margin:0}
.lmx-app.lmx-app button.lmx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--lx-line);display:inline-flex;align-items:center;gap:6px}
.lmx-app.lmx-app button.lmx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-all::before{display:none}
.lmx-app.lmx-app button.lmx-chip.lmx-t-bad::before{background:var(--lx-bad)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-warn::before{background:var(--lx-warn)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-ok::before{background:var(--lx-ok)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-cold::before{background:#38bdf8}
.lmx-app.lmx-app button.lmx-chip.lmx-t-idle::before{background:transparent;box-shadow:inset 0 0 0 1.5px rgba(127,127,127,.6)}
.lmx-app.lmx-app button.lmx-chip.lmx-on,.lmx-app.lmx-app button.lmx-chip.lmx-on:hover:enabled{background:var(--lx-soft);border-color:var(--lx-ring);color:var(--lx-accent);font-weight:650}
.lmx-app.lmx-app .lmx-rows,.lmx-app.lmx-app .lmx-runs{overflow:hidden auto;padding:0 4px 4px 0;border:1px solid var(--lx-line);border-radius:14px;background:var(--lx-surface)}
.lmx-app.lmx-app .lmx-rows>*,.lmx-app.lmx-app .lmx-runs>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-lhead{position:sticky;top:0;z-index:3;gap:10px;padding:6px 10px 6px 12px;background:var(--lx-surface);border-bottom:1px solid var(--lx-line);align-items:center}
.lmx-app.lmx-app .lmx-lhead>*{margin:0}
.lmx-app.lmx-app button.lmx-col{height:24px;padding:0 4px;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-col.lmx-c-name{flex:1 1 auto;text-align:left;padding-left:18px}
.lmx-app.lmx-app button.lmx-col:hover:enabled{opacity:1;background:var(--lx-tint-2)}
.lmx-app.lmx-app button.lmx-col.lmx-on{opacity:1;color:var(--lx-accent)}
.lmx-app.lmx-app .lmx-row{position:relative;height:54px;margin:0 0 1px;overflow:visible}
.lmx-app.lmx-app .lmx-row.lmx-short{height:42px}
.lmx-app.lmx-app .lmx-row>.lmx-row-b{border-radius:10px}
.lmx-app.lmx-app .lmx-row>.lmx-row-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-row.lmx-on>.lmx-row-b,.lmx-app.lmx-app .lmx-row.lmx-on>.lmx-row-b:hover:enabled{background:var(--lx-soft);border-color:var(--lx-ring)}
.lmx-app.lmx-app button.lmx-act{position:absolute;right:10px;top:50%;transform:translateY(-50%);z-index:2;display:none;width:auto;height:26px;padding:0 11px;border-radius:999px;font-size:12px;background:var(--lx-raised);border-color:var(--lx-line-2);color:var(--lx-accent);box-shadow:0 1px 3px rgba(15,23,42,.12)}
.lmx-app.lmx-app .lmx-row:hover>button.lmx-act,.lmx-app.lmx-app .lmx-row.lmx-on>button.lmx-act{display:inline-flex;align-items:center}
.lmx-app.lmx-app button.lmx-act:active:enabled{transform:translateY(-50%)}
.lmx-app .fr{display:grid;grid-template-columns:8px minmax(0,1fr) 86px 64px 72px 76px 92px 38px 52px;align-items:center;gap:0 10px;height:54px;padding:0 10px 0 12px;min-width:0}
.lmx-app .fm{min-width:0;line-height:1.3}
.lmx-app .fn{display:flex;align-items:baseline;gap:8px;min-width:0;white-space:nowrap}
.lmx-app .fn b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.lmx-app .rt{flex:0 0 auto;font-size:11px;padding:0 7px;border-radius:999px;background:var(--lx-tint-2);font-weight:500}
.lmx-app .rt.warn{background:rgba(245,158,11,.16);color:var(--lx-ink-warn)}
.lmx-app .rt.bad{background:rgba(239,68,68,.13);color:var(--lx-ink-bad)}
.lmx-app .rg{flex:0 0 auto;font-size:11px;opacity:.6;font-family:var(--lx-mono)}
.lmx-app .ff{font-size:11.5px;opacity:.68;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.lmx-app .fc{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12.5px;min-width:0;overflow:hidden;text-overflow:ellipsis}
.lmx-app .fc.dim{opacity:.55}
.lmx-app .fc .pill{display:inline-block;padding:0 7px;border-radius:999px;font-weight:600;font-size:11.5px}
.lmx-app .fc .pill.warn{background:rgba(245,158,11,.18);color:var(--lx-ink-warn)}
.lmx-app .fc .pill.bad{background:rgba(239,68,68,.15);color:var(--lx-ink-bad)}
.lmx-app .wb{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:20px;padding:0 6px;border-radius:999px;font-size:11.5px;font-weight:700;background:rgba(245,158,11,.18);color:var(--lx-ink-warn)}
.lmx-app .sk{display:inline-block;width:42px;height:10px;border-radius:5px;vertical-align:middle;background-size:300% 100%;background-image:linear-gradient(90deg,var(--lx-tint) 30%,var(--lx-tint-2) 50%,var(--lx-tint) 70%);animation:lmx-glow 1.3s ease-in-out infinite}
@container (max-width:900px){.lmx-app .fr{grid-template-columns:8px minmax(0,1fr) 80px 60px 70px 38px 52px}.lmx-app .fr .c-dur,.lmx-app .fr .c-called{display:none}.lmx-app.lmx-app .lmx-lhead .lmx-c-duration,.lmx-app.lmx-app .lmx-lhead .lmx-c-called{display:none}}
.lmx-app.lmx-app .lmx-listpage{container-type:inline-size}
.lmx-app .lmx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.lmx-app.lmx-app .lmx-pager{align-items:center;gap:2px;padding:6px 2px 0;margin-top:2px}
.lmx-app.lmx-app .lmx-pager>*{margin:0}
.lmx-app.lmx-app button.lmx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.lmx-app .lmx-pager .widget-html-content,.lmx-app .lmx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app.lmx-app .lmx-ask{flex:1 1 300px;align-items:center;gap:4px;padding:3px 3px 3px 6px;border:1px solid var(--lx-line-2);border-radius:999px;background:var(--lx-surface);transition:border-color .15s,box-shadow .15s;min-width:0}
.lmx-app.lmx-app .lmx-ask>*{margin:0}
.lmx-app.lmx-app .lmx-ask:focus-within{border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .lmx-ask .widget-text input,.lmx-app.lmx-app .lmx-ask .jupyter-widget-text input,.lmx-app.lmx-app .lmx-ask .widget-text input:focus,.lmx-app.lmx-app .lmx-ask .jupyter-widget-text input:focus{border:0;box-shadow:none;background-color:transparent;height:28px;font-size:13px}
.lmx-app.lmx-app .lmx-range{flex:0 0 auto;width:150px}
.lmx-app.lmx-app button.lmx-live{width:auto;height:32px;padding:0 14px;border-radius:999px;font-size:12.5px;font-weight:600;display:inline-flex;align-items:center;gap:7px;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-live::before{content:"";width:8px;height:8px;border-radius:50%;background:rgba(127,127,127,.55)}
.lmx-app.lmx-app button.lmx-live.lmx-on,.lmx-app.lmx-app button.lmx-live.lmx-on:hover:enabled{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.55);color:var(--lx-ink-bad)}
.lmx-app.lmx-app button.lmx-live.lmx-on::before{background:#ef4444;animation:lmx-pulse 1.4s ease-in-out infinite}
@keyframes lmx-pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.5)}50%{box-shadow:0 0 0 5px rgba(239,68,68,0)}}
.lmx-app .lmx-sum{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;font-size:12px;padding:0 2px 8px}
.lmx-app .lmx-sum span{white-space:nowrap;opacity:.78}
.lmx-app .lmx-sum b{font-weight:650;opacity:1}
.lmx-app .lmx-sum .bad{color:var(--lx-ink-bad);opacity:1}
.lmx-app .lmx-sum .warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .lmx-sum .lmx-note{flex:1 1 100%;white-space:normal;opacity:.75}
.lmx-app.lmx-app .lmx-run{margin:0;border-bottom:1px solid var(--lx-line);overflow:visible}
.lmx-app.lmx-app .lmx-run:last-child{border-bottom:0}
.lmx-app.lmx-app .lmx-run-h{position:relative;height:42px;margin:0;overflow:visible}
.lmx-app.lmx-app .lmx-run-h>.lmx-run-b{border-radius:0}
.lmx-app.lmx-app .lmx-run-h>.lmx-run-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-run.lmx-open>.lmx-run-h>.lmx-run-b{background:var(--lx-tint)}
.lmx-app .rr{display:grid;grid-template-columns:12px 18px 92px 136px 104px 64px minmax(0,1fr) 76px;align-items:center;gap:0 10px;height:42px;padding:0 12px 0 10px;font-size:12.5px;min-width:0;white-space:nowrap}
.lmx-app .rr .rv{width:7px;height:7px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(-45deg);opacity:.45;transition:transform .15s}
.lmx-app .lmx-open .rr .rv{transform:rotate(45deg);opacity:.8}
.lmx-app .rr .ri{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:11px;font-weight:800;background:var(--lx-tint-2)}
.lmx-app .rr.ok .ri{background:rgba(16,185,129,.16);color:var(--lx-ink-ok)}
.lmx-app .rr.bad .ri{background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .rr.warn .ri{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .rr.run .ri{background:rgba(59,130,246,.16);color:var(--lx-accent)}
.lmx-app .rr .rw{font-variant-numeric:tabular-nums;opacity:.8}
.lmx-app .rr .rd{display:flex;align-items:center;gap:7px;font-variant-numeric:tabular-nums}
.lmx-app .rr .rb{display:inline-block;width:44px;height:6px;border-radius:3px;background:var(--lx-tint-2);overflow:hidden;flex:0 0 auto}
.lmx-app .rr .rb i{display:block;height:100%;border-radius:3px;background:rgba(59,130,246,.7)}
.lmx-app .rr .rb i.warn{background:var(--lx-warn)}
.lmx-app .rr .rb i.bad{background:var(--lx-bad)}
.lmx-app .rr .rm{font-variant-numeric:tabular-nums;opacity:.75;overflow:hidden;text-overflow:ellipsis}
.lmx-app .rr .rc{font-size:11px;font-weight:600;color:#0284c7;overflow:hidden;text-overflow:ellipsis}
.lmx-app .rr .rs{overflow:hidden;text-overflow:ellipsis;opacity:.75;min-width:0}
.lmx-app .rr .rs.bad{color:var(--lx-ink-bad);opacity:1;font-weight:500}
.lmx-app .rr .rs.warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .rr .rq{font-family:var(--lx-mono);font-size:11px;opacity:.5;text-align:right;overflow:hidden;text-overflow:ellipsis}
@container (max-width:820px){.lmx-app .rr{grid-template-columns:12px 18px 80px 120px minmax(0,1fr)}.lmx-app .rr .rm,.lmx-app .rr .rc,.lmx-app .rr .rq{display:none}}
.lmx-app.lmx-app .lmx-logpage{container-type:inline-size}
.lmx-app .rbody{padding:4px 0 10px;background:var(--lx-code);border-top:1px solid var(--lx-line)}
.lmx-app .rl{display:grid;grid-template-columns:70px 50px minmax(0,1fr);gap:0 10px;padding:1px 14px 1px 40px;font-family:var(--lx-mono);font-size:12px;line-height:1.55}
.lmx-app .rl:hover{background:var(--lx-tint)}
.lmx-app .rl .lt{opacity:.48;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.lmx-app .rl .lv{align-self:start;margin-top:2px;justify-self:start;font-size:9.5px;font-weight:700;letter-spacing:.04em;padding:0 5px;border-radius:4px;line-height:15px;opacity:.85}
.lmx-app .rl .lv.error{background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .rl .lv.warn{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .rl .lv.info{background:rgba(59,130,246,.13);color:var(--lx-accent)}
.lmx-app .rl .lv.debug{background:var(--lx-tint-2)}
.lmx-app .rl .lm{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
.lmx-app .rl.error{background:rgba(239,68,68,.06)}
.lmx-app .rl.error .lm{color:var(--lx-ink-bad)}
.lmx-app .rl.warn .lm{color:var(--lx-ink-warn)}
.lmx-app .rl.platform{opacity:.55}
.lmx-app .rl.hit{box-shadow:inset 3px 0 var(--lx-accent)}
.lmx-app .rl details{display:inline}
.lmx-app .rl summary{display:inline;cursor:pointer;font-size:11px;opacity:.6;margin-left:8px;list-style:none}
.lmx-app .rl summary::-webkit-details-marker{display:none}
.lmx-app .rl details[open] summary{opacity:.9}
.lmx-app .rl .jf{display:block;margin:3px 0 4px;padding:6px 9px;border-radius:7px;border:1px solid var(--lx-line);background:var(--lx-bg);white-space:pre-wrap;font-size:11.5px;color:var(--jp-content-font-color1,inherit)}
.lmx-app .rf{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:8px 14px 2px 40px;font-size:11.5px}
.lmx-app .rf span{white-space:nowrap;opacity:.75}
.lmx-app .rf b{font-weight:650}
.lmx-app .rf code{font-family:var(--lx-mono);font-size:11px;padding:1px 5px;border-radius:5px;background:var(--lx-tint-2);user-select:all;-webkit-user-select:all;cursor:text;opacity:1}
.lmx-app .rf .warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .rf .bad{color:var(--lx-ink-bad);opacity:1}
.lmx-app .rmore{padding:4px 14px 0 40px;font-size:11.5px;opacity:.6}
.lmx-app .er{display:grid;grid-template-columns:18px 112px 150px minmax(0,1fr) 84px;align-items:center;gap:0 10px;height:42px;padding:0 12px 0 10px;font-size:12.5px;white-space:nowrap;min-width:0}
.lmx-app .er .ri{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:11px;font-weight:800;background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .er.slow .ri{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .er .ew{font-variant-numeric:tabular-nums;opacity:.8}
.lmx-app .er .ek{font-weight:600;overflow:hidden;text-overflow:ellipsis}
.lmx-app .er .es{overflow:hidden;text-overflow:ellipsis;opacity:.8}
.lmx-app .er .eq{font-family:var(--lx-mono);font-size:11px;opacity:.5;text-align:right}
.lmx-app .er .eo{color:var(--lx-accent);font-size:12px;text-align:right;opacity:0;transition:opacity .15s}
.lmx-app .lmx-row:hover .er .eo{opacity:1}
.lmx-app .lmx-h{font-size:13px;font-weight:650;margin:14px 0 6px}
.lmx-app .lmx-h span{font-weight:400;opacity:.6;font-size:12px;margin-left:6px}
.lmx-app.lmx-app .lmx-split{gap:12px;align-items:stretch;overflow:hidden}
.lmx-app.lmx-app .lmx-left{flex:0 0 300px;min-width:240px;max-width:40%;border:1px solid var(--lx-line);border-radius:14px;padding:8px 8px 6px;background:var(--lx-surface);overflow:hidden}
.lmx-app.lmx-app .lmx-left>*{margin:0;flex:0 0 auto}
.lmx-app.lmx-app .lmx-left>.lmx-files{flex:1 1 auto;min-height:0;overflow:hidden auto;margin:6px -2px 0;padding:0 2px}
.lmx-app.lmx-app .lmx-files>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.lmx-app.lmx-app .lmx-right>*{flex:0 0 auto;margin:0}
@media (max-width:900px){.lmx-app.lmx-app .lmx-split{flex-wrap:wrap;overflow:hidden auto}.lmx-app.lmx-app .lmx-left{flex:1 1 100%;max-width:100%;height:360px}.lmx-app.lmx-app .lmx-right{flex:1 1 100%;overflow:visible}}
.lmx-app.lmx-app .lmx-codebar{align-items:center;gap:10px;padding:8px 2px 10px;flex-wrap:wrap}
.lmx-app.lmx-app .lmx-codebar>*{margin:0}
.lmx-app.lmx-app .lmx-codebar button.lmx-small{font-weight:600;color:var(--lx-accent)}
.lmx-app.lmx-app .lmx-codebar button.lmx-side.lmx-on{background:var(--lx-soft);border-color:var(--lx-accent);color:var(--lx-accent);font-weight:600}
.lmx-app.lmx-app .lmx-codebar button.lmx-side{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:108px;padding:0 13px 0 11px;font-weight:500;color:inherit}
.lmx-app.lmx-app .lmx-codebar button.lmx-side::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;opacity:.75;-webkit-mask:var(--lx-icon) center/contain no-repeat;mask:var(--lx-icon) center/contain no-repeat}
.lmx-app .cbs{font-size:12px;opacity:.7;line-height:1.4}
.lmx-app .cbs.ok{opacity:1}
.lmx-app .cbs.ok b{font-weight:600;color:var(--lx-ink-ok)}
.lmx-app .cbs code{font-family:var(--lx-mono);font-size:11px;padding:1px 5px;border-radius:5px;background:var(--lx-tint-2);color:inherit;user-select:all;-webkit-user-select:all;cursor:text}
.lmx-app.lmx-app .lmx-row.lmx-tree{height:30px;margin:0}
.lmx-app.lmx-app .lmx-row.lmx-tree>.lmx-row-b{border-radius:7px}
.lmx-app .ftl{display:flex;align-items:center;gap:6px;height:30px;padding:0 8px 0 calc(6px + var(--d,0) * 16px);min-width:0;font-size:12.5px;background:repeating-linear-gradient(90deg,transparent 0 11.5px,var(--lx-line) 11.5px 12.5px,transparent 12.5px 16px) 0 0/calc(var(--d,0) * 16px) 100% no-repeat}
.lmx-app .ftl .tv{flex:0 0 12px;width:12px;height:12px;display:inline-flex;align-items:center;justify-content:center}
.lmx-app .ftl.d .tv::before{content:"";width:6px;height:6px;margin-left:-2px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(-45deg);opacity:.55;transition:transform .15s}
.lmx-app .ftl.d.open .tv::before{transform:rotate(45deg);margin:-3px 0 0}
.lmx-app .ftl .ti{flex:0 0 30px;height:16px;display:inline-flex;align-items:center;justify-content:center}
.lmx-app .ftl .ti::before{content:"";width:16px;height:16px;background:var(--lx-accent);opacity:.8;-webkit-mask:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%272%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M3.5%206.5a1.5%201.5%200%200%201%201.5-1.5h4.3l2%202.2H19a1.5%201.5%200%200%201%201.5%201.5v9.3a1.5%201.5%200%200%201-1.5%201.5H5a1.5%201.5%200%200%201-1.5-1.5z%27/%3E%3C/svg%3E") center/contain no-repeat;mask:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%272%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M3.5%206.5a1.5%201.5%200%200%201%201.5-1.5h4.3l2%202.2H19a1.5%201.5%200%200%201%201.5%201.5v9.3a1.5%201.5%200%200%201-1.5%201.5H5a1.5%201.5%200%200%201-1.5-1.5z%27/%3E%3C/svg%3E") center/contain no-repeat}
.lmx-app .ftl.open .ti::before{-webkit-mask-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%272%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M3.5%2017.5V6.5A1.5%201.5%200%200%201%205%205h4.3l2%202.2H17.5A1.5%201.5%200%200%201%2019%208.7V10%27/%3E%3Cpath%20d%3D%27M3.5%2018.5%206.4%2011a1.5%201.5%200%200%201%201.4-1H20.6a1%201%200%200%201%20.9%201.4l-2.6%206.7a1.5%201.5%200%200%201-1.4.9H4.5%27/%3E%3C/svg%3E");mask-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27black%27%20stroke-width%3D%272%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M3.5%2017.5V6.5A1.5%201.5%200%200%201%205%205h4.3l2%202.2H17.5A1.5%201.5%200%200%201%2019%208.7V10%27/%3E%3Cpath%20d%3D%27M3.5%2018.5%206.4%2011a1.5%201.5%200%200%201%201.4-1H20.6a1%201%200%200%201%20.9%201.4l-2.6%206.7a1.5%201.5%200%200%201-1.4.9H4.5%27/%3E%3C/svg%3E")}
.lmx-app .ftl .tn{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .ftl.d .tn{font-weight:600}
.lmx-app .ftl .tz{flex:0 0 auto;font-size:11px;opacity:.55;font-variant-numeric:tabular-nums}
.lmx-app .ftl.junk{opacity:.55}
.lmx-app .ct{flex:0 0 auto;width:30px;height:16px;border-radius:5px;display:inline-flex;align-items:center;justify-content:center;font-size:8.5px;font-weight:750;letter-spacing:.03em;background:var(--lx-tint-2)}
.lmx-app .ct.py{background:rgba(59,130,246,.14);color:var(--lx-accent)}
.lmx-app .ct.js{background:rgba(234,179,8,.18);color:#a16207}
.lmx-app .ct.cfg{background:rgba(16,185,129,.14);color:var(--lx-ink-ok)}
.lmx-app .ct.key{background:rgba(239,68,68,.13);color:var(--lx-ink-bad)}
.lmx-app .ftl .hb{font-size:9px;font-weight:700;padding:0 5px;border-radius:4px;background:rgba(234,88,12,.14);color:#c2410c;margin-left:6px}
.lmx-app .srch{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:2px 0 8px}
.lmx-app .srch b{font-size:14px;font-family:var(--lx-mono)}
.lmx-app .srch span{font-size:12px;opacity:.65}
.lmx-app .srch .cpy{align-self:center;position:relative;margin-left:auto;display:inline-flex;align-items:center;height:26px;padding:0 12px;border-radius:999px;border:1px solid var(--lx-line-2);background:var(--lx-tint);color:var(--lx-accent);font-weight:600;opacity:1;white-space:nowrap;cursor:pointer;user-select:all;-webkit-user-select:all;outline:none;transition:background-color .15s,border-color .15s}
.lmx-app .srch .cpy::before{content:"\\29C9\\2002 Copy"}
.lmx-app .srch .cpy:hover{background:var(--lx-tint-2)}
.lmx-app .srch .cpy:focus{border-color:var(--lx-accent);background:var(--lx-soft)}
.lmx-app .srch .cpy:focus-visible{outline:2px solid var(--lx-ring);outline-offset:1px}
.lmx-app .srch .cpy:focus:not(:focus-visible)::before{content:"\\2713\\2002Selected: press Ctrl+C or \\2318 C"}
.lmx-app .srch .cpy .cpt{position:absolute;left:0;top:0;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:pre;opacity:1}
.lmx-app .srch .cpy ::selection,.lmx-app .srch .cpy::selection{background:transparent}
.lmx-app .src{display:flex;border:1px solid var(--lx-line);border-radius:10px;background:var(--lx-code);overflow:auto;max-height:none;font-family:var(--lx-mono);font-size:12px;line-height:1.55}
.lmx-app .src pre{margin:0;padding:8px 12px;border:0;background:transparent;white-space:pre;overflow:visible;max-height:none;font-size:inherit;line-height:inherit;font-family:inherit}
.lmx-app .src pre.gut{flex:0 0 auto;text-align:right;opacity:.38;user-select:none;-webkit-user-select:none;border-right:1px solid var(--lx-line);padding-right:10px}
.lmx-app .src pre.txt{flex:1 1 auto;user-select:text}
.lmx-app .src .pk{color:var(--lx-tok-k)}
.lmx-app .src .js,.lmx-app .src .jk{color:var(--lx-tok-s)}
.lmx-app .src .jk{font-weight:600}
.lmx-app .src .jn,.lmx-app .src .jl{color:var(--lx-tok-n)}
.lmx-app .src .pf{color:var(--lx-tok-f)}
.lmx-app .src .pa{color:var(--lx-tok-n);font-style:italic}
.lmx-app .src .pc{color:var(--lx-tok-c);font-style:italic}
.lmx-app .lmx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.lmx-app .lmx-status .st{opacity:.72}
.lmx-app .lmx-status .st.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app .lmx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.lmx-app .lmx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--lx-ok)}
.lmx-app .lmx-status .st.live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;background:#ef4444;animation:lmx-pulse 1.4s ease-in-out infinite}
.lmx-app .lmx-status code,.lmx-app .lmx-hint code{font-family:var(--lx-mono);font-size:11px;padding:1px 5px;border-radius:5px;background:var(--lx-tint-2);user-select:all;-webkit-user-select:all}
.lmx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--lx-accent);border-radius:50%;animation:lmx-spin .8s linear infinite}
@keyframes lmx-spin{to{transform:rotate(360deg)}}
.lmx-app .skw{padding:6px 2px}
.lmx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.lmx-app .skw .sk{display:block;width:auto;height:12px;margin:10px 0;border-radius:6px}
.lmx-app .skw .sk.t{height:18px;width:42%;margin-bottom:16px}
.lmx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.lmx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes lmx-glow{from{background-position:100% 0}to{background-position:0 0}}
.lmx-app .lmx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--lx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.lmx-app .lmx-hint b{font-weight:650}
.lmx-app .lmb h3{font-size:17px;margin:6px 0 2px}
.lmx-app .lmb h3 .badge{display:none}
.lmx-app .lmb .card{border-radius:12px;background:var(--lx-tint);border-color:var(--lx-line)}
.lmx-app .lmb .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.lmx-app .lmb .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.lmx-app .lmb .card.ok{border-color:rgba(16,185,129,.55)}
.lmx-app .lmb .note,.lmx-app .lmb .fi{border-radius:4px 10px 10px 4px}
.lmx-app .lmb pre,.lmx-app .lmb .jt{border-radius:10px}
.lmx-app .lmb .wire .wn,.lmx-app .lmb .wire .wf{border-radius:12px;background-color:var(--lx-surface)}
@media (prefers-reduced-motion:reduce){.lmx-app *,.lmx-app *::before{transition:none!important;animation-duration:2.5s!important}}
""" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {  # a command a report names -> where the explorer window shows the same
    "functions": "the Functions tab",
    "function_info": "the Settings tab",
    "errors": "the Errors tab",
    "logs": "the Logs tab",
    "performance": "the Performance tab",
    "code": "the Code tab",
    "how_it_works": "How it works in the Code tab",
}


_WINDOW_WORDS = [(re.compile(pattern), plain) for pattern, plain in (  # a command's arguments -> what they do
    (r"\bfunctions\(regions='all'\)", "All regions in the region field"),
    (r"\bfunction_info\([^()]*\) shows when it was last called", "the Overview tab shows when it was last called"),
    (r"\bpass limit= to read more, or a shorter since=", "pick a shorter time range"),
    (r"\bpass limit= to read more", "pick a shorter time range"),
)]


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (errors('etl') -> "the Errors tab", logs('etl', request_id='8f5c...') -> "the Logs tab (run 8f5ce35b)"), and the
    arguments it names become what they do; other calls and AWS CLI commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        run = re.search(r"request_id='([\w-]+)'", call)
        if name == "logs" and run:
            return f"run {run.group(1)[:8]} in the Logs tab"
        return _IN_WINDOW.get(name, call)

    text = str(text or "")
    for pattern, plain in _WINDOW_WORDS:
        text = pattern.sub(plain, text)
    text = _CALL_RE.sub(swap, text)
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], text)


_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(the [A-Z]\w* tab|the region field|run [0-9a-f]{8} in the Logs tab)")


def _seconds(since: str) -> float:
    """
    How far back a time range reaches, in seconds: '1h' -> 3600, '7d' -> 604800, a date -> the time since it.
    """
    start = parse_time(since)
    return (_utcnow() - start).total_seconds() if start is not None else 0.0


def _short_day(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}"


def _run_clock(moment: datetime, today: date) -> str:
    """
    A run's start in the zone reports show times in: '14:02:31' today, 'Oct 5 14:02' another day.
    """
    moment = _in_zone(moment)
    return moment.strftime("%H:%M:%S") if moment.date() == today else f"{_short_day(moment)} {moment:%H:%M}"
