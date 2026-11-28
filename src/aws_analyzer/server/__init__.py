"""
aws-analyzer in a browser tab: `aws-analyzer serve` in a terminal starts a web server on this machine and prints the
address to open. On SageMaker that's the notebook's own address with /proxy/<port>/ after it, so it's behind the same
sign-in; elsewhere it's http://127.0.0.1:<port>/.

The page runs the same commands the notebook does, on the same Views, and shows the same reports, with more room:
pick a service and a command, fill in its form (or type the call as you would in a cell), and a report's next steps
run with a click. It only reads, like the Views it runs: commands that write files or change something stay in the
notebook.

    catalog   the services and the commands the page offers, from the Views (pure)
    runner    ReportRunner: one View per service, a command's report as HTML
    links     the address to open, from SageMaker's metadata file
    app       the FastAPI app (FastAPI and uvicorn come with aws-analyzer)
    assets    the page's built files, from static/dist/manifest.json
    ping      a check of what the notebook's proxy passes (for now)
    cli       the `aws-analyzer serve` command
    static/   the page itself: index.html, dist/ (built from web/, Preact and TypeScript) and the services' icons

Importing this package loads none of that: FastAPI loads when the server starts.
"""

from __future__ import annotations
