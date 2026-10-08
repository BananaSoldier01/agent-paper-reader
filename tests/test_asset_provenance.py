"""Synthetic structure submissions that must keep images with their source atoms."""

import pytest
from PIL import Image

from reader import store
from reader.importer import import_document
from reader.workflow import submit


@pytest.fixture
def image_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    for name, color in (("one.png", "red"), ("two.png", "blue")):
        Image.new("RGB", (8, 8), color).save(tmp_path / name)
    source = tmp_path / "synthetic.md"
    source.write_text(
        "# Synthetic\n\nPlain paragraph.\n\n![one](one.png)\n\n"
        "Between images.\n\n![two](two.png)\n",
        encoding="utf-8",
    )
    return import_document(source)


def _rows(doc):
    return [
        {
            "id": b["id"],
            "kind": b["kind"],
            "text": b["text"],
            "source_ids": list(b["source_ids"]),
            "structure_note": "Checked against synthetic source",
        }
        for b in doc["blocks"]
    ]


def _send(doc, rows=None, *, submission_id, **extra):
    payload = {
        "revision": doc["revision"],
        "submission_id": submission_id,
        "operation": "structure",
        "agent": "asset provenance regression",
        "note": "Synthetic image provenance checked",
    }
    if rows is not None:
        payload["blocks"] = rows
    payload.update(extra)
    return submit(doc["id"], payload)


def _parts(doc):
    figures = [b for b in doc["blocks"] if b["kind"] == "figure" and b.get("asset")]
    plain = next(b for b in doc["blocks"] if b["text"] == "Plain paragraph.")
    assert len(figures) == 2
    return plain, figures[0], figures[1]


def test_full_replacement_follows_source_when_old_id_is_reused(image_doc):
    plain, figure, _ = _parts(image_doc)
    rows = _rows(image_doc)
    by_id = {b["id"]: b for b in rows}
    by_id[figure["id"]].update(
        kind="paragraph", text=plain["text"], source_ids=plain["source_ids"]
    )
    by_id[plain["id"]].update(
        id="b99999", kind="figure", text=figure["text"], source_ids=figure["source_ids"]
    )

    result = _send(image_doc, rows, submission_id="replace-reused-id")
    changed = {b["id"]: b for b in result["blocks"]}
    assert changed[figure["id"]]["asset"] is None
    assert changed["b99999"]["asset"] == figure["asset"]


def test_full_replacement_keeps_asset_on_renamed_reordered_source(image_doc):
    plain, figure, _ = _parts(image_doc)
    rows = _rows(image_doc)
    merged = next(b for b in rows if b["id"] == figure["id"])
    merged.update(
        source_ids=figure["source_ids"] + plain["source_ids"],
        text=figure["text"] + " " + plain["text"],
    )
    rows = [b for b in rows if b["id"] != plain["id"]]
    joined = _send(image_doc, rows, submission_id="join-image-and-text")
    renamed = _rows(joined)
    target = next(b for b in renamed if b["id"] == figure["id"])
    target.update(
        id="b99999",
        source_ids=list(reversed(target["source_ids"])),
        text=plain["text"] + " " + figure["text"],
    )

    result = _send(joined, renamed, submission_id="rename-reorder-image")
    assert next(b for b in result["blocks"] if b["id"] == "b99999")["asset"] == figure["asset"]


def test_full_replacement_needs_explicit_choice_for_multiple_images(image_doc):
    _, first, second = _parts(image_doc)
    rows = _rows(image_doc)
    target = next(b for b in rows if b["id"] == first["id"])
    target["source_ids"] += second["source_ids"]
    target["text"] += " " + second["text"]
    rows = [b for b in rows if b["id"] != second["id"]]

    with pytest.raises(ValueError, match="asset"):
        _send(image_doc, rows, submission_id="multi-unexplained")
    for label, empty in (("null", None), ("empty", ""), ("false", False)):
        target["asset"] = empty
        with pytest.raises(ValueError, match="asset"):
            _send(image_doc, rows, submission_id=f"multi-{label}")

    crop = "region-" + "a" * 16 + ".png"
    Image.new("RGB", (8, 8), "green").save(store.folder(image_doc["id"]) / crop)
    target["asset"] = crop
    result = _send(image_doc, rows, submission_id="multi-explicit-crop")
    assert next(b for b in result["blocks"] if b["id"] == first["id"])["asset"] == crop


