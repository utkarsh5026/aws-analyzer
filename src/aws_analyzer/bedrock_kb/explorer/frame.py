"""
What the explorer window is drawn with: its tabs, stylesheet and colours, the sentences from
reports reworded for the window, and the faces of its lines.
"""

from __future__ import annotations

import re
from typing import Any

from ..._kit.fmt import _fmt_dt, human_age, human_size
from ..._kit.render import _CALL_RE
from ..._kit.text import _esc, _marked
from ..._kit.window import _icon_url

from ..helpers import file_type, human_duration
from ..models import FILE_STATES, IngestionJob, KBFile
from ..analysis.common import _reasons_text
from ..view.cells import _JOB_STATES


# ----------------------------------------------------------------------------- the explorer window

# The window's tabs, in order: (key, title, what it holds, the line drawing (24 x 24) shown before the title as a mask
# in the text's colour, so it follows the theme).
_EXPLORER_TABS = (
    ("overview", "Overview", "Health, the indexing pipeline and every data source",
     "<rect x='3.5' y='3.5' width='7' height='7' rx='1.6'/><rect x='13.5' y='3.5' width='7' height='4.5' rx='1.6'/>"
     "<rect x='13.5' y='11' width='7' height='9.5' rx='1.6'/><rect x='3.5' y='13.5' width='7' height='7' rx='1.6'/>"),
    ("files", "Files", "Every file and how it was indexed: click one",
     "<path d='M14 3.5H7.2a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9.6a2 2 0 0 0 2-2V8.3z'/><path d='M14 3.5v4.8h4.8'/>"
     "<path d='M8.8 13h6.4M8.8 16.5h4.4'/>"),
    ("syncs", "Syncs", "The sync history, and why syncs failed",
     "<path d='M19.5 12a7.5 7.5 0 0 1-13.2 4.9M4.5 12a7.5 7.5 0 0 1 13.2-4.9'/><path d='M18 3.6v3.6h-3.6'/>"
     "<path d='M6 20.4v-3.6h3.6'/>"),
    ("search", "Search", "What a question retrieves, file by file",
     "<circle cx='11' cy='11' r='6.5'/><path d='M20 20l-4.2-4.2'/>"),
    ("settings", "Settings", "Every setting in plain English, and as AWS returns it",
     "<path d='M4 7.5h9M17.5 7.5H20M4 16.5h2.5M11 16.5h9'/><circle cx='15' cy='7.5' r='2.5'/>"
     "<circle cx='8.5' cy='16.5' r='2.5'/>"),
)

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 5.5A2 2 0 0 1 6.5 3.5h11v14h-11a2 2 0 0 0-2 '
         '2z"/><path d="M4.5 19.5a2 2 0 0 0 2 2h11v-4"/><path d="M9 8h5M9 11h3"/></svg>')

_TYPE_TONES = {"pdf": "r", "doc": "b", "docx": "b", "xls": "g", "xlsx": "g", "csv": "g", "html": "o", "htm": "o",
               "md": "p", "markdown": "p", "txt": "", "png": "v", "jpg": "v", "jpeg": "v"}  # a file type's badge colour

_FILE_PAGE = 40  # files on a page of the list; « ‹ › » move between pages

_CHIP_LABELS = {"changed": "Changed", "new": "Not synced", "skipped": "Skipped", "deleted": "Deleted",
                "partial": "Partly indexed"}  # FILE_STATES' labels, shorter, for the list's chips

_RESULTS = (5, 10, 20, 50)  # the Search tab's choices of passages

_FIELD_ROLES = {"vectorField": "Its vector", "textField": "Its text", "metadataField": "Bedrock's metadata on it",
                "primaryKeyField": "Its ID", "customMetadataField": "Your metadata attributes"}  # fieldMapping's keys


_STATE_COLOURS = {"failed": "#ef4444", "changed": "#f59e0b", "new": "#f97316", "skipped": "#a855f7",
                  "deleted": "#ec4899", "partial": "#eab308", "ignored": "#94a3b8", "indexing": "#3b82f6",
                  "unchecked": "#cbd5e1", "indexed": "#10b981"}  # a file state's colour on dots and chips, as in reports

