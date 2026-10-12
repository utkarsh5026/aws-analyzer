"""The code every analyzer shares, written once: each service module imports what it uses from these modules.

    fmt      numbers, sizes, money, times and counts: what users type in, and what reports show
    text     escaping for HTML, clipping and padding for text tables, and search words (`search_rank`, `_marked`)
    deps     optional packages (`_require`) and whether this is a notebook
    errors   AWS error codes, the permission a denied call needs, and `_Hint`
    blocks   the blocks a report is made of (`_Title`, `_Cards`, `_Table`, ...) and what draws each kind (`@_draws`)
    render   how reports are drawn: the stylesheet (`_base_css`), the shared blocks' HTML and text, `_render_text`
    progress the progress a long command shows: a tqdm bar, or a line with the rate and time left
    view     `_BaseView`, which every <Service>View subclasses (showing reports, progress, help()), and
             `@_friendly_errors`, which turns a command's errors into notes
    analyzer `_BaseAnalyzer`, which every <Service>Analyzer subclasses (session, prices, clients made once)
    highlight source code as HTML: Python and JSON with their tokens in colour
    window   what the notebook windows share: `_Window` and `_Explorer` (errors said in the window, AWS read in the
             background, the status line, tabs), `@_window_errors`, the searchable `_Picker`, `_row_button`, ...

Like the services, these load only the standard library and boto3 / botocore when imported, and they never import a
service module. Nothing here is re-exported: import each name from its own module.
"""