def test_partial_source_split_requires_explicit_asset_placement(image_doc):
    plain, figure, _ = _parts(image_doc)
    rows = _rows(image_doc)
    merged = next(b for b in rows if b["id"] == figure["id"])
    merged.update(
        source_ids=figure["source_ids"] + plain["source_ids"],
        text=figure["text"] + " " + plain["text"],
    )
    rows = [b for b in rows if b["id"] != plain["id"]]
    joined = _send(image_doc, rows, submission_id="partial-join")
    split = _rows(joined)
    old_id = next(b for b in split if b["id"] == figure["id"])
    old_id.update(
        kind="paragraph", text=plain["text"], source_ids=plain["source_ids"]
    )
    figure_row = {
        "id": "b99999",
        "kind": "figure",
        "text": figure["text"],
        "source_ids": figure["source_ids"],
        "structure_note": "Figure part explicitly retained",
    }
    split.append(figure_row)
    with pytest.raises(ValueError, match="asset"):
        _send(joined, split, submission_id="partial-unexplained")

    figure_row["asset"] = figure["asset"]
    result = _send(joined, split, submission_id="partial-explicit-figure")
    changed = {b["id"]: b for b in result["blocks"]}
    assert changed[old_id["id"]]["asset"] is None
    assert changed["b99999"]["asset"] == figure["asset"]


def test_partial_split_cannot_be_satisfied_by_another_old_image(image_doc):
    plain, first, second = _parts(image_doc)
    rows = _rows(image_doc)
    merged = next(b for b in rows if b["id"] == first["id"])
    merged.update(
        source_ids=first["source_ids"] + plain["source_ids"],
        text=first["text"] + " " + plain["text"],
    )
    joined = _send(
        image_doc,
        [b for b in rows if b["id"] != plain["id"]],
        submission_id="partial-with-second-image-join",
    )
    split = _rows(joined)
    first_row = next(b for b in split if b["id"] == first["id"])
    second_row = next(b for b in split if b["id"] == second["id"])
    first_row.update(kind="paragraph", text=plain["text"], source_ids=plain["source_ids"])
    second_row.update(
        source_ids=second["source_ids"] + first["source_ids"],
        text=second["text"] + " " + first["text"],
        asset=second["asset"],
    )
    with pytest.raises(ValueError, match="Split source image asset"):
        _send(joined, split, submission_id="other-image-is-not-disposition")


def test_incremental_source_swap_follows_image_provenance(image_doc):
    plain, figure, _ = _parts(image_doc)
    result = _send(
        image_doc,
        submission_id="patch-source-swap",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[
            {
                "id": figure["id"],
                "kind": "paragraph",
                "text": plain["text"],
                "source_ids": plain["source_ids"],
                "source_change": "Source atom moved to the paragraph block",
                "structure_note": "Now represents plain text",
            },
            {
                "id": plain["id"],
                "kind": "figure",
                "text": figure["text"],
                "source_ids": figure["source_ids"],
                "source_change": "Source atom moved to the figure block",
                "structure_note": "Now represents the original image",
            },
        ],
    )
    changed = {b["id"]: b for b in result["blocks"]}
    assert changed[figure["id"]]["asset"] is None
    assert changed[plain["id"]]["asset"] == figure["asset"]


def test_incremental_merge_uses_updated_source_provenance(image_doc):
    plain, first, second = _parts(image_doc)
    result = _send(
        image_doc,
        submission_id="patch-source-swap-then-merge",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[
            {
                "id": first["id"],
                "kind": "paragraph",
                "text": plain["text"],
                "source_ids": plain["source_ids"],
                "source_change": "Moved plain atom before merge",
            },
            {
                "id": plain["id"],
                "kind": "figure",
                "text": first["text"],
                "source_ids": first["source_ids"],
                "source_change": "Moved image atom to its new block",
            },
        ],
        merges=[
            {
                "into": second["id"],
                "from": [first["id"]],
                "kind": "figure",
                "text": second["text"] + " " + plain["text"],
                "structure_note": "Join the second figure to adjacent plain text",
            }
        ],
    )
    changed = {b["id"]: b for b in result["blocks"]}
    assert changed[second["id"]]["asset"] == second["asset"]
    assert changed[plain["id"]]["asset"] == first["asset"]


def test_incremental_empty_asset_on_unrelated_reused_id_is_not_a_clear(image_doc):
    plain, figure, _ = _parts(image_doc)
    result = _send(
        image_doc,
        submission_id="patch-reused-id-empty",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[
            {
                "id": figure["id"],
                "kind": "paragraph",
                "text": plain["text"],
                "source_ids": plain["source_ids"],
                "asset": None,
                "source_change": "The old id now represents plain source",
            },
            {
                "id": plain["id"],
                "kind": "figure",
                "text": figure["text"],
                "source_ids": figure["source_ids"],
                "source_change": "The original figure source moved here",
            },
        ],
    )
    changed = {b["id"]: b for b in result["blocks"]}
    assert changed[figure["id"]]["asset"] is None
    assert changed[plain["id"]]["asset"] == figure["asset"]


