"""Tests for the locate() evaluation scorer and synthetic scenes.

No model is involved: a fake `locate` stands in for MiniCPM-V.
"""

from __future__ import annotations

from vision.locate_eval import (
    PERCENT,
    PIXELS,
    RESIZED,
    Item,
    LocateResult,
    centered_baseline_iou,
    distinct_boxes_per_scene,
    iou,
    make_button_scene,
    make_scene,
    resize_longest_side,
    run_trials,
    score_trial,
    summarize,
    to_pixel_box,
)


def test_iou_identical_disjoint_and_half_overlap() -> None:
    box = (0.0, 0.0, 10.0, 10.0)
    assert iou(box, box) == 1.0
    assert iou(box, (20.0, 20.0, 30.0, 30.0)) == 0.0
    # 10x10 vs 10x10 shifted by 5 in x: intersection 50, union 150
    assert abs(iou(box, (5.0, 0.0, 15.0, 10.0)) - 50 / 150) < 1e-9


def test_iou_zero_area_boxes_do_not_divide_by_zero() -> None:
    assert iou((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)) == 0.0


def test_to_pixel_box_percent_pixels_and_resized() -> None:
    size = (1000, 500)
    assert to_pixel_box(10, 20, 30, 40, size, PERCENT) == (100.0, 100.0, 400.0, 300.0)
    assert to_pixel_box(10, 20, 30, 40, size, PIXELS) == (10.0, 20.0, 40.0, 60.0)
    # model saw 500x250: everything scales by 2
    assert to_pixel_box(10, 20, 30, 40, size, RESIZED, model_size=(500, 250)) == (
        20.0,
        40.0,
        80.0,
        120.0,
    )


def test_to_pixel_box_rejects_bad_input() -> None:
    for kwargs in ({"interpretation": "nope"}, {"interpretation": RESIZED}):
        try:
            to_pixel_box(1, 1, 1, 1, (10, 10), **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


def test_scenes_are_deterministic_and_boxes_do_not_overlap() -> None:
    a, b = make_scene(7), make_scene(7)
    assert [i.box for i in a.items] == [i.box for i in b.items]
    assert a.image.tobytes() == b.image.tobytes()
    assert [i.box for i in make_scene(8).items] != [i.box for i in a.items]

    for scene in (make_scene(3), make_button_scene(3)):
        assert len(scene.items) == 4
        targets = [i.target for i in scene.items]
        assert len(set(targets)) == len(targets), "each target must name one thing"
        for i, first in enumerate(scene.items):
            for second in scene.items[i + 1 :]:
                assert iou(first.box, second.box) == 0.0
            left, top, right, bottom = first.box
            width, height = scene.image.size
            assert 0 <= left < right <= width and 0 <= top < bottom <= height


def test_perfect_percent_answer_scores_one_only_under_percent() -> None:
    scene = make_scene(1)
    item = scene.items[0]
    w, h = scene.image.size
    left, top, right, bottom = item.box
    perfect = LocateResult(
        True,
        round(left / w * 100),
        round(top / h * 100),
        round((right - left) / w * 100),
        round((bottom - top) / h * 100),
    )

    trial = score_trial(scene, item, perfect, [PERCENT, PIXELS])

    assert trial.ious[PERCENT] > 0.8
    assert trial.ious[PIXELS] < 0.1


def test_not_found_and_parse_failure_score_zero() -> None:
    scene = make_scene(1)
    item = scene.items[0]
    missing = LocateResult(False, 10, 10, 10, 10)
    assert score_trial(scene, item, missing, [PERCENT]).ious[PERCENT] == 0.0
    assert score_trial(scene, item, None, [PERCENT]).ious[PERCENT] == 0.0


def test_centered_baseline_is_low_for_off_center_items() -> None:
    item = Item("the thing", (0.0, 0.0, 100.0, 100.0))
    assert centered_baseline_iou(item, (1280, 720)) == 0.0


def test_resize_longest_side_matches_app_behaviour() -> None:
    scene = make_scene(1)
    assert resize_longest_side(scene.image, None) is scene.image
    assert resize_longest_side(scene.image, 2000) is scene.image
    assert resize_longest_side(scene.image, 512).size == (512, 288)


def test_run_trials_scores_a_fake_model_that_answers_correctly() -> None:
    scenes = [make_scene(1), make_scene(2)]
    truth_by_target = {}
    for s in scenes:
        for item in s.items:
            truth_by_target[(s.seed, item.target)] = item.box

    # The fake knows the real boxes, and answers in percent of the image
    # it is handed (which is the downscaled one).
    current = {"seed": 0}

    def fake_locate(image, target):
        w, h = image.size
        left, top, right, bottom = truth_by_target[(current["seed"], target)]
        sx = w / 1280
        sy = h / 720
        return LocateResult(
            True,
            round(left * sx / w * 100),
            round(top * sy / h * 100),
            round((right - left) * sx / w * 100),
            round((bottom - top) * sy / h * 100),
        )

    trials = []
    for scene in scenes:
        current["seed"] = scene.seed
        trials += run_trials([scene], fake_locate, [PERCENT, PIXELS], max_dimension=512)

    assert len(trials) == 8
    good = summarize(trials, PERCENT)
    bad = summarize(trials, PIXELS)
    assert good.hit_rate >= 0.75
    assert bad.hit_rate == 0.0
    # truth stays in original scene pixels
    assert trials[0].truth == scenes[0].items[0].box


def test_distinct_boxes_flags_a_model_that_ignores_the_question() -> None:
    scene = make_scene(5)
    same = LocateResult(True, 22, 100, 100, 100)

    trials = run_trials([scene], lambda img, tgt: same, [PERCENT])

    assert len(trials) == 4
    assert distinct_boxes_per_scene(trials) == 1.0


def test_summarize_handles_no_trials() -> None:
    summary = summarize([], PERCENT)
    assert summary.trials == 0 and summary.mean_iou == 0.0 and summary.hit_rate == 0.0


def test_report_verdicts_for_blind_and_accurate_models() -> None:
    from eval_locate import format_report

    scenes = [make_scene(1), make_scene(2)]

    blind = run_trials(scenes, lambda img, tgt: LocateResult(True, 22, 100, 100, 100), [PERCENT, PIXELS])
    assert "not localizing" in format_report(blind, scenes, [PERCENT, PIXELS], None)

    truth = {(s.seed, i.target): i.box for s in scenes for i in s.items}
    seed_of = {}
    for s in scenes:
        for i in s.items:
            seed_of.setdefault(i.target, []).append(s.seed)

    # an oracle that is handed the scene via closure, answering in percent
    def oracle_for(scene):
        def answer(image, target):
            w, h = image.size
            left, top, right, bottom = truth[(scene.seed, target)]
            sx, sy = w / scene.image.size[0], h / scene.image.size[1]
            return LocateResult(
                True,
                round(left * sx / w * 100), round(top * sy / h * 100),
                round((right - left) * sx / w * 100), round((bottom - top) * sy / h * 100),
            )
        return answer

    good = []
    for scene in scenes:
        good += run_trials([scene], oracle_for(scene), [PERCENT, PIXELS])
    assert "percent reading holds up" in format_report(good, scenes, [PERCENT, PIXELS], None)
