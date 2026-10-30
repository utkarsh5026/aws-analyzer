"""
The dataclasses LambdaAnalyzer returns: functions and their triggers, metrics, logs and runs, errors,
performance, the deployment package, what a search of its code found and the map of its code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .._kit.deps import _require

from .helpers import MEMORY_PER_VCPU, _ASYNC_KINDS, _SHORT_KINDS, _in_days

if TYPE_CHECKING:
    from .analysis.codemap.model import AwsCall


@dataclass
class RuntimeStatus:
    """
    Where a runtime stands in AWS's support schedule.
    """

    runtime: str | None
    state: str  # 'supported' | 'ending' (within ENDING_SOON_DAYS) | 'deprecated' | 'blocked' | 'image' | 'unknown'
    deprecated: date | None = None  # end of support: no more security patches
    block_create: date | None = None  # no new functions on it from this day
    block_update: date | None = None  # no updates to functions on it from this day
    days_left: int | None = None  # until end of support (negative: since)
    upgrade: str | None = None  # the newest runtime for the same language

    @property
    def label(self) -> str:
        """
        'python3.9 (ended)', 'python3.10 (ends in 26 days)', 'python3.12', 'container image'.
        """
        if self.state == "image":
            return "container image"
        name = self.runtime or "-"
        if self.state in ("deprecated", "blocked"):
            return f"{name} (ended)"
        if self.state == "ending" and self.days_left is not None:
            return f"{name} (ends {_in_days(self.days_left)})"
        return name

    @property
    def tone(self) -> str:
        return {"deprecated": "bad", "blocked": "bad", "ending": "warn"}.get(self.state, "")


@dataclass
class Layer:
    arn: str
    code_size: int | None = None

    @property
    def name(self) -> str:
        """
        'arn:aws:lambda:us-east-1:123456789012:layer:pandas:7' -> 'pandas:7'.
        """
        parts = self.arn.split(":")
        return ":".join(parts[-2:]) if len(parts) >= 8 else self.arn


@dataclass
class Function:
    """
    One function's configuration (ListFunctions / GetFunction), in plain Python.
    """

    name: str
    arn: str = ""
    region: str = ""
    runtime: str | None = None  # None for a container image
    handler: str | None = None
    description: str = ""
    memory: int = 128  # MB; it also sets the CPU
    timeout: int = 3  # seconds
    architecture: str = "x86_64"  # or 'arm64'
    package_type: str = "Zip"  # or 'Image'
    code_size: int = 0  # bytes of the deployment package (zipped)
    last_modified: datetime | None = None
    ephemeral_storage: int = 512  # MB of /tmp
    layers: list[Layer] = field(default_factory=list)
    env_names: list[str] = field(default_factory=list)  # environment variable names; values are never read in
    env_error: str | None = None  # why Lambda couldn't decrypt them, if it couldn't
    role: str | None = None  # the execution role: what the code is allowed to do
    vpc_id: str | None = None
    subnets: list[str] = field(default_factory=list)
    security_groups: list[str] = field(default_factory=list)
    tracing: str | None = None  # 'Active' (X-Ray) or 'PassThrough'
    snapstart: bool = False
    state: str | None = None  # 'Active', 'Pending', 'Inactive', 'Failed'
    state_reason: str | None = None
    last_update: str | None = None  # 'Successful', 'InProgress', 'Failed'
    last_update_reason: str | None = None
    log_group: str = ""
    log_format: str | None = None  # 'Text' or 'JSON'
    log_level: str | None = None  # the application log level (JSON logs only)
    dead_letter: str | None = None  # SQS queue or SNS topic for failed asynchronous events
    kms_key: str | None = None  # a customer key for its environment variables
    file_systems: list[str] = field(default_factory=list)  # 'EFS access point -> /mnt/data'
    image_uri: str | None = None
    version: str = "$LATEST"
    reserved_concurrency: int | None = None  # GetFunction only: None means it shares the region's pool
    tags: dict[str, str] = field(default_factory=dict)  # GetFunction only
    code_location: str | None = field(default=None, repr=False)  # GetFunction only: a link to download the package
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # the configuration, environment values hidden
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def gb(self) -> float:
        return self.memory / 1024

    @property
    def arm(self) -> bool:
        return self.architecture == "arm64"

    @property
    def vcpus(self) -> float:
        """
        The share of CPU its memory buys (Lambda gives a full vCPU at 1,769 MB).
        """
        return self.memory / MEMORY_PER_VCPU

    @property
    def layer_size(self) -> int:
        return sum(layer.code_size or 0 for layer in self.layers)


@dataclass
class Trigger:
    """
    Something that invokes a function: an event source mapping (a queue or stream Lambda reads), a resource
    policy statement (a service or account allowed to call it), or its function URL.
    """

    kind: str  # 'SQS queue', 'S3 bucket', 'EventBridge rule', 'API Gateway', 'Function URL', 'AWS account', ...
    source: str = ""  # its name: a queue, bucket, rule or account
    arn: str | None = None
    via: str = "event source mapping"  # or 'resource policy' / 'function URL'
    state: str | None = None  # event source mappings: 'Enabled', 'Disabled', ...
    detail: str = ""  # batch size, API route, auth type...
    last_result: str | None = None  # event source mappings over streams: 'OK' or 'PROBLEM: ...'
    public: bool = False  # anyone on the internet can invoke it this way
    statement: str | None = None  # the policy statement's ID (to remove it)
    principal: str | None = None
    unscoped: bool = False  # a service allowed without saying which of its resources (any account's)
    uuid: str | None = None  # the event source mapping's ID

    @property
    def short(self) -> str:
        """
        A few characters for a table cell: 'SQS', 'S3', 'URL (public)'.
        """
        label = _SHORT_KINDS.get(self.kind, self.kind)
        return f"{label} (public)" if self.public else label

    @property
    def asynchronous(self) -> bool:
        """
        Whether the source invokes the function asynchronously, so failed events go to its on-failure destination.
        """
        return self.kind in _ASYNC_KINDS


@dataclass
class DailyUsage:
    """
    One period of CloudWatch numbers for a function (a day, or an hour in short reports).
    """

    start: datetime
    invocations: float = 0.0
    errors: float = 0.0
    throttles: float = 0.0
    duration_sum: float = 0.0  # ms, all runs together
    duration_max: float | None = None  # ms
    concurrency: float | None = None  # the most running at once

    @property
    def avg_duration(self) -> float | None:
        return self.duration_sum / self.invocations if self.invocations else None


@dataclass
class ChangeEffect:
    """
    How a function's calls went before and after it was last changed (its code or settings, `Function.last_modified`),
    from CloudWatch's daily numbers: the days before the change (at most a week), and the days since it.
    """

    changed: datetime
    calls_before: float
    errors_before: float
    calls_after: float
    errors_after: float
    days_before: int

    @property
    def rate_before(self) -> float:
        return self.errors_before / self.calls_before if self.calls_before else 0.0

    @property
    def rate_after(self) -> float:
        return self.errors_after / self.calls_after if self.calls_after else 0.0


@dataclass
class FunctionMetrics:
    """
    What CloudWatch counted for a function over a window: runs, failures, throttles, run time, log volume.
    """

    name: str
    days: float  # the window, in days
    period: int = 86400  # seconds per row of `daily`
    invocations: float = 0.0
    errors: float = 0.0
    throttles: float = 0.0
    duration_sum: float = 0.0  # ms, all runs together
    duration_max: float | None = None  # ms, the longest run
    concurrency_max: float | None = None  # the most running at once
    log_bytes: float | None = None  # bytes it sent to CloudWatch Logs (None: not read)
    daily: list[DailyUsage] = field(default_factory=list)  # oldest first; periods with no data are left out

    @property
    def avg_duration(self) -> float | None:
        return self.duration_sum / self.invocations if self.invocations else None

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.invocations if self.invocations else None

    def _last(self, attribute: str) -> datetime | None:
        return max((d.start for d in self.daily if getattr(d, attribute)), default=None)

    @property
    def last_invoked(self) -> datetime | None:
        """
        The start of the latest period with a run (a day, in daily numbers).
        """
        return self._last("invocations")

    @property
    def last_error(self) -> datetime | None:
        return self._last("errors")

    @property
    def last_throttle(self) -> datetime | None:
        return self._last("throttles")


@dataclass
class LogGroup:
    name: str
    retention_days: int | None = None  # None: logs are kept forever
    stored_bytes: int | None = None
    created: datetime | None = None
    log_class: str | None = None  # 'STANDARD' or 'INFREQUENT_ACCESS'


@dataclass
class ProvisionedConcurrency:
    """
    Copies of a version or alias kept started and ready, billed whether they're used or not.
    """

    qualifier: str  # the alias or version
    requested: int
    allocated: int = 0
    available: int = 0
    status: str = ""  # 'READY', 'IN_PROGRESS', 'FAILED'
    reason: str | None = None

    @property
    def billed(self) -> int:
        """
        The copies being paid for: what's allocated, or what was asked for while it's starting.
        """
        return self.allocated or (self.requested if self.status == "IN_PROGRESS" else 0)


@dataclass
class Alias:
    name: str
    version: str
    weights: dict[str, float] = field(default_factory=dict)  # other version -> its share of the traffic
    description: str = ""


@dataclass
class Version:
    version: str
    code_size: int = 0
    last_modified: datetime | None = None
    description: str = ""
    runtime: str | None = None
    code_sha: str = ""


@dataclass
class FunctionUrl:
    url: str
    auth_type: str  # 'NONE' (anyone with the URL) or 'AWS_IAM'
    cors_origins: list[str] = field(default_factory=list)
    invoke_mode: str | None = None  # 'BUFFERED' or 'RESPONSE_STREAM'


@dataclass
class AsyncConfig:
    """
    What happens to an event an asynchronous caller (S3, SNS, EventBridge) sent when the function fails it.
    """

    retries: int = 2  # Lambda's default
    max_age: int = 6 * 3600  # seconds an event may wait; Lambda's default
    on_success: str | None = None
    on_failure: str | None = None


@dataclass
class FunctionDetail:
    """
    Everything function_info() shows about one function. Sections that couldn't be read are in errors.
    """

    function: Function
    triggers: list[Trigger] = field(default_factory=list)
    url: FunctionUrl | None = None
    async_config: AsyncConfig | None = None  # None: not read
    versions: list[Version] = field(default_factory=list)  # published versions, oldest first
    aliases: list[Alias] = field(default_factory=list)
    provisioned: list[ProvisionedConcurrency] = field(default_factory=list)
    policy: dict[str, Any] | None = None  # the resource-based policy document
    log_group: LogGroup | None = None
    runtime_updates: str | None = None  # 'Auto', 'FunctionUpdate' or 'Manual'
    runtime_version: str | None = None  # the runtime version 'Manual' pins
    metrics: FunctionMetrics | None = None
    errors: dict[str, str] = field(default_factory=dict)  # section -> error code


@dataclass
class AccountLimits:
    """
    A region's Lambda limits and usage (GetAccountSettings), with the most functions CloudWatch saw running.
    """

    region: str
    concurrency: int | None = None  # how many runs may be in progress at once, across the region
    unreserved: int | None = None  # what's left for functions without reserved concurrency
    code_storage: int | None = None  # bytes of code stored, every version and layer
    code_storage_limit: int | None = None
    function_count: int | None = None
    peak_concurrency: float | None = None  # the most running at once in the window (CloudWatch)


@dataclass
class LogEvent:
    time: datetime
    message: str
    stream: str = ""
    request_id: str | None = None
    matched: bool = False  # it matched the search that found its run (log_runs(search=...))


@dataclass
class ErrorGroup:
    """
    Errors that share a cause: the same kind and message, once numbers and IDs are blanked out. `versions` are the
    versions of the function that logged them, newest first, read from their log streams' names ('$LATEST', '3').
    """

    kind: str  # 'Timeout', 'Out of memory', 'KeyError', 'Runtime.ImportModuleError', 'Logged error', ...
    message: str  # the message with numbers and IDs blanked out
    count: int
    first: datetime
    last: datetime
    example: str  # one full message
    request_ids: list[str] = field(default_factory=list)  # a few, newest first
    versions: list[str] = field(default_factory=list)


@dataclass
class CodeFrame:
    """
    A line of the function's deployment package that a traceback or stack trace names: its path in the package
    ('etl.py', 'lib/orders.js'), the line, the function it's in, and whether it's in a library the package bundles
    (requests, botocore, node_modules/...) rather than the function's own code.
    """

    path: str
    line: int
    function: str = ""
    library: bool = False


@dataclass
class ErrorReport:
    function: Function
    log_group: str
    since: datetime
    until: datetime
    groups: list[ErrorGroup] = field(default_factory=list)  # most frequent first
    newest: list[LogEvent] = field(default_factory=list)  # the newest error events, newest first
    errors_found: int = 0  # log events that look like errors
    truncated: bool = False
    covered_from: datetime | None = None
    metrics: FunctionMetrics | None = None  # CloudWatch over the same window
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Invocation:
    """
    One run, from the REPORT line Lambda logs at the end of it.
    """

    request_id: str
    time: datetime | None = None
    duration: float = 0.0  # ms
    billed: float = 0.0  # ms
    memory: int = 0  # MB it had
    max_memory: int = 0  # MB it used
    init: float | None = None  # ms of start-up before it: a cold start
    restore: float | None = None  # ms to restore a SnapStart snapshot
    status: str | None = None  # 'timeout' or 'error' when it didn't succeed (newer runtimes say)
    error_type: str | None = None  # 'Runtime.OutOfMemory', ...
    stream: str = ""  # the log stream it ran in (performance() fills it in), where the rest of its lines are


@dataclass
class Performance:
    """
    Run times and memory from the REPORT lines in a window.
    """

    function: Function
    log_group: str
    since: datetime
    until: datetime
    invocations: list[Invocation] = field(default_factory=list)  # newest first
    truncated: bool = False
    covered_from: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def cold_starts(self) -> list[Invocation]:
        return [i for i in self.invocations if i.init is not None]

    @property
    def window_days(self) -> float:
        """
        The days the invocations read cover (less than asked when the read stopped at the limit).
        """
        start = self.covered_from if self.truncated and self.covered_from else self.since
        return max((self.until - start).total_seconds() / 86400, 1 / 24)

    def to_df(self):
        pd = _require("pandas", "Performance.to_df()")
        return pd.DataFrame([vars(i) for i in self.invocations])


@dataclass
class CodeFile:
    path: str
    size: int  # unzipped
    compressed: int


@dataclass
class CodePackage:
    """
    A function's deployment package: the files in it, and the text of one of them.
    """

    function: Function
    size: int = 0  # bytes downloaded (zipped)
    files: list[CodeFile] = field(default_factory=list)
    handler_file: str | None = None  # the file Lambda loads the handler from
    shown_file: str | None = None
    source: str | None = None  # the shown file's text
    source_truncated: bool = False
    note: str | None = None  # why there's no code to show (a container image, a binary or secrets file)

    @property
    def unzipped(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class CodeMatch:
    """
    A line of a deployment package a search found: its file and line number, its text, and the lines around it as
    (number, text), itself included.
    """

    file: str
    line: int
    text: str
    context: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class CodeSearch:
    """
    What search_code() found, like grep -rn: the matching lines (up to its limit, in file order, the handler's file
    first), how many lines matched in all (`total`) and in how many files, how many files it read, and the files it
    left out with why (`skipped`: too big, binary). `note` says why nothing could be searched (a container image,
    no link to the code).
    """

    function: Function
    pattern: str
    regex: bool = False
    files: str | None = None
    matches: list[CodeMatch] = field(default_factory=list)
    total: int = 0
    files_matched: int = 0
    files_searched: int = 0
    skipped: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def truncated(self) -> bool:
        """
        Whether more lines matched than `matches` holds.
        """
        return self.total > len(self.matches)


@dataclass
class CodeSymbol:
    """
    Where a Python name is defined: its qualified name ('Orders.load'), its kind ('function', 'method', 'class' or
    'variable'), its file and first and last lines, its signature as written, its decorators and the first line of
    its docstring.
    """

    name: str
    kind: str
    file: str
    line: int
    end_line: int
    signature: str
    decorators: list[str] = field(default_factory=list)
    doc: str = ""


@dataclass
class SymbolUse:
    """
    A line that uses a name: its file and line number, its text, how it uses the name ('call', 'import' or
    'reference') and the function it's in ('(module)' at the top level).
    """

    file: str
    line: int
    text: str
    kind: str
    within: str


@dataclass
class SymbolReport:
    """
    What find_symbol() found, like a language server's go to definition and find references: where a name is
    defined, the lines that use it (up to its limit; `total_uses` counts them all), how many Python files it read,
    the files it couldn't read with why (`skipped`: a syntax error, too big), and `note` when there was nothing to
    read (not Python, a container image).
    """

    function: Function
    name: str
    definitions: list[CodeSymbol] = field(default_factory=list)
    uses: list[SymbolUse] = field(default_factory=list)
    total_uses: int = 0
    files_read: int = 0
    skipped: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class PackageSources:
    """
    The text of a deployment package's files, as sources() reads them: each file's text by its path in the
    package, the handler's file first, the files left out with why (`skipped`: too big, binary, past what's read in
    all), and `note` when there was nothing to read (a container image, no link to the code). Secrets files (.env,
    keys) are never among them.
    """

    function: Function
    texts: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class FileChange:
    """
    One file that differs between two versions of a function: `status` is 'added', 'removed' or 'changed', with its
    size on each side, the lines added and removed, the unified diff's lines (`lines`, without its --- / +++ header,
    cut short past a limit: `truncated`), the Python functions and classes whose lines changed (`definitions`,
    '(top level)' for code outside them) and `note` when its lines aren't compared (a binary, minified or secrets file).
    """

    path: str
    status: str
    old_size: int = 0
    new_size: int = 0
    added: int = 0
    removed: int = 0
    lines: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    truncated: bool = False
    note: str | None = None


@dataclass
class SettingChange:
    """
    A setting that differs between two versions, as people write it: ('Memory', '1,024 MB', '512 MB').
    """

    setting: str
    old: str
    new: str


@dataclass
class LibraryChange:
    """
    A library packed with the code that was added, removed or moved to another version: `old` / `new` are its
    versions, None on the side it isn't on.
    """

    name: str
    old: str | None
    new: str | None


@dataclass
class VersionDiff:
    """
    What changed between two versions of a function (diff()): `old` and `new` are their configurations, `files`
    the files of its own that differ (the handler's first), `libraries` the libraries packed with it that changed,
    with `library_files` counting their files that differ, `settings` and the environment variable names added and
    removed (values are never read), with `still_read` naming where the new code still reads a removed one
    ('app.py:4'), `broken` the Python files that read before and no longer do (path -> the SyntaxError's line), and
    `new_calls` the AWS calls a Python function's new code makes that the old one didn't, each with the IAM
    permission its role needs.
    `same_code` is true when both have the same SHA-256. `versions` are the
    function's published versions, `skipped` the ones passed over because their code was the same, and `note` says
    why there's nothing to compare (no published versions, a container image, no link to the code).
    """

    function: Function
    old: Function | None = None
    same_code: bool = False
    files: list[FileChange] = field(default_factory=list)
    unchanged_files: int = 0
    libraries: list[LibraryChange] = field(default_factory=list)
    library_files: int = 0
    settings: list[SettingChange] = field(default_factory=list)
    env_added: list[str] = field(default_factory=list)
    env_removed: list[str] = field(default_factory=list)
    still_read: dict[str, str] = field(default_factory=dict)
    broken: dict[str, str] = field(default_factory=dict)
    new_calls: list[AwsCall] = field(default_factory=list)
    versions: list[Version] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def added(self) -> int:
        return sum(change.added for change in self.files)

    @property
    def removed(self) -> int:
        return sum(change.removed for change in self.files)

    @property
    def truncated(self) -> bool:
        return any(change.truncated for change in self.files)


@dataclass
class PackageDownload:
    """
    A deployment package download() saved on the notebook's disk, as Lambda stores it (nothing unzipped or run).
    """

    function: Function
    path: str  # where it was saved (absolute)
    size: int  # bytes written: the .zip as Lambda stores it
    files: int | None = None  # files in the .zip (None when it isn't a readable .zip)
    sha256_matches: bool | None = None  # its SHA-256 against the CodeSha256 Lambda reports (None: Lambda gave none)
    replaced: bool = False  # a file of that name was there already, and this replaced it
    marked: bool = False  # the downloads folder was made now, with a .gitignore that keeps it out of git


@dataclass
class SavedPage:
    """
    A report how_it_works_page() saved on the notebook's disk as a page of its own, to open in a browser tab:
    where (`path`, absolute), its size in bytes, whether it replaced a file of that name, and whether the downloads
    folder was made now (`marked`, with a .gitignore that keeps it out of git).
    """

    function: Function
    path: str
    size: int
    replaced: bool = False
    marked: bool = False