def test_incremental_explicit_new_crop_follows_merged_source(image_doc):
    plain, _, _ = _parts(image_doc)
    other = next(b for b in image_doc["blocks"] if b["text"] == "Between images.")
    crop = "region-" + "b" * 16 + ".png"
    Image.new("RGB", (8, 8), "green").save(store.folder(image_doc["id"]) / crop)
    result = _send(
        image_doc,
        submission_id="patch-new-crop-then-merge",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[{"id": other["id"], "asset": crop}],
        merges=[
            {
                "into": plain["id"],
                "from": [other["id"]],
                "text": plain["text"] + " " + other["text"],
                "source_change": "Adjacent synthetic paragraphs joined",
                "structure_note": "New crop follows its source through the merge",
            }
        ],
    )
    assert next(b for b in result["blocks"] if b["id"] == plain["id"])["asset"] == crop


def test_incremental_replaced_figure_crop_survives_plain_merge(image_doc):
    plain, figure, _ = _parts(image_doc)
    crop = "region-" + "c" * 16 + ".png"
    Image.new("RGB", (8, 8), "green").save(store.folder(image_doc["id"]) / crop)
    result = _send(
        image_doc,
        submission_id="replaced-figure-plus-plain",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[{"id": figure["id"], "asset": crop}],
        merges=[
            {
                "into": figure["id"],
                "from": [plain["id"]],
                "text": figure["text"] + " " + plain["text"],
                "structure_note": "Explicit crop replaces the old image before merging plain text",
            }
        ],
    )
    assert next(b for b in result["blocks"] if b["id"] == figure["id"])["asset"] == crop


def test_incremental_two_figures_replaced_by_same_crop_can_merge(image_doc):
    _, first, second = _parts(image_doc)
    crop = "region-" + "c" * 16 + ".png"
    Image.new("RGB", (8, 8), "green").save(store.folder(image_doc["id"]) / crop)
    result = _send(
        image_doc,
        submission_id="two-figures-replaced-by-one-crop",
        keep_extracted=True,
        default_structure_note="Unchanged synthetic source",
        updates=[{"id": first["id"], "asset": crop}, {"id": second["id"], "asset": crop}],
        merges=[
            {
                "into": first["id"],
                "from": [second["id"]],
                "text": first["text"] + " " + second["text"],
                "structure_note": "Both image sources now use the same explicit crop",
            }
        ],
    )
    assert next(b for b in result["blocks"] if b["id"] == first["id"])["asset"] == crop


def test_incremental_partial_crop_does_not_replace_whole_old_image(image_doc):
    plain, first, second = _parts(image_doc)
    other = next(b for b in image_doc["blocks"] if b["text"] == "Between images.")
    rows = _rows(image_doc)
    joined_row = next(b for b in rows if b["id"] == first["id"])
    joined_row.update(
        source_ids=first["source_ids"] + plain["source_ids"],
        text=first["text"] + " " + plain["text"],
    )
    joined = _send(
        image_doc,
        [b for b in rows if b["id"] != plain["id"]],
        submission_id="partial-crop-join-old-image",
    )
    crop = "region-" + "c" * 16 + ".png"
    Image.new("RGB", (8, 8), "green").save(store.folder(joined["id"]) / crop)
    with pytest.raises(ValueError, match="asset"):
        _send(
            joined,
            submission_id="partial-crop-is-not-whole-replacement",
            keep_extracted=True,
            default_structure_note="Unchanged synthetic source",
            updates=[
                {
                    "id": first["id"],
                    "source_ids": first["source_ids"],
                    "text": first["text"],
                    "asset": crop,
                    "source_change": "Partial source returned to figure",
                },
                {
                    "id": other["id"],
                    "source_ids": plain["source_ids"],
                    "text": plain["text"],
                    "source_change": "Remaining old image source moved here",
                },
                {
                    "id": second["id"],
                    "source_ids": second["source_ids"] + other["source_ids"],
                    "text": second["text"] + " " + other["text"],
                    "source_change": "Other plain source moved here",
                },
            ],
            merges=[
                {
                    "into": other["id"],
                    "from": [first["id"]],
                    "kind": "figure",
                    "text": plain["text"] + " " + first["text"],
                    "structure_note": "Rejoin both halves of the original image",
                }
            ],
        )


@pytest.mark.parametrize("empty", [None, "", False])
def test_empty_asset_cannot_clear_same_source_image(image_doc, empty):
    _, figure, _ = _parts(image_doc)
    rows = _rows(image_doc)
    next(b for b in rows if b["id"] == figure["id"])["asset"] = empty
    with pytest.raises(ValueError, match="asset"):
        _send(image_doc, rows, submission_id=f"empty-same-source-{empty!r}")
