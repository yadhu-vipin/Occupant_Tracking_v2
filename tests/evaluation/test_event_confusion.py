from evaluation.evaluate_events import build_event_rows, event_confusion, summarize


def truth(i, occupant, home, current, zone="z1"):
    return {"event_id": f"E{i}", "timestamp": f"08:00:0{i}", "occupant_id": occupant, "home_building": home,
            "current_building": current, "current_zone": zone}


def local(i, predicted):
    return {"event_id": f"E{i}", "accepted": predicted is not None, "predicted_occupant_id": predicted}


def attempt(i, occupant, building, source="routed"):
    return {"event_id": f"E{i}", "shortlist": [building] if building else [], "candidates_queried": 1,
            "verification_result": "CONFIRMED" if occupant else "UNRESOLVED",
            "verified_occupant_id": occupant, "verified_building": building, "routing_source": source}


def test_event_level_tp_fp_fn_tn_split_by_local_and_visitor():
    gt = [truth(1, "A", "b1", "b1"), truth(2, "B", "b1", "b1"), truth(3, "C", "b2", "b1"), truth(4, "D", "b2", "b1")]
    recognition = {"E1": local(1, "A"), "E2": local(2, "A"),            # A right, B mistaken for A
                   "E3": local(3, None), "E4": local(4, None)}
    retrieval = {"E3": attempt(3, "C", "b2"), "E4": attempt(4, None, None)}   # C found, D not
    rows = build_event_rows(gt, recognition, retrieval)
    assert [r["predicted_occupant_id"] for r in rows] == ["A", "A", "C", None]
    c = event_confusion(rows)
    assert c["n_labels"] == 4
    assert (c["all"]["tp"], c["all"]["fp"], c["all"]["fn"]) == (2, 1, 2)
    assert c["all"]["tn"] == 4 * 4 - 2 - 1 - 2
    assert (c["local"]["tp"], c["local"]["fp"], c["local"]["fn"]) == (1, 1, 1)
    assert (c["visitor"]["tp"], c["visitor"]["fp"], c["visitor"]["fn"]) == (1, 0, 1)
    assert summarize(rows, {})["event_confusion"] == c


def test_visitor_served_from_the_visitor_db_counts_as_correct_without_routing():
    gt = [truth(1, "C", "b2", "b1")]
    rows = build_event_rows(gt, {"E1": local(1, None)}, {"E1": attempt(1, "C", "b2", source="day_cache")})
    assert rows[0]["outcome"] == "CORRECT_VIA_VISITOR_DB"
    s = summarize(rows, {})
    assert s["visitor_accuracy"] == 1.0 and s["visitor_events_served_from_visitor_db"] == 1
    assert s["routed_visitor_events"] == 0
    wrong = build_event_rows(gt, {"E1": local(1, None)}, {"E1": attempt(1, "X", "b3", source="permanent_pool")})
    assert wrong[0]["outcome"] == "WRONG_IDENTITY_VIA_VISITOR_DB"
