"""Prove the Day 8 scoring settings are what they claim to be.

Two separate questions, and only the second is unusual.

The first is what the values ARE. A one-line `python -c` was supposed
to answer this and printed nothing at all -- no values, no error, no
traceback. That is not a pass, it is an unexplained silence. Reading
the values from a file removes shell quoting from the list of
suspects, which is the whole point: two explanations for one
observation means the checking is not finished.

The second is whether the weights validator CAN fire. A run in which
it does not raise proves only that it did not raise. It does not
prove the check works. A model_validator with a mistyped decorator,
or one that is never invoked, produces output identical to a healthy
one -- silent, passing, and useless. So this deliberately constructs
a Settings with a broken weight and requires the ValueError. An
assertion that has never fired against its own model is not known to
be an assertion.

Same reasoning as pack.ps1 -SelfTest, and the same reasoning behind
every isolate script in scripts/: build the thing that separates the
causes instead of guessing between them.

NOTHING here touches the network, the database, or .env's contents.
It reads named scalar settings only -- never the Settings object as a
whole, whose repr carries all five credentials.

Run with:  python -m scripts.config_selftest
"""

from __future__ import annotations

from app.core.config import Settings, settings


def main() -> None:
    print("--- values ---")
    print("weight_skill      ", settings.weight_skill)
    print("weight_semantic   ", settings.weight_semantic)
    print("weight_experience ", settings.weight_experience)
    print("weight_location   ", settings.weight_location)
    print("weight_title      ", settings.weight_title)

    total = (
        settings.weight_skill
        + settings.weight_semantic
        + settings.weight_experience
        + settings.weight_location
        + settings.weight_title
    )
    print("sum               ", repr(total))
    print("sum == 1.0        ", total == 1.0)

    print("weights_version   ", settings.weights_version)
    print("anchor_low        ", settings.semantic_anchor_low)
    print("anchor_high       ", settings.semantic_anchor_high)
    print("notify_floor      ", settings.semantic_notify_floor)
    print("min_coverage      ", settings.min_weight_covered_to_notify)
    print("taper_years       ", settings.experience_taper_years)
    print("mult_agency       ", settings.quality_multiplier_agency)
    print("mult_no_city      ", settings.quality_multiplier_no_city)
    print("enrich_delay      ", settings.enrichment_seconds_between_calls)

    print("--- list settings ---")
    print("agencies          ", len(settings.staffing_agency_list))
    print("agency names      ", sorted(settings.staffing_agency_list))
    print("weak tokens       ", len(settings.title_weak_token_set))

    print("--- rescale check ---")
    # The boundary cases, printed rather than reasoned about. 0.5058
    # and 0.6928 are the observed min and max over one AI/ML CV
    # against all 99 stored jobs; 0.62 is the notify floor; 0.70 is
    # anchor_high itself, which must map to 1.0 WITHOUT being counted
    # as a clamp, since (0.70-0.50)/(0.70-0.50) is 1.0 by arithmetic.
    low = settings.semantic_anchor_low
    high = settings.semantic_anchor_high
    for raw in (0.5058, 0.62, 0.6928, 0.70, 0.50):
        score = max(0.0, min(1.0, (raw - low) / (high - low)))
        print(
            f"raw={raw:<7} score={score:.4f} "
            f"points={score * settings.weight_semantic * 100:5.2f}/20 "
            f"clamp_hi={raw > high} clamp_lo={raw < low}"
        )

    print("--- notify floor boundary ---")
    # Written with >=, so exactly the floor CLEARS it. Day 6's
    # `median < 500` check stayed silent when the median was exactly
    # 500; the boundary is the case that fails while looking like it
    # should pass.
    floor = settings.semantic_notify_floor
    for raw in (0.6199, 0.62, 0.6201):
        print(f"raw={raw} clears={raw >= floor}")

    print("--- validator self-test ---")
    # Deliberately broken: 0.50 + 0.20 + 0.20 + 0.15 + 0.15 = 1.20.
    # Only the exception's own first line is printed. It contains the
    # computed total and nothing else -- no field values, no object.
    try:
        Settings(weight_skill=0.50)
    except ValueError as exc:
        first_line = str(exc).splitlines()[0]
        print("validator FIRED (correct):", first_line[:160])
    else:
        print("validator DID NOT FIRE -- the check is not working")


if __name__ == "__main__":
    main()
