"""The code every service shares (aws_analyzer._kit): what the renderer does with a block nothing draws, the
stylesheet every report starts with, and what the base View and Analyzer do that no service shows in normal use
(an error none of them raises, a command no group lists, prices passed to one analyzer). Each service's tests cover
how its reports read."""

import itertools

import pytest
from botocore.exceptions import ClientError

from aws_analyzer import bedrock_chat, bedrock_kb, dynamodb, lambda_functions, opensearch, s3, sagemaker_env
from aws_analyzer._kit import view as viewmod
from aws_analyzer._kit.analyzer import _BaseAnalyzer
from aws_analyzer._kit.blocks import _Note, _Title
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.render import _base_css, _render_text, _report_html
from aws_analyzer._kit.view import _BaseView, _commands, _friendly_errors

# Each service: its module, Analyzer and View, and its list prices.
SERVICES = [
    (s3, s3.S3Analyzer, s3.S3View, s3.S3_PRICES),
    (dynamodb, dynamodb.DynamoDBAnalyzer, dynamodb.DynamoDBView, dynamodb.DYNAMODB_PRICES),
    (bedrock_kb, bedrock_kb.BedrockKBAnalyzer, bedrock_kb.BedrockKBView, bedrock_kb.BEDROCK_PRICES),
    (bedrock_chat, bedrock_chat.BedrockChatAnalyzer, bedrock_chat.BedrockChatView, bedrock_chat.BEDROCK_PRICES),
    (sagemaker_env, sagemaker_env.SageMakerAnalyzer, sagemaker_env.SageMakerView, sagemaker_env.SAGEMAKER_PRICES),
    (opensearch, opensearch.OpenSearchAnalyzer, opensearch.OpenSearchView, opensearch.OPENSEARCH_PRICES),
    (lambda_functions, lambda_functions.LambdaAnalyzer, lambda_functions.LambdaView, lambda_functions.LAMBDA_PRICES),
]


class _Mystery:  # a block no service registers a drawing for
    pass


class _Loud(_Note):  # a block made from another one
    pass


def test_a_block_nothing_draws_becomes_a_note():
    html = _report_html([_Title("Report"), _Mystery()], 0, "<style></style>", "ddb", "🗄️ DynamoDB")
    assert html.startswith('<style></style><div class="ddb"><h3><span class="badge">🗄️ DynamoDB</span>Report</h3>')
    assert ('<div class="note warn"><span class="ld">Part of this report isn&#x27;t shown:</span> there&#x27;s no way to '
            'draw a _Mystery') in html
    assert _render_text([_Mystery()], 0).startswith("[!] Part of this report isn't shown")


def test_a_block_made_from_another_is_drawn_like_it():
    assert _report_html([_Loud("Careful", "warn")], 0, "", "ddb", "") == (
        '<div class="ddb"><div class="note warn">Careful</div></div>')
    assert _render_text([_Loud("Careful", "warn")], 0) == "[!] Careful"


def test_base_css_restyles_only_a_rule_it_finds_once():
    assert _base_css("ddb") == dynamodb._CSS and _base_css("x").count(".x ") == 161
    css = _base_css("x", ".x .own{}", theme=".x{--c:red}", restyle={".pill{": ".x .pill{color:var(--c)}"},
                     after={"pre.code{": ".x pre.hl{}"})
    lines = css.split("\n")
    assert lines[:2] == ["<style>", ".x{--c:red}"] and lines[-2:] == [".x .own{}", "</style>"]
    assert ".x .pill{color:var(--c)}" in lines and lines[lines.index(".x pre.hl{}") - 1].startswith(".x pre.code{")
    with pytest.raises(ValueError, match="'.nope{' starts 0 of the shared CSS rules"):
        _base_css("x", restyle={".nope{": ".x .nope{}"})
    with pytest.raises(ValueError, match="'.track{' starts 2 of"):  # .track{display:..} and .track{vertical-align:..}
        _base_css("x", restyle={".track{": ".x .track{}"})


# ----------------------------------------------------------------------------- the base View


class _Core(_BaseAnalyzer):
    _LIST_PRICES = {"thing": 1.0}


