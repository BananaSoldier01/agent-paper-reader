"""Optional, read-only structure-candidate suggestions.

Does not modify document.json or submit. The Agent confirms page by page
and submits structure through the existing submit command.
"""

import re
from collections import defaultdict

HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)[A-Z]")
CAPTION_RE = re.compile(r"^(?:Figure|Table|Fig\.?)\s+\d+\s*[:.]", re.I)
# A line that opens with a footnote mark starts a new footnote item, even when
# it sits tight under the previous footnote with the same left edge
# ("†Work at A." / "‡Work at B."). Superscript digits are not detected: NFKC
# turns them into plain digits that look like list numbers or table cells.
FOOTNOTE_MARK_RE = re.compile(r"^[\u2217*\u2020\u2021\u00a7\u00b6\u2016]+\s*\S")
NAMED_HEADINGS = {
    "abstract",
    "references",
    "acknowledgments",
    "acknowledgements",
    "appendix",
}


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def ends_sentence(text):
    t = (text or "").rstrip()
    if not t or t.endswith("-"):
        return False
    if not t.endswith((".", "?", "!")):
        return False
    if re.search(r"(?:et al|e\.g|i\.e|vs|fig|dr|mr|mrs|prof|al)\.$", t, re.I):
        return False
    if re.search(r"\b[A-Z]\.$", t):
        return False
    return True


def is_heading_text(text):
    t = (text or "").strip()
    if not t or len(t) > 90:
        return False
    if _norm(t) in NAMED_HEADINGS:
        return True
    if CAPTION_RE.match(t):
        return False
    words = t.split()
    if HEADING_RE.match(t) and len(words) <= 14:
        return True
    return False


def is_caption_text(text):
    return bool(CAPTION_RE.match((text or "").strip()))


def starts_footnote_mark(text):
    return bool(FOOTNOTE_MARK_RE.match((text or "").lstrip()))


class Line:
    def __init__(self, atom_id, page, text, bbox, kind="prose"):
        self.atom_ids = [atom_id]
        self.page = page
        self.text = text or ""
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.kind = kind  # prose, header, footer, fragment, page
        self.column = "single"
        self.hard = []

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return max(0.0, self.y1 - self.y0)

    def add_hard(self, flag):
        if flag not in self.hard:
            self.hard.append(flag)

    def absorb(self, other, flag=None):
        # Caller passes `other` later in reading order. Do not reorder by x0:
        # a following line is often ~1pt further left than the line above.
        joiner = "" if self.text.rstrip().endswith("-") else " "
        self.atom_ids = self.atom_ids + other.atom_ids
        self.text = (self.text.rstrip() + joiner + other.text.lstrip()).strip()
        self.x0 = min(self.x0, other.x0)
        self.y0 = min(self.y0, other.y0)
        self.x1 = max(self.x1, other.x1)
        self.y1 = max(self.y1, other.y1)
        for h in other.hard:
            self.add_hard(h)
        if flag:
            self.add_hard(flag)


def _atom_has_geom(atom):
    loc = atom.get("location") or {}
    page = loc.get("page")
    bbox = loc.get("bbox")
    return page is not None and bool(bbox) and len(bbox) == 4


def lines_from_document(doc):
    pages = {p["page"]: p for p in doc.get("pages") or []}
    lines = []
    for atom in doc.get("atoms") or []:
        loc = atom.get("location") or {}
        page = loc.get("page")
        bbox = loc.get("bbox")
        if page is None or not bbox or len(bbox) != 4:
            # Still kept later as orphan. Stash on a sentinel list via kind.
            lines.append(Line(atom.get("id"), page or 0, atom.get("text") or "", [0, 0, 0, 0], "missing"))
            continue
        text = atom.get("text") or ""
        x0, y0, x1, y1 = [float(v) for v in bbox]
        pg = pages.get(page) or {}
        pw = float(pg.get("width") or 612)
        ph = float(pg.get("height") or 792)
        kind = "prose"
        if not text.strip() and (x1 - x0) > pw * 0.9 and (y1 - y0) > ph * 0.9:
            kind = "page"
        lines.append(Line(atom["id"], int(page), text, [x0, y0, x1, y1], kind))
    if not pages:
        by = defaultdict(lambda: [0, 0])
        for ln in lines:
            if ln.kind == "missing":
                continue
            by[ln.page][0] = max(by[ln.page][0], ln.x1)
            by[ln.page][1] = max(by[ln.page][1], ln.y1)
        pages = {p: {"page": p, "width": w or 612, "height": h or 792} for p, (w, h) in by.items()}
    return lines, pages


