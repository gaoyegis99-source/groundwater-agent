from platform_knowledge_next.retrieval_budget import (
    additional_context_units,
    context_content_chars,
    fits_context_budget,
    required_context_ids,
)


def _unit(unit_id, required_units, content):
    return {
        "unit_id": unit_id,
        "body": {"required_units": required_units, "content": content},
    }


def test_required_context_ids_resolves_transitive_cycles_once():
    units = {
        ("v1", "root"): _unit("root", ["left", "right"], "r"),
        ("v1", "left"): _unit("left", ["shared"], "l"),
        ("v1", "right"): _unit("right", ["shared"], "r"),
        ("v1", "shared"): _unit("shared", ["left"], "s"),
    }

    assert required_context_ids(units, "v1", "root") == {
        "root", "left", "right", "shared"
    }


def test_additional_context_units_excludes_already_included_units():
    units = {
        ("v1", "root"): _unit("root", [], "root"),
        ("v1", "required"): _unit("required", [], "required"),
    }

    result = additional_context_units(
        units,
        "v1",
        {"root", "required"},
        {("v1", "required")},
    )

    assert result == [units[("v1", "root")]]
    assert context_content_chars(result) == 4


def test_context_budget_accepts_exact_limits_and_rejects_each_overflow():
    additions = [_unit("one", [], "123"), _unit("two", [], "45")]

    assert fits_context_budget(
        included_count=1,
        included_chars=4,
        additional_count=len(additions),
        additional_chars=5,
        max_units=3,
        max_chars=9,
    )
    assert not fits_context_budget(
        included_count=2,
        included_chars=4,
        additional_count=len(additions),
        additional_chars=5,
        max_units=3,
        max_chars=9,
    )
    assert not fits_context_budget(
        included_count=1,
        included_chars=5,
        additional_count=len(additions),
        additional_chars=5,
        max_units=3,
        max_chars=9,
    )