class _Toy(_BaseView):
    """A View with one command that raises what it's given, and its own answers to the hooks."""

    _render_html = staticmethod(lambda blocks, max_rows: "")
    _PROGRESS = ("Toying", "toys")
    _DATA_ERRORS = (ValueError, OSError)
    _GROUPS = {"Help": ("help",)}

    def __init__(self, **options):
        super().__init__(mode="text", **options)
        self.core = _Core(region="us-east-1")

    def _explain_call(self, code, message, command, args, kwargs):
        return f"{message} ({command} was given {args[1:]} and {kwargs})"

    def _extra_note(self, exc, command):
        return _Note(f"Mine: {exc}  [{command}]", "warn") if isinstance(exc, LookupError) else None

    @_friendly_errors
    def fail(self, exc: BaseException, *args, **kwargs) -> None:
        """Raises exc."""
        raise exc


def shown(capsys, exc, *args, **kwargs):
    _Toy().fail(exc, *args, **kwargs)
    return capsys.readouterr().out.strip()


def test_friendly_errors_asks_the_view_what_each_error_means(capsys):
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetThing")
    assert shown(capsys, denied, "a", n=1) == "[!] AccessDenied: no (fail was given ('a',) and {'n': 1})  [fail]"
    assert shown(capsys, _Hint("Which one? Pass name=")) == "[i] Which one? Pass name="  # a question, not an error
    assert shown(capsys, ImportError("Drawing needs `x` (pip install x)")) == "[!] Drawing needs `x` (pip install x)."
    assert shown(capsys, OSError("disk full")) == "[!] OSError: disk full  [fail]"  # one of its _DATA_ERRORS
    assert shown(capsys, KeyError("k")) == "[!] Mine: 'k'  [fail]"  # its _extra_note
    for unknown in (TypeError("not one of its _DATA_ERRORS"), RuntimeError("a bug")):
        with pytest.raises(type(unknown)):
            _Toy().fail(unknown)


def test_help_lists_the_views_commands_and_its_own(capsys):
    assert [name for name in vars(_BaseView) if not name.startswith("_")] == ["help"]  # the base's only command
    assert list(_commands(_Toy)) == ["fail", "help"]  # the View's own first, then the base's
    _Toy().help()
    out = capsys.readouterr().out
    assert "_Toy commands" in out and "-- Help --\nCommand" in out and "-- Other --" in out and "fail(exc," in out
    _Toy().help("fial")
    assert "_Toy has no command 'fial'. Did you mean 'fail'?" in capsys.readouterr().out


def test_progress_says_the_views_words_unless_given_its_own(capsys, monkeypatch):
    clock = itertools.count()
    monkeypatch.setattr(viewmod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(viewmod, "_progress_bar_class", lambda notebook: None)  # no tqdm: the plain line
    with _Toy(progress="plain")._progress() as tick:
        tick(3000)
    assert "Toying... 3,000 toys" in capsys.readouterr().err
    with _Toy(progress="plain")._progress("Reading", unit="B") as tick:
        tick(2048)
    assert "Reading... 2.0 KB" in capsys.readouterr().err


# ----------------------------------------------------------------------------- shared state


@pytest.mark.parametrize("module, analyzer, view, list_prices", SERVICES, ids=[m.__name__ for m, *_ in SERVICES])
def test_prices_given_to_one_analyzer_change_nothing_else(module, analyzer, view, list_prices):
    # prices= is copied into the analyzer: the service's table, and every other analyzer's prices, stay as they were.
    before = {m.__name__: dict(table) for m, _, _, table in SERVICES}
    key = next(iter(list_prices))
    mine, other = analyzer(region="us-east-1", prices={key: 1234.5}), analyzer(region="us-east-1")
    mine.prices[key] = 99.0  # changing it afterwards doesn't reach the table either
    assert {m.__name__: dict(table) for m, _, _, table in SERVICES} == before
    assert other.prices == list_prices and other.prices is not list_prices and mine.prices[key] == 99.0
    assert "your prices" in view(mine, mode="text")._price_basis()
    assert "us-east-1 list prices" in view(other, mode="text")._price_basis()
    if hasattr(module, "MODEL_PRICES"):  # Bedrock: the models' prices too
        model = next(iter(module.MODEL_PRICES))
        custom = analyzer(region="us-east-1", model_prices={model: (1.0, 2.0)})
        assert module.MODEL_PRICES[model] != (1.0, 2.0) and other.model_prices == module.MODEL_PRICES
        assert view(custom, mode="text")._price_basis(models=True) == "your prices"
        assert view(other, mode="text")._price_basis(models=True) == "us-east-1 list prices"
