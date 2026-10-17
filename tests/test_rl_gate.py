"""Publication gate: a failed, altered or hand-placed policy is never offered or selectable. No SUMO needed."""

import json

import pytest

from flowstate.rl import controllers as K


@pytest.fixture
def art(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "artifacts_dir", lambda: tmp_path)
    return tmp_path


def _publish(art, passed, sha=None):
    ckpt = art / "ppo_policy.zip"
    ckpt.write_bytes(b"not a real model")
    (art / "validation.json").write_text(json.dumps({"passed": passed, "reason": "r", "checkpoint_sha256": sha or K.file_sha256(ckpt)}))


def test_no_checkpoint_means_unavailable_and_heuristic_runs(art):
    ok, why = K.rl_status()
    assert not ok and "heuristic" in why
    ctl, info = K.make("rl_ppo")
    assert info["fallback"] and info["used"] == "queue_heuristic" and ctl.id == "queue_heuristic"


def test_checkpoint_without_gate_is_not_offered(art):
    (art / "ppo_policy.zip").write_bytes(b"x")
    assert not K.rl_status()[0]


def test_failed_gate_is_not_offered(art):
    _publish(art, passed=False)
    ok, why = K.rl_status()
    assert not ok and "failed" in why
    assert K.make("rl_ppo")[1]["fallback"]
    assert K.available()["rl_ppo"]["available"] is False


def test_passed_gate_for_a_different_file_is_not_offered(art):
    _publish(art, passed=True, sha="0" * 64)  # the file on disk is not the one that was validated
    ok, why = K.rl_status()
    assert not ok and "does not match" in why


def test_passed_gate_matching_checkpoint_is_offered(art):
    _publish(art, passed=True)
    assert K.rl_status() == (True, "validated")
    assert K.available()["rl_ppo"]["available"] is True


def test_default_controller_is_the_heuristic_unless_the_policy_passed(art):
    assert K.default_controller() == "queue_heuristic"
    _publish(art, passed=False)
    assert K.default_controller() == "queue_heuristic"
    assert K.available()["rl_ppo"]["status"] == "experimental"
    _publish(art, passed=True)
    assert K.default_controller() == "rl_ppo"
    assert K.available()["rl_ppo"]["status"] == "verified"
    _publish(art, passed=True, sha="f" * 64)  # tampered / swapped file
    assert K.default_controller() == "queue_heuristic"


def test_catalog_exposes_the_default(art):
    from flowstate.rl.service import catalog

    c = catalog()
    assert c["default_controller"] == "queue_heuristic"
    assert {x["id"]: x["status"] for x in c["controllers"]}["rl_ppo"] == "experimental"


@pytest.mark.parametrize("cid", ["rl_ppo@/etc/passwd", "rl_ppo@artifacts/rl/run3/best_validation.zip", "queue_heuristic@9,9,9", "fixed_time@5"])
def test_the_api_cannot_select_unvalidated_checkpoints_or_tuning_ids(art, cid):
    """The explicit-path ids exist for offline experiments; the API accepts catalog ids only (PPO.load unpickles)."""
    from pydantic import ValidationError

    from flowstate.rl.service import CompareRequest

    with pytest.raises(ValidationError):
        CompareRequest(controllers=["fixed_time", cid])


def test_requesting_rl_ppo_through_the_api_runs_the_heuristic_and_says_so(art):
    from flowstate.rl.service import CompareRequest

    req = CompareRequest(controllers=["fixed_time", "rl_ppo"])  # accepted (listed, flagged experimental) ...
    assert req.controllers == ["fixed_time", "rl_ppo"]
    _, info = K.make("rl_ppo")  # ... but it resolves to the heuristic with a visible fallback
    assert info["fallback"] and info["used"] == "queue_heuristic"