def _body_height(lines):
    heights = [ln.height for ln in lines if ln.kind == "prose" and len(ln.text.strip()) > 20 and 6 <= ln.height <= 22]
    if not heights:
        heights = [ln.height for ln in lines if ln.kind == "prose" and ln.height > 0]
    if not heights:
        return 10.0
    heights.sort()
    return heights[len(heights) // 2]


def _mark_margin(all_lines, pages):
    """Repeated running headers/footers and short page numbers. Generic, no fixed title."""
    texts = defaultdict(set)
    for ln in all_lines:
        if ln.kind != "prose" or not ln.text.strip():
            continue
        ph = float(pages[ln.page]["height"])
        if ln.y0 < ph * 0.09 or ln.y1 > ph * 0.91:
            texts[_norm(ln.text)].add(ln.page)
    repeated = {t for t, ps in texts.items() if t and len(ps) >= 3}
    for ln in all_lines:
        if ln.kind != "prose":
            continue
        ph = float(pages[ln.page]["height"])
        key = _norm(ln.text)
        if key in repeated and (ln.y0 < ph * 0.09 or ln.y1 > ph * 0.91):
            ln.kind = "header" if ln.y0 < ph * 0.5 else "footer"
            ln.add_hard("repeated_margin")
        elif re.fullmatch(r"\d{1,3}", ln.text.strip()) and ln.y0 > ph * 0.90:
            ln.kind = "footer"
            ln.add_hard("page_number")


def _is_micro(ln, body_h, page_w):
    """A short glyph (sub/superscript), not a full-height word or table cell.

    Full-height text can be narrow ("values.", "2017.", "5.29") and still be a
    real line. Those stay in the prose stream so a short last line can merge,
    and unmerged ones are retagged as fragments afterwards.
    """
    if ln.kind != "prose":
        return False
    t = ln.text.strip()
    if not t or len(t) > 18:
        return False
    if ln.height < body_h * 0.78 and ln.width < 48:
        return True
    return False


def _is_narrow_token(ln, body_h):
    """Full-height but very narrow: a short word, a year, or one table cell."""
    if ln.kind != "prose":
        return False
    t = ln.text.strip()
    if not t or len(t) > 18:
        return False
    return ln.width < 36 and ln.height <= body_h * 1.25


def _detect_gutter(voters, page_w):
    if len(voters) < 8:
        return None
    best = None
    g = page_w * 0.35
    while g <= page_w * 0.65:
        left = right = straddle = 0
        for ln in voters:
            if ln.x1 < g - 2:
                left += 1
            elif ln.x0 > g + 2:
                right += 1
            else:
                straddle += 1
        if left >= 4 and right >= 4 and straddle <= max(2, int(0.08 * (left + right))):
            score = left * right - straddle * 20
            if best is None or score > best[0]:
                best = (score, g, left, right, straddle)
        g += 4
    if not best:
        return None
    return best[1]


def _assign_columns(text_lines, page_w, body_h):
    prose = [ln for ln in text_lines if ln.kind == "prose"]
    micros = [ln for ln in prose if _is_micro(ln, body_h, page_w)]
    body = [ln for ln in prose if ln not in micros]
    wide = [ln for ln in body if ln.width >= page_w * 0.55]
    # A page whose typical line already spans the text block is single-column,
    # even if a formula hole splits one baseline into two medium pieces.
    if len(wide) >= 4 and len(wide) >= 0.4 * max(1, len(body)):
        for ln in body:
            ln.column = "full" if ln.width >= page_w * 0.55 else "single"
        for ln in micros:
            ln.column = "fragment"
            ln.kind = "fragment"
        return {"mode": "single", "gutter": None, "wide": len(wide), "body": len(body)}
    voters = [ln for ln in body if page_w * 0.20 <= ln.width < page_w * 0.55]
    gutter = _detect_gutter(voters, page_w)
    if gutter is None:
        for ln in body:
            ln.column = "full" if ln.width >= page_w * 0.55 else "single"
        for ln in micros:
            ln.column = "fragment"
            ln.kind = "fragment"
        return {"mode": "single", "gutter": None, "wide": len(wide), "body": len(body)}
    for ln in body:
        if ln.width >= page_w * 0.55 or (ln.x0 < gutter - 6 and ln.x1 > gutter + 6):
            ln.column = "full"
            if ln.width >= page_w * 0.70:
                ln.add_hard("possible_fused_columns")
        elif ln.x1 < gutter - 1:
            ln.column = "left"
        elif ln.x0 > gutter + 1:
            ln.column = "right"
        else:
            ln.column = "left" if (ln.x0 + ln.x1) / 2 < gutter else "right"
            ln.add_hard("ambiguous_column")
    for ln in micros:
        ln.column = "fragment"
        ln.kind = "fragment"
    return {"mode": "two", "gutter": round(gutter, 1), "wide": len(wide), "body": len(body)}


def _same_baseline_join(lines, page_w, two_col, gutter):
    """Join pieces of one baseline split by an inline hole (formula), not authors."""
    prose = [ln for ln in lines if ln.kind == "prose"]
    other = [ln for ln in lines if ln.kind != "prose"]
    prose.sort(key=lambda ln: (ln.y0, ln.x0))
    used = set()
    merged = []
    for i, ln in enumerate(prose):
        if id(ln) in used:
            continue
        used.add(id(ln))
        cur = ln
        for nxt in prose[i + 1:]:
            if id(nxt) in used:
                continue
            if abs(nxt.y0 - cur.y0) > 2.5:
                break
            gap = nxt.x0 - cur.x1
            if gap < -2:
                continue
            if two_col and gutter is not None:
                cur_side = "L" if cur.x1 < gutter else "R"
                nxt_side = "L" if nxt.x0 < gutter else "R"
                if cur_side != nxt_side:
                    continue
            starts_lower = bool(re.match(r"[a-z(\[]", nxt.text.lstrip()))
            hole = 8 <= gap <= max(90, page_w * 0.16)
            # A short left piece (an email, a name) is not a formula hole.
            left_words = len(cur.text.split())
            right_words = len(nxt.text.split())
            # Both sides must look like prose. A one-word table cell ("semi-supervised") is not a continuation.
            if hole and starts_lower and not ends_sentence(cur.text) and left_words >= 4 and right_words >= 4:
                used.add(id(nxt))
                cur.absorb(nxt, "inline_gap")
                continue
            # not joined; leave for later so a row of names stays separate
        merged.append(cur)
    return other + merged


def _cluster_fragments(frags):
    # Do not chain nearby fragments. Table cells and axis ticks are within a few
    # points of each other and would collapse into one false paragraph.
    out = []
    for ln in sorted(frags, key=lambda ln: (ln.y0, ln.x0)):
        ln.kind = "fragment"
        ln.column = "fragment"
        ln.add_hard("isolated_fragment")
        out.append(ln)
    return out


def _reading_order(body_lines):
    """Full-width lines split the page. Inside a band: left column, then right."""
    ordered = sorted(body_lines, key=lambda ln: (ln.y0, ln.x0))
    out = []
    band = []

    def flush():
        if not band:
            return
        cols = {ln.column for ln in band}
        if "left" in cols or "right" in cols:
            left = sorted((ln for ln in band if ln.column == "left"), key=lambda ln: (ln.y0, ln.x0))
            right = sorted((ln for ln in band if ln.column == "right"), key=lambda ln: (ln.y0, ln.x0))
            mid = sorted((ln for ln in band if ln.column not in ("left", "right")), key=lambda ln: (ln.y0, ln.x0))
            out.extend(left)
            out.extend(right)
            out.extend(mid)
        else:
            out.extend(sorted(band, key=lambda ln: (ln.y0, ln.x0)))
        band.clear()

    for ln in ordered:
        if ln.column == "full":
            flush()
            out.append(ln)
        else:
            band.append(ln)
    flush()
    return out


def _columns_compatible(prev, nxt):
    if prev.column == nxt.column:
        return True
    # A short last line is labeled "single" while the measure above is "full".
    # The label must not block a same-paragraph join. Left/right stay strict
    # so a two-column page does not glue a full title to the column under it
    # unless the rest of this function agrees — and a title is a heading.
    return {prev.column, nxt.column} == {"full", "single"}


def _can_merge(prev, nxt, body_h):
    if prev.page != nxt.page or not _columns_compatible(prev, nxt):
        return False
    if prev.kind != nxt.kind:
        return False
    if prev.kind in ("header", "footer", "fragment", "page"):
        return False
    # Digit-heavy cells (tables) must not chain into a paragraph.
    # A short last token of a real prose line ("2017.", "P", "PE .") is not a cell.
    prev_alpha = sum(ch.isalpha() for ch in prev.text)
    nxt_alpha = sum(ch.isalpha() for ch in nxt.text)
    if prev_alpha < 4 or nxt_alpha < 4:
        short_tail = (
            nxt_alpha < 4
            and prev_alpha >= 8
            and len(nxt.text.strip()) <= 18
            and nxt.width <= 48
            and (prev.column == "full" or prev.width >= 180)
        )
        if not short_tail:
            return False
    if is_heading_text(prev.text) or is_heading_text(nxt.text):
        return False
    # A caption may absorb following tight lines, but a caption does not join the previous paragraph.
    if is_caption_text(nxt.text) and not is_caption_text(prev.text):
        return False
    gap = nxt.y0 - prev.y1
    # Side-by-side leftovers (author row): overlap vertically and sit to the right.
    if gap < -2 and nxt.x0 > prev.x1 - 2:
        return False
    if gap > body_h * 0.45:
        return False
    if gap < -body_h * 0.2:
        return False
    x_shift = nxt.x0 - prev.x0
    aligned = abs(x_shift) <= 8
    hanging = 6 < x_shift <= 36 and nxt.x1 <= prev.x1 + 10
    hyphen = prev.text.rstrip().endswith("-") and bool(re.match(r"[a-z]", nxt.text.lstrip()))
    if hyphen and gap <= body_h * 0.55:
        return "hyphen_join"
    if not aligned and not hanging:
        return False
    # First-line indent after a finished sentence is a new paragraph even when the gap is small.
    if x_shift > 8 and ends_sentence(prev.text) and not hyphen:
        return False
    if hanging and not is_caption_text(prev.text):
        # Bibliography wraps hang off a full-measure line (or a real column).
        # Two short rows in a table ("Vinyals…" / "Petrov…") are both "single"
        # and must stay apart. Left/right still wrap inside a column.
        if prev.column == "single":
            return False
        return "hanging_indent"
    return "tight"


def _merge_stream(ordered, body_h):
    if not ordered:
        return []
    groups = []
    cur = ordered[0]
    for nxt in ordered[1:]:
        why = _can_merge(cur, nxt, body_h)
        if why and starts_footnote_mark(nxt.text):
            # Geometry says "same paragraph", but the next line opens a new
            # footnote mark. Keep the items apart and flag both so the agent
            # confirms the split against the page image.
            cur.add_hard("footnote_marker_split")
            nxt.add_hard("footnote_marker_split")
            why = False
        if why:
            cur.absorb(nxt, why if why != "tight" else None)
            if why == "tight" and is_caption_text(cur.text):
                cur.add_hard("caption_continuation")
        else:
            groups.append(cur)
            cur = nxt
    groups.append(cur)
    return groups


def _kind_hint(ln):
    if ln.kind == "page":
        return "page"
    if ln.kind == "header":
        return "header"
    if ln.kind == "footer":
        return "footer"
    if ln.kind == "fragment":
        return "figure_fragment"
    if is_heading_text(ln.text):
        return "heading"
    words = ln.text.split()
    if ln.kind == "prose" and 1 < len(words) <= 12 and len(ln.text) <= 80 and ln.height >= 13 and ln.y0 < 220:
        if ln.text[:1].isupper() and not ln.text.rstrip().endswith("."):
            return "heading"
    if is_caption_text(ln.text):
        return "caption"
    return "paragraph"


def _confidence(ln, hint):
    if hint == "page":
        return 1.0
    if hint == "figure_fragment":
        return 0.35
    score = 0.84
    score -= 0.12 * len(ln.hard)
    if hint == "heading":
        score -= 0.05
    return round(max(0.2, min(0.95, score)), 2)


def _insert_fragments(prose_groups, clusters):
    """Place fragment clusters by vertical position without splitting a prose group."""
    placed = []
    frags = sorted(clusters, key=lambda ln: (ln.y0, ln.x0))
    fi = 0
    for group in prose_groups:
        while fi < len(frags) and frags[fi].y0 <= group.y0:
            placed.append(frags[fi])
            fi += 1
        placed.append(group)
        while fi < len(frags) and frags[fi].y0 < group.y1 and frags[fi].page == group.page:
            group.add_hard("fragment_beside_text")
            placed.append(frags[fi])
            fi += 1
    while fi < len(frags):
        placed.append(frags[fi])
        fi += 1
    return placed


def build_candidates(lines, pages):
    _mark_margin(lines, pages)
    by_page = defaultdict(list)
    missing = []
    for ln in lines:
        if ln.kind == "missing":
            missing.append(ln)
        else:
            by_page[ln.page].append(ln)

    page_info = []
    ordered_lines = []
    for page in sorted(by_page):
        pw = float(pages.get(page, {}).get("width") or 612)
        ph = float(pages.get(page, {}).get("height") or 792)
        bucket = by_page[page]
        body_h = _body_height(bucket)
        # Attach subscripts before column votes so tiny glyphs do not look like a column.
        prose_for_attach = []
        passthrough = []
        for ln in bucket:
            if ln.kind == "prose":
                prose_for_attach.append(ln)
            else:
                passthrough.append(ln)
        # mark micros relative to this page width, then attach those inside a host
        hosts = []
        micros = []
        narrow = []
        for ln in prose_for_attach:
            if _is_micro(ln, body_h, pw):
                micros.append(ln)
            elif _is_narrow_token(ln, body_h):
                # Not a subscript host and not a column voter. Joined later if
                # it is a short last line; otherwise retagged as a fragment.
                narrow.append(ln)
            else:
                hosts.append(ln)
        attached = set()
        host_box = {id(h): (h.x0, h.y0, h.x1, h.y1) for h in hosts}
        for micro in micros:
            best = None
            best_score = 0
            for host in hosts:
                x0, y0, x1, y1 = host_box[id(host)]
                x_inside = micro.x0 >= x0 - 2 and micro.x1 <= x1 + 4
                y_overlap = min(y1, micro.y1) - max(y0, micro.y0)
                if x_inside and y_overlap > 0:
                    score = y_overlap
                    if score > best_score:
                        best_score = score
                        best = host
            if best is not None:
                best.absorb(micro, "possible_subscript")
                attached.add(id(micro))
        prose = hosts + [m for m in micros if id(m) not in attached]
        info = _assign_columns(prose, pw, body_h)
        gutter = info["gutter"]
        for ln in narrow:
            if info["mode"] == "two" and gutter is not None:
                if ln.x1 < gutter - 1:
                    ln.column = "left"
                elif ln.x0 > gutter + 1:
                    ln.column = "right"
                else:
                    ln.column = "left" if (ln.x0 + ln.x1) / 2 < gutter else "right"
            else:
                ln.column = "single"
        prose.extend(narrow)
        info.update({"page": page, "width": pw, "height": ph, "body_height": round(body_h, 2)})
        page_info.append(info)
        two = info["mode"] == "two"
        frags = [ln for ln in prose if ln.kind == "fragment"]
        body = [ln for ln in prose if ln.kind == "prose"]
        body = _same_baseline_join(body, pw, two, info["gutter"])
        headers = sorted((ln for ln in passthrough if ln.kind == "header"), key=lambda ln: ln.y0)
        footers = sorted((ln for ln in passthrough if ln.kind == "footer"), key=lambda ln: ln.y0)
        pages_assets = [ln for ln in passthrough if ln.kind == "page"]
        # margin marks live on prose lines that were retagged
        headers += sorted((ln for ln in prose if ln.kind == "header"), key=lambda ln: ln.y0)
        footers += sorted((ln for ln in prose if ln.kind == "footer"), key=lambda ln: ln.y0)
        body = [ln for ln in body if ln.kind == "prose"]
        ordered = _reading_order(body)
        merged = _merge_stream(ordered, body_h)
        kept_merged = []
        for ln in merged:
            # A narrow token that did not join a paragraph is a cell or a tick,
            # not a one-word paragraph. Short last lines already joined above.
            if (
                ln.kind == "prose"
                and len(ln.atom_ids) == 1
                and _is_narrow_token(ln, body_h)
                and not is_heading_text(ln.text)
                and not is_caption_text(ln.text)
            ):
                ln.kind = "fragment"
                ln.column = "fragment"
                ln.add_hard("isolated_fragment")
                frags.append(ln)
            else:
                kept_merged.append(ln)
        merged = kept_merged
        clusters = _cluster_fragments(frags)
        flow = _insert_fragments(merged, clusters)
        for asset in pages_assets:
            ordered_lines.append(asset)
        ordered_lines.extend(headers)
        ordered_lines.extend(flow)
        ordered_lines.extend(footers)

    # Cross-page hint only. Do not merge.
    body_idxs = [i for i, ln in enumerate(ordered_lines) if _kind_hint(ln) in ("paragraph", "heading", "caption")]
    for a, b in zip(body_idxs, body_idxs[1:]):
        prev, nxt = ordered_lines[a], ordered_lines[b]
        if nxt.page == prev.page + 1 and not is_heading_text(nxt.text) and not ends_sentence(prev.text) and not prev.text.rstrip().endswith("-"):
            prev.add_hard("possible_cross_page")
            nxt.add_hard("possible_cross_page")
        elif nxt.page == prev.page + 1 and prev.text.rstrip().endswith("-"):
            prev.add_hard("possible_cross_page_hyphen")
            nxt.add_hard("possible_cross_page_hyphen")

    candidates = []
    for ln in ordered_lines + missing:
        hint = _kind_hint(ln) if ln.kind != "missing" else "orphan"
        if ln.kind == "missing":
            ln.add_hard("missing_bbox")
        if ln.text.count("@") >= 2:
            ln.add_hard("possible_multi_item")
        candidates.append({
            "page": ln.page,
            "page_span": [ln.page] if ln.page else [],
            "column": ln.column if ln.kind not in ("page", "header", "footer", "fragment", "missing") else (
                "none" if ln.kind in ("page", "missing") else ln.kind if ln.kind in ("header", "footer", "fragment") else ln.column
            ),
            "kind_hint": hint,
            "atom_ids": list(ln.atom_ids),
            "bbox": [round(ln.x0, 2), round(ln.y0, 2), round(ln.x1, 2), round(ln.y1, 2)],
            "confidence": _confidence(ln, hint),
            "hard_spots": list(ln.hard),
            "text_preview": re.sub(r"\s+", " ", ln.text).strip()[:400],
        })
    for i, cand in enumerate(candidates, 1):
        cand["id"] = f"c{i:05d}"
        cand["order"] = i
    return candidates, page_info


def coverage(lines, candidates):
    expected = []
    for ln in lines:
        expected.extend(ln.atom_ids)
    got = []
    for cand in candidates:
        got.extend(cand["atom_ids"])
    exp_set = set(expected)
    got_set = set(got)
    missing = sorted(exp_set - got_set)
    extra = sorted(got_set - exp_set)
    dup = sorted({a for a in got if got.count(a) > 1})
    orphans = [a for a in missing]
    return {
        "atom_count": len(exp_set),
        "candidate_count": len(candidates),
        "coverage_ok": not missing and not extra and not dup and len(got) == len(exp_set),
        "missing_atom_ids": missing,
        "duplicate_atom_ids": dup,
        "orphan_atom_ids": orphans,
    }


def render_preview(payload, max_pages=3):
    lines = []
    src = payload["source"]
    cov = payload["coverage"]
    lines.append("# Structure candidates preview")
    lines.append("")
    lines.append(f"- source: `{src.get('kind')}`")
    if src.get("document_id"):
        lines.append(f"- document: `{src['document_id']}` title `{src.get('title')}` revision `{src.get('revision')}`")
    lines.append(f"- atoms: {cov['atom_count']}  candidates: {cov['candidate_count']}  coverage_ok: {cov['coverage_ok']}")
    lines.append(f"- orphans: {len(cov['orphan_atom_ids'])}  duplicates: {len(cov['duplicate_atom_ids'])}")
    lines.append("")
    lines.append("Column decisions (generic gutter search, not fixed coordinates):")
    lines.append("")
    for info in payload["pages"]:
        lines.append(
            f"- page {info['page']}: mode={info['mode']} gutter={info['gutter']} "
            f"wide_lines={info['wide']} body_lines={info['body']} body_h={info['body_height']}"
        )
    lines.append("")
    shown = 0
    current = None
    for cand in payload["candidates"]:
        if cand["page"] > max_pages:
            break
        if cand["page"] != current:
            current = cand["page"]
            lines.append(f"## Page {current}")
            lines.append("")
        spots = ",".join(cand["hard_spots"]) if cand["hard_spots"] else "-"
        ids = cand["atom_ids"]
        id_span = ids[0] if len(ids) == 1 else f"{ids[0]}..{ids[-1]} ({len(ids)})"
        text = cand["text_preview"] or "(empty)"
        lines.append(
            f"- `{cand['id']}` {cand['kind_hint']} col={cand['column']} conf={cand['confidence']} "
            f"atoms={id_span} spots={spots}"
        )
        lines.append(f"  - {text}")
        shown += 1
    lines.append("")
    lines.append(f"Preview items: {shown} (pages 1–{max_pages}). Full list is the JSON.")
    lines.append("")
    return "\n".join(lines)


def assemble(lines, pages, meta):
    candidates, page_info = build_candidates(lines, pages)
    cov = coverage(lines, candidates)
    hard = defaultdict(int)
    for cand in candidates:
        for flag in cand["hard_spots"]:
            hard[flag] += 1
    return {
        "tool": "structure-candidates",
        "version": "20261005b",
        "not_a_structure_submission": True,
        "source": meta,
        "pages": page_info,
        "candidates": candidates,
        "coverage": cov,
        "hard_spot_counts": dict(sorted(hard.items())),
        "orphan_atom_ids": cov["orphan_atom_ids"],
    }


def build_payload(doc):
    """Return a candidates payload for a loaded document dict.

    Raises ValueError if no atom has page and bbox, or if coverage fails.
    """
    if not any(_atom_has_geom(atom) for atom in doc.get("atoms") or []):
        raise ValueError("structure candidates need PDF atoms with page and bbox")
    lines, pages = lines_from_document(doc)
    meta = {
        "kind": "document",
        "document_id": doc.get("id"),
        "title": doc.get("title"),
        "revision": doc.get("revision"),
    }
    payload = assemble(lines, pages, meta)
    if not payload["coverage"]["coverage_ok"]:
        raise ValueError("structure candidate coverage failed")
    return payload
