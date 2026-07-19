"""Journey: record, replay, scenario — the run's opposable memory."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="journey",
    title="cdpx — record, replay, prove a journey",
    height=16,
    env={"FORM_NAME": "Leo", "E2E_FORM_NAME": "Leo"},
    forbidden=("Leo",),
    copies=(
        (
            "tests/fixtures/scenarios/static_form_pass.yml",
            "checkout.yml",
            {"__BASE_URL__": "{base}"},
        ),
    ),
    steps=(
        Comment("record: every executed action leaves a redacted NDJSON journal"),
        Cmd(
            argv=("record", "-o", "journey.ndjson", "--", "goto", "{base}/form.html"),
            display="cdpx record -o journey.ndjson -- goto {base}/form.html",
            expect=('"schema":"cdpx.record/v2"', '"recorded":1', '"replayable":true'),
        ),
        Comment("the secret never crosses the journal: @env:FORM_NAME, not the value"),
        Cmd(
            argv=(
                "record",
                "-o",
                "journey.ndjson",
                "--",
                "type",
                "#name",
                "@env:FORM_NAME",
                "--clear",
            ),
            display='cdpx record -o journey.ndjson -- type "#name" @env:FORM_NAME --clear',
            expect=('"recorded":1', '"replayable":true'),
        ),
        Cmd(
            argv=("record", "-o", "journey.ndjson", "--", "click", "#submit-btn"),
            display='cdpx record -o journey.ndjson -- click "#submit-btn"',
            expect=('"recorded":1', '"replayable":true'),
        ),
        Comment(
            "replay pre-validates the whole journal, then replays — stops at the first divergence"
        ),
        Cmd(
            argv=("--max-actions", "20", "replay", "journey.ndjson"),
            display="cdpx --max-actions 20 replay journey.ndjson",
            expect=('"events":3', '"played":3', '"ok":true'),
        ),
        Comment("scenario: the declarative business journey — verdict, assertions, archived proof"),
        Cmd(
            argv=("scenario", "run", "checkout.yml"),
            expect=('"verdict":"pass"', '"value_masked":true', '"text":"OK:***"'),
            timeout=120.0,
        ),
    ),
)
