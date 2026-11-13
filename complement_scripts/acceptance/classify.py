from collections import Counter

TECHNICAL = {
    "Session timed out": "timeout",
    "Testing system is busy": "system_busy",
    "Session ended: page_unload": "page_unload",
    "Session replaced by a new attempt": "replaced_attempt",
    "Missing enrolled fingerprint baseline": "missing_enrollment_baseline",
    "Audio and Canvas verification did not both complete": "incomplete_verification",
}


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "percent": numerator / denominator * 100 if denominator else None}


def fmt_ratio(value):
    return "N/A (0 eligible)" if not value["denominator"] else f"{value['numerator']}/{value['denominator']} ({value['percent']:.2f}%)"


def exclude(event):
    error = str(event.get("error") or "").strip()
    if error.casefold() == "incorrect password":
        return "wrong_password", "Incorrect password"
    if error in TECHNICAL:
        return "technical", TECHNICAL[error]
    if event.get("result") == "error":
        lowered = error.casefold()
        category = next((name for words, name in [
            (("timeout", "timed out"), "timeout"), (("busy",), "system_busy"),
            (("fetch", "network", "connection"), "network_error"),
            (("unload",), "page_unload"), (("cancel", "manual"), "cancelled_session")]
            if any(word in lowered for word in words)), "other_technical_error")
        return "technical", category
    if event.get("result") not in {"success", "failure"}:
        return "technical", "unrecognized_final_result"
    return None, None


def context_evidence(event, platforms):
    flags, baseline_seeds, actual_seeds = [], set(), set()
    for modality in ("canvas", "audio"):
        item = event.get(modality, {})
        actual, baseline = item.get("seed") or event.get("seed"), item.get("baseline_seed")
        if actual:
            actual_seeds.add(actual)
        if baseline:
            baseline_seeds.add(baseline)
        flag = item.get("seed_matches_baseline")
        if not isinstance(flag, bool) and actual and baseline:
            flag = actual == baseline
        flags.append(flag)
    baseline_platforms = set().union(*(platforms.get(seed, set()) for seed in baseline_seeds))
    actual_platforms = set().union(*(platforms.get(seed, set()) for seed in actual_seeds))
    baseline_platform = next(iter(baseline_platforms)) if len(baseline_platforms) == 1 else None
    actual_platform = next(iter(actual_platforms)) if len(actual_platforms) == 1 else None
    platform_changed = (baseline_platform != actual_platform
                        if baseline_platform is not None and actual_platform is not None else None)
    changed = any(flag is False for flag in flags) or platform_changed is True
    same = all(flag is True for flag in flags) and not changed
    return {"context": "changed" if changed else "same" if same else "unknown",
            "canvas_seed_matches": flags[0], "audio_seed_matches": flags[1],
            "baseline_platform_by_seed": baseline_platform, "session_platform_by_seed": actual_platform,
            "platform_changed": platform_changed,
            "platform_evidence": "seed-linked current metadata" if actual_platform else "unavailable for this session"}


def decision_statistics(rows):
    rows = [row for row in rows if row["included"] and row["event_type"] == "login"]
    same = [row for row in rows if row["context"] == "same"]
    changed = [row for row in rows if row["context"] == "changed"]
    correct_accepts = sum(row["result"] == "success" for row in same)
    correct_rejects = sum(row["result"] == "failure" for row in changed)
    return {"eligible_logins": len(rows), "same_context_logins": len(same),
            "changed_context_logins": len(changed), "unknown_context_logins": len(rows) - len(same) - len(changed),
            "overall_acceptance": ratio(sum(row["result"] == "success" for row in rows), len(rows)),
            "same_context_acceptance": ratio(correct_accepts, len(same)),
            "same_context_false_rejection": ratio(len(same) - correct_accepts, len(same)),
            "changed_context_correct_rejection": ratio(correct_rejects, len(changed)),
            "changed_context_false_acceptance": ratio(len(changed) - correct_rejects, len(changed)),
            "context_policy_correctness": ratio(correct_accepts + correct_rejects, len(same) + len(changed)),
            "failure_reasons": dict(Counter(row["decision_reason"] for row in rows if row["result"] == "failure"))}