_STATE_INKS = {"failed": ("#dc2626", "#f87171"), "changed": ("#b45309", "#fbbf24"), "new": ("#c2410c", "#fb923c"),
               "skipped": ("#7e22ce", "#c084fc"), "deleted": ("#be185d", "#f472b6"), "partial": ("#a16207", "#facc15"),
               "ignored": ("#64748b", "#94a3b8"), "indexing": ("#1d4ed8", "#60a5fa"),
               "unchecked": ("#64748b", "#94a3b8"), "indexed": ("#047857", "#34d399")}  # its name's colour: light, dark


def _explorer_rules() -> str:
    """
    The explorer's tab icons (.kbx-i-<key> sets --kx-icon, which a tab's ::before draws), and each file state's
    colour on the list's dots and chips.
    """
    icons = [f".kbx-app .kbx-i-{key}{{--kx-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS]
    states = [f".kbx-app .dot.s-{state},.kbx-app.kbx-app button.kbx-chip.kbx-s-{state}::before{{background:{colour}}}"
              for state, colour in _STATE_COLOURS.items()]
    inks = [".kbx-app{" + ";".join(f"--kx-s-{state}:{light}" for state, (light, _) in _STATE_INKS.items()) + "}"]
    inks += [f".kbx-app .sl.s-{state}{{color:var(--kx-s-{state})}}" for state in _STATE_INKS]
    return "\n".join(icons + states + inks)


_EXPLORER_DARK = ("--kx-accent:#60a5fa;--kx-accent-2:#a78bfa;--kx-soft:rgba(96,165,250,.15);--kx-ring:rgba(96,165,250,"
                  ".38);--kx-raised:rgba(255,255,255,.11);--kx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--kx-ink-bad:#f87171;--kx-ink-warn:#fbbf24;--kx-ink-ok:#34d399"
                  + "".join(f";--kx-s-{state}:{dark}" for state, (_, dark) in _STATE_INKS.items()))

_EXPLORER_CSS = """<style>
.kbx-app{--kx-accent:#2563eb;--kx-button:#2563eb;--kx-accent-2:#7c3aed;--kx-soft:rgba(37,99,235,.10);--kx-ring:rgba(37,99,235,.28);--kx-line:rgba(127,127,127,.22);--kx-line-2:rgba(127,127,127,.36);--kx-tint:rgba(127,127,127,.06);--kx-tint-2:rgba(127,127,127,.11);--kx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--kx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--kx-raised:var(--kx-surface);--kx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--kx-ok:#10b981;--kx-warn:#f59e0b;--kx-bad:#ef4444;--kx-ink-bad:#dc2626;--kx-ink-warn:#b45309;--kx-ink-ok:#047857}
body[data-jp-theme-light="false"] .kbx-app,body.vscode-dark .kbx-app,body.vscode-high-contrast .kbx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .kbx-app{""" + _EXPLORER_DARK + """}}
.kbx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--kx-line);border-radius:18px;padding:14px 16px 10px;background:var(--kx-bg);box-shadow:var(--kx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.kbx-app *{box-sizing:border-box}
.kbx-app .widget-html-content,.kbx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.kbx-app.kbx-app .kbx-flat>*{margin:0}
.kbx-app.kbx-app button.jupyter-button{color:inherit;background:var(--kx-tint);border:1px solid var(--kx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.kbx-app.kbx-app button.jupyter-button:hover:enabled{background:var(--kx-tint-2);border-color:var(--kx-line-2);box-shadow:none}
.kbx-app.kbx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.kbx-app.kbx-app button.jupyter-button:focus-visible{outline:2px solid var(--kx-ring);outline-offset:-1px}
.kbx-app.kbx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.kbx-app.kbx-app button.jupyter-button.mod-primary{background:var(--kx-button);border-color:transparent;color:#fff;font-weight:600}
.kbx-app.kbx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--kx-button);filter:brightness(1.08)}
.kbx-app.kbx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.kbx-app.kbx-app .widget-text input,.kbx-app.kbx-app .jupyter-widget-text input,.kbx-app.kbx-app .widget-dropdown>select,.kbx-app.kbx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--kx-line-2);border-radius:9px;background-color:var(--kx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.kbx-app.kbx-app .widget-dropdown>select,.kbx-app.kbx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.kbx-app.kbx-app .widget-text input:focus,.kbx-app.kbx-app .jupyter-widget-text input:focus,.kbx-app.kbx-app .widget-dropdown>select:focus,.kbx-app.kbx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--kx-accent);box-shadow:0 0 0 3px var(--kx-soft)}
.kbx-app.kbx-app .widget-text,.kbx-app.kbx-app .widget-dropdown,.kbx-app.kbx-app .jupyter-widget-text,.kbx-app.kbx-app .jupyter-widget-dropdown{margin:0;height:auto}
.kbx-app.kbx-app .widget-toggle-buttons,.kbx-app.kbx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--kx-tint-2);gap:2px;flex:0 0 auto;margin:0}
.kbx-app.kbx-app .widget-toggle-buttons .widget-toggle-button,.kbx-app.kbx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.kbx-app.kbx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.kbx-app.kbx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--kx-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.kbx-app.kbx-app .widget-checkbox input[type=checkbox],.kbx-app.kbx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--kx-accent)}
.kbx-app.kbx-app .kbx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--kx-line);overflow:visible}
.kbx-app.kbx-app .kbx-top{align-items:center;gap:12px;overflow:visible}
.kbx-app.kbx-app .kbx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.kbx-app .kbx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.kbx-app .kbx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--kx-accent),var(--kx-accent-2));box-shadow:0 2px 8px var(--kx-ring);flex:0 0 auto}
.kbx-app .kbx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbx-app .kbx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.kbx-app .kbx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbx-app .kbx-stats{display:flex;flex-wrap:wrap;gap:8px}
.kbx-app .kbx-stat{border:1px solid var(--kx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--kx-tint)}
.kbx-app .kbx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.kbx-app .kbx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.kbx-app .kbx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.kbx-app .kbx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.kbx-app .kbx-stat.ok{border-color:rgba(16,185,129,.55)}
.kbx-app .kbx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--kx-tint-2) 30%,var(--kx-line) 50%,var(--kx-tint-2) 70%);animation:kbx-glow 1.3s ease-in-out infinite}
.kbx-app.kbx-app .kbx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.kbx-app.kbx-app .kbx-field.kbx-open{z-index:41}
.kbx-app.kbx-app .kbx-trig{position:relative;min-height:52px;overflow:visible}
.kbx-app.kbx-app .kbx-trig>.kbx-trig-b,.kbx-app.kbx-app .kbx-opt>.kbx-opt-b,.kbx-app.kbx-app .kbx-row>.kbx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.kbx-app.kbx-app .kbx-trig>.kbx-trig-b{border-color:var(--kx-line-2);border-radius:12px;background:var(--kx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.kbx-app.kbx-app .kbx-trig>.kbx-trig-b:hover:enabled{border-color:var(--kx-accent);background:var(--kx-surface)}
.kbx-app.kbx-app .kbx-open .kbx-trig>.kbx-trig-b{border-color:var(--kx-accent);box-shadow:0 0 0 3px var(--kx-soft)}
.kbx-app.kbx-app .kbx-trig>.kbx-trig-b:active:enabled,.kbx-app.kbx-app .kbx-opt>.kbx-opt-b:active:enabled,.kbx-app.kbx-app .kbx-row>.kbx-row-b:active:enabled{transform:none}
.kbx-app.kbx-app .kbx-trig>.kbx-face,.kbx-app.kbx-app .kbx-opt>.kbx-opt-t,.kbx-app.kbx-app .kbx-row>.kbx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.kbx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.kbx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.kbx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.kbx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.kbx-app .fxi,.kbx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.kbx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.kbx-app .kbx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--kx-accent)}
.kbx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.kbx-app .dot.ok{background:var(--kx-ok)}
.kbx-app .dot.warn{background:var(--kx-warn)}
.kbx-app .dot.bad{background:var(--kx-bad)}
.kbx-app.kbx-app .kbx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--kx-line-2);border-radius:14px;background:var(--kx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.kbx-app.kbx-app .kbx-pop>*{margin:0}
.kbx-app.kbx-app .kbx-pop .kbx-x{margin-left:6px}
.kbx-app.kbx-app .kbx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.kbx-app.kbx-app .kbx-opts>*{flex:0 0 auto}
.kbx-app.kbx-app .kbx-opt{position:relative;margin:0 0 2px;overflow:visible}
.kbx-app.kbx-app .kbx-opt>.kbx-opt-b{border-radius:10px}
.kbx-app.kbx-app .kbx-opt>.kbx-opt-b:hover:enabled{background:var(--kx-tint-2)}
.kbx-app.kbx-app .kbx-opt.kbx-on>.kbx-opt-b{background:var(--kx-soft);border-color:var(--kx-ring)}
.kbx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.kbx-app .op .dot{margin-top:6px}
.kbx-app .opb{flex:1 1 auto;min-width:0}
.kbx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.kbx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.kbx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--kx-line);line-height:1.4}
.kbx-app .opf.warn{opacity:1;color:var(--kx-ink-warn)}
.kbx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.kbx-app.kbx-app .kbx-backdrop,.kbx-app.kbx-app .kbx-backdrop:hover:enabled,.kbx-app.kbx-app .kbx-backdrop:active:enabled,.kbx-app.kbx-app .kbx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.kbx-app.kbx-app .kbx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--kx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.kbx-app.kbx-app button.kbx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.kbx-app.kbx-app button.kbx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--kx-icon) center/contain no-repeat;mask:var(--kx-icon) center/contain no-repeat}
.kbx-app.kbx-app button.kbx-tab:hover:enabled{background:var(--kx-tint);opacity:.95}
.kbx-app.kbx-app button.kbx-tab.kbx-on,.kbx-app.kbx-app button.kbx-tab.kbx-on:hover:enabled{background:var(--kx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.kbx-app.kbx-app button.kbx-tab.kbx-on::before{background:var(--kx-accent)}
.kbx-app.kbx-app button.kbx-tab.kbx-alert::after,.kbx-app.kbx-app button.kbx-tab.kbx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--kx-warn);flex:0 0 auto}
.kbx-app.kbx-app button.kbx-tab.kbx-alarm::after{background:var(--kx-bad)}
@container (max-width:520px){.kbx-app.kbx-app button.kbx-tab::before{display:none}}
.kbx-app.kbx-app .kbx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .kbx-app.kbx-app .kbx-page{height:620px}
.kbx-app.kbx-app .kbx-page>*{flex:0 0 auto}
.kbx-app.kbx-app .kbx-page.kbx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.kbx-app.kbx-app .kbx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--kx-line);border-radius:14px;padding:10px 10px 6px;background:var(--kx-surface);overflow:hidden}
.kbx-app.kbx-app .kbx-left>*{margin:0;flex:0 0 auto}
.kbx-app.kbx-app .kbx-left,.kbx-app.kbx-app .kbx-right,.kbx-app.kbx-app .kbx-rows{min-height:0}
@media (max-width:900px){.kbx-app.kbx-app .kbx-page.kbx-split{flex-wrap:wrap;overflow:hidden auto}.kbx-app.kbx-app .kbx-left{flex:1 1 100%;max-width:100%;height:440px}.kbx-app.kbx-app .kbx-right{flex:1 1 100%;overflow:visible}}
.kbx-app.kbx-app .kbx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.kbx-app.kbx-app .kbx-right>*{flex:0 0 auto;margin:0}
.kbx-app.kbx-app .kbx-find{position:relative;align-items:center}
.kbx-app.kbx-app .kbx-find input,.kbx-app.kbx-app .kbx-find input:focus{padding-left:32px;background:var(--kx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.kbx-app.kbx-app .kbx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.kbx-app.kbx-app .kbx-chips>*{margin:0}
.kbx-app.kbx-app button.kbx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--kx-line);display:inline-flex;align-items:center;gap:6px}
.kbx-app.kbx-app button.kbx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.kbx-app.kbx-app button.kbx-chip.kbx-t-all::before{display:none}
.kbx-app.kbx-app button.kbx-chip.kbx-t-bad::before{background:var(--kx-bad)}
.kbx-app.kbx-app button.kbx-chip.kbx-t-warn::before{background:var(--kx-warn)}
.kbx-app.kbx-app button.kbx-chip.kbx-t-ok::before{background:var(--kx-ok)}
.kbx-app.kbx-app button.kbx-chip.kbx-on,.kbx-app.kbx-app button.kbx-chip.kbx-on:hover:enabled{background:var(--kx-soft);border-color:var(--kx-ring);color:var(--kx-accent);font-weight:650}
.kbx-app.kbx-app .kbx-filters{gap:6px;margin:8px 0 0;align-items:center}
.kbx-app.kbx-app .kbx-filters>*{margin:0}
.kbx-app.kbx-app .kbx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.kbx-app.kbx-app .kbx-rows>*{flex:0 0 auto}
.kbx-app.kbx-app .kbx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.kbx-app.kbx-app .kbx-row>.kbx-row-b{border-radius:10px}
.kbx-app.kbx-app .kbx-row>.kbx-row-b:hover:enabled{background:var(--kx-tint-2)}
.kbx-app.kbx-app .kbx-row.kbx-on>.kbx-row-b,.kbx-app.kbx-app .kbx-row.kbx-on>.kbx-row-b:hover:enabled{background:var(--kx-soft);border-color:var(--kx-ring)}
.kbx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.kbx-app .ft{position:relative;flex:0 0 auto;width:36px;height:24px;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;font-size:9.5px;font-weight:750;letter-spacing:.03em;background:var(--kx-tint-2)}
.kbx-app .ft.r{background:rgba(239,68,68,.13);color:var(--kx-ink-bad)}
.kbx-app .ft.b{background:rgba(59,130,246,.14);color:var(--kx-accent)}
.kbx-app .ft.g{background:rgba(16,185,129,.14);color:var(--kx-ink-ok)}
.kbx-app .ft.o{background:rgba(249,115,22,.14);color:#c2410c}
.kbx-app .ft.p{background:rgba(124,58,237,.12);color:var(--kx-accent-2)}
.kbx-app .ft.v{background:rgba(236,72,153,.12);color:#be185d}
.kbx-app .ft .dot{position:absolute;right:-3px;top:-3px;width:9px;height:9px;box-shadow:0 0 0 2px var(--kx-surface)}
.kbx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.kbx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.kbx-app .sl{font-weight:600}
.kbx-app .sl.bad{color:var(--kx-ink-bad)}
.kbx-app .sl.warn{color:var(--kx-ink-warn)}
.kbx-app .sl.ok{color:var(--kx-ink-ok)}
.kbx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.kbx-app .kbx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.kbx-app.kbx-app .kbx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--kx-line);margin-top:4px}
.kbx-app.kbx-app .kbx-pager>*{margin:0}
.kbx-app.kbx-app .kbx-saved{margin:2px 0 0}
.kbx-app .kbx-saved .kba .dlw{margin:4px 2px 2px;font-size:12px}
.kbx-app .kba a.dl{color:var(--kx-accent);border-color:var(--kx-ring)}
.kbx-app .kba a.dl:hover{background:var(--kx-soft)}
.kbx-app.kbx-app button.kbx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.kbx-app .kbx-pager .widget-html-content,.kbx-app .kbx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbx-app.kbx-app .kbx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--kx-bg);border-bottom:1px solid var(--kx-line);flex-wrap:wrap}
.kbx-app.kbx-app .kbx-bar>*{margin:0}
.kbx-app.kbx-app button.kbx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.kbx-app.kbx-app button.kbx-ghost{background:transparent;border-color:transparent}
.kbx-app.kbx-app button.kbx-ghost:hover:enabled{background:var(--kx-tint-2)}
.kbx-app.kbx-app button.kbx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.kbx-app.kbx-app button.kbx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--kx-ink-bad)}
.kbx-app.kbx-app .kbx-ask{flex:1 1 320px;align-items:center;gap:6px;padding:3px 3px 3px 6px;border:1px solid var(--kx-line-2);border-radius:999px;background:var(--kx-surface);transition:border-color .15s,box-shadow .15s}
.kbx-app.kbx-app .kbx-ask>*{margin:0}
.kbx-app.kbx-app .kbx-ask:focus-within{border-color:var(--kx-accent);box-shadow:0 0 0 3px var(--kx-soft)}
.kbx-app.kbx-app .kbx-ask .widget-text input,.kbx-app.kbx-app .kbx-ask .jupyter-widget-text input,.kbx-app.kbx-app .kbx-ask .widget-text input:focus,.kbx-app.kbx-app .kbx-ask .jupyter-widget-text input:focus{border:0;box-shadow:none;background:transparent;height:30px;font-size:13.5px}
.kbx-app.kbx-app .kbx-ask button.jupyter-button{border-radius:999px;height:30px;padding:0 16px}
.kbx-app.kbx-app .kbx-ask.kbx-big{flex:0 0 auto;padding:5px 5px 5px 10px;box-shadow:var(--kx-shadow);overflow:visible}
.kbx-app.kbx-app .kbx-ask.kbx-big .widget-text input,.kbx-app.kbx-app .kbx-ask.kbx-big .jupyter-widget-text input{height:34px;font-size:14px}
.kbx-app.kbx-app .kbx-ask.kbx-big button.jupyter-button{height:34px;padding:0 20px}
.kbx-app.kbx-app .kbx-options{gap:8px 12px;align-items:center;flex-wrap:wrap;margin:10px 0 4px}
.kbx-app.kbx-app .kbx-options>*{margin:0}
.kbx-app .kbx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.kbx-app.kbx-app .kbx-hits>*{flex:0 0 auto;margin:0}
.kbx-app.kbx-app .kbx-hit{border:1px solid var(--kx-line);border-radius:12px;padding:0 0 8px;margin:0 0 8px;background:var(--kx-surface);max-width:960px}
.kbx-app.kbx-app .kbx-hit>*{margin:0}
.kbx-app.kbx-app .kbx-hit button.kbx-link{align-self:flex-start;margin:0 12px;height:26px;padding:0 12px;border-radius:999px;font-size:12px;background:transparent;color:var(--kx-accent);border-color:var(--kx-ring);width:auto}
.kbx-app.kbx-app .kbx-hit button.kbx-link:hover:enabled{background:var(--kx-soft);border-color:var(--kx-accent)}
.kbx-app .kbx-hit .kba .psg{border:0;margin:0;max-width:none;padding:8px 12px 6px}
.kbx-app .kba h3{font-size:17px;margin:6px 0 2px}
.kbx-app .kba h3 .badge{display:none}
.kbx-app .kba .card{border-radius:12px;background:var(--kx-tint);border-color:var(--kx-line)}
.kbx-app .kba .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.kbx-app .kba .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.kbx-app .kba .card.ok{border-color:rgba(16,185,129,.55)}
.kbx-app .kba .note,.kbx-app .kba .fi{border-radius:4px 10px 10px 4px}
.kbx-app .kba details.ck,.kbx-app .kba .psg,.kbx-app .kba .pipe .ps{border-radius:10px;background:var(--kx-surface)}
.kbx-app .kba pre,.kbx-app .kba .json{border-radius:10px}
.kbx-app .kba a.fl{color:var(--kx-accent)}
.kbx-app .kbx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.kbx-app .kbx-status .st{opacity:.72}
.kbx-app .kbx-status .st.warn{opacity:1;color:var(--kx-ink-warn)}
.kbx-app .kbx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.kbx-app .kbx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--kx-ok)}
.kbx-app .kbx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--kx-tint-2);user-select:all;-webkit-user-select:all}
.kbx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--kx-accent);border-radius:50%;animation:kbx-spin .8s linear infinite}
@keyframes kbx-spin{to{transform:rotate(360deg)}}
.kbx-app .skw{padding:6px 2px}
.kbx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.kbx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--kx-tint) 30%,var(--kx-tint-2) 50%,var(--kx-tint) 70%);animation:kbx-glow 1.3s ease-in-out infinite}
.kbx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.kbx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.kbx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes kbx-glow{from{background-position:100% 0}to{background-position:0 0}}
.kbx-app .kbx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--kx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.kbx-app .kbx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.kbx-app *,.kbx-app *::before{transition:none!important;animation-duration:2.5s!important}}
""" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {  # a command a report names -> where the explorer window shows the same
    "documents": "the Files tab",
    "files": "the Files tab",
    "unsynced": "the Files tab's Changed since sync filter",
    "syncs": "the Syncs tab",
    "kb_info": "the Overview tab",
    "kbs": "the knowledge base list",
    "search": "the Search tab",
    "search_file": "the file's question box",
    "file": "the file's page",
}


_WINDOW_WORDS = [(re.compile(pattern), plain) for pattern, plain in (  # a command's arguments -> what they do
    (r"\bdata_source= or where=", "a data source or metadata filter"),
    (r"\bwithout data_source=", "with no data source picked"),
    (r"\bwhere= filters\b", "metadata filters"),
    (r"\bwhere=", "a metadata filter"),
    (r"\bsearch_type='HYBRID'", "hybrid search"),
    (r"\ba larger n=", "more passages"),
    (r"\bn=(\d+) passages", r"\1 passages"),
)]


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (documents(status='FAILED') -> "the Files tab's Failed filter"), and the arguments it names become what they do
    (where= filters -> metadata filters); other calls and AWS CLI commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        if name == "documents" and "FAILED" in call:
            return "the Files tab's Failed filter"
        return _IN_WINDOW.get(name, call)

    text = str(text or "")
    for pattern, plain in _WINDOW_WORDS:
        text = pattern.sub(plain, text)
    return _CALL_RE.sub(swap, text)


_PARSER_NAMES = {"BEDROCK_FOUNDATION_MODEL": "Foundation model", "BEDROCK_DATA_AUTOMATION": "Data Automation",
                 "SMART_PARSING": "Smart parsing", "MULTI_MODAL_EMBEDDINGS": "Multimodal embeddings"}


def _parser_name(cfg: dict[str, Any] | None) -> str:
    """
    parsingConfiguration in two words: 'Default (text only)', 'Foundation model', 'Data Automation'...
    """
    strategy = (cfg or {}).get("parsingStrategy")
    return _PARSER_NAMES.get(strategy, strategy) if strategy else "Default (text only)"


def _kb_tone(status: str) -> str:
    return {"ACTIVE": "ok", "FAILED": "bad", "DELETE_UNSUCCESSFUL": "bad"}.get(status or "", "warn")


def _sync_step(job: IngestionJob, names: dict[str, str]) -> tuple[str, str, str, str]:
    """
    A sync as a step of the Syncs tab's timeline: (its data source, what happened and when, what it read and
    changed and why it failed, its tone).
    """
    state = _JOB_STATES.get(job.status, job.status).lower()
    took = f" · took {human_duration(job.duration)}" if job.duration else ""
    counts = (f"read {job.scanned:,}: {job.new:,} new, {job.modified:,} changed, {job.deleted:,} deleted, "
              f"{job.failed:,} failed" + (f", {job.skipped:,} skipped" if job.skipped else ""))
    why = f" · {_reasons_text(job.failure_reasons, 2)}" if job.failure_reasons else ""
    tone = ("bad" if job.status == "FAILED" else "warn" if job.failed or job.status == "STOPPED"
            else "ok" if job.status == "COMPLETE" else "")
    return (names.get(job.data_source_id) or job.data_source_id, f"{state} · {_fmt_dt(job.started)}{took}",
            counts + why, tone)


def _file_face(f: KBFile, words: list[str], source: str = "") -> str:
    """
    A file's line in the list: its type with the state's dot on it, its name (search words marked), its folder and
    state, and its size and when it changed.
    """
    label, tone = FILE_STATES.get(f.state, (f.state or "?", ""))
    kind = file_type(f.uri)
    badge = (kind or "?")[:4].upper()
    where = " · ".join(p for p in (f.folder.rstrip("/") or ("/" if f.uri.startswith("s3://") else ""), source) if p)
    size = human_size(f.size) if f.size is not None else "-"
    age = human_age(f.modified) if f.modified else (human_age(f.indexed) if f.indexed else "")
    return (f'<div class="fr"><span class="ft {_TYPE_TONES.get(kind, "")}">{_esc(badge)}<span class="dot '
            f's-{_esc(f.state)}"></span></span><div class="fm"><div class="fn">{_marked(f.name, words)}</div>'
            f'<div class="ff"><span class="sl {tone} s-{_esc(f.state)}">{_esc(label)}</span>'
            + (f" · {_marked(where, words)}" if where else "") + f'</div></div><div class="fz">{_esc(size)}<br>'
            f"{_esc(age)}</div></div>")
