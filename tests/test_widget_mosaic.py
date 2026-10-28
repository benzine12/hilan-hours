# -*- coding: utf-8 -*-
"""The widget's mosaic, drawn on every size, in both themes.

Runs the whole widget on the made-up phone (phone.py) with the drawing fakes of
scriptable.py, which note any colour that is not one, any coordinate that is
not a number and anything drawn outside its canvas. What each day's tile says
is held to Python by test_widget_parity.py; this is about the drawing.
"""
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from phone import phone
from test_election_day import october
from test_widget_network import KEYS, WIDGET, routes
from test_widget_parity import NODE, random_month

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

AFTERNOON = "2026-09-22T11:35:00Z"            # 14:35 in Israel, as in the README
FAMILIES = ["small", "medium", "large", "extraLarge",
            "accessoryRectangular", "accessoryCircular", "accessoryInline"]


def show(tmp_path, html, family, *, now=AFTERNOON, parameter=None, **extra):
    return phone(tmp_path, {"routes": routes(html), "keychain": KEYS, "now": now,
                            "config": {**WIDGET, "widgetFamily": family},
                            "parameter": parameter, **extra})


class TestEverySize:
    @pytest.mark.parametrize("parameter", [None, "light"])
    @pytest.mark.parametrize("family", FAMILIES)
    def test_it_draws_inside_its_canvas(self, tmp_path, september_html, family, parameter):
        result = show(tmp_path, september_html, family, parameter=parameter)
        assert result["error"] is None and result["problems"] == [], result["problems"][:5]

    @pytest.mark.parametrize("family", ["small", "medium", "large", "extraLarge", "accessoryRectangular"])
    def test_the_mosaic_is_on_it(self, tmp_path, september_html, family):
        images = show(tmp_path, september_html, family)["images"]
        assert len(images) == 1 and images[0]["paths"] > 0


class TestItFitsAnyPhone:
    """A widget is 292 points wide on one phone and 364 on another.

    An image given a fixed size wider than the widget it lands in pushes the
    whole column out past the widget's edges; one left to fit scales to it.
    """

    @pytest.mark.parametrize("family", ["small", "medium", "large", "extraLarge", "accessoryRectangular"])
    def test_no_image_is_given_a_size_of_its_own(self, tmp_path, september_html, family):
        placed = show(tmp_path, september_html, family)["placed"]
        assert placed and all("imageSize" not in p and p.get("resizable") is not False for p in placed), placed


class TestTheWeek:
    """The medium widget: 20-26 September, with today on the Tuesday."""

    def drawn(self, tmp_path, html, **kw):
        return show(tmp_path, html, "medium", **kw)["images"][0]["texts"]

    def test_every_day_is_named(self, tmp_path, september_html):
        texts = self.drawn(tmp_path, september_html)
        assert [t for t in texts if t.isalpha() and t.isupper() and len(t) == 3] == \
            ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]

    def test_each_day_says_what_it_holds(self, tmp_path, september_html):
        texts = self.drawn(tmp_path, september_html)
        assert "HOLIDAY" in texts                      # 21/09
        assert "6.33" in texts and "OF 9.00" in texts  # today, so far
        assert texts.count("AHEAD") == 2               # 23 and 24
        assert "4.00" in texts                         # the eve of Yom Kippur, worked

    def test_its_dates_are_in_the_header(self, tmp_path, september_html):
        assert "  20/09–26/09" in show(tmp_path, september_html, "medium")["texts"]

    def test_election_day_is_a_holiday_not_a_day_short(self, tmp_path):
        texts = self.drawn(tmp_path, october(), now="2026-10-26T09:00:00Z")
        assert "HOLIDAY" in texts and "27" in texts
        assert not any(t.startswith("-") for t in texts)


class TestTheMonth:
    def test_every_day_is_numbered(self, tmp_path, september_html):
        texts = show(tmp_path, september_html, "large")["images"][0]["texts"]
        assert [str(d) for d in range(1, 31)] == [t for t in texts if t.isdigit()]

    def test_a_day_short_by_more_than_an_hour_is_the_strongest_orange(self, tmp_path, september_html):
        colours = show(tmp_path, september_html, "small")["images"][0]["colours"]
        assert "#FFB340/1" in colours                  # 06/09, 1.17 short
        assert "#FFB340/0.75" in colours               # 02/09, half an hour short

    def test_light_is_drawn_in_its_own_colours(self, tmp_path, september_html):
        dark = set(show(tmp_path, september_html, "large")["images"][0]["colours"])
        light = set(show(tmp_path, september_html, "large", parameter="light")["images"][0]["colours"])
        assert any(c.startswith("#4C9DFF") for c in dark) and not any(c.startswith("#4C9DFF") for c in light)
        assert any(c.startswith("#2563EB") for c in light)

    @pytest.mark.parametrize("parameter", [" Light ", "LIGHT"])
    def test_the_parameter_is_read_loosely(self, tmp_path, september_html, parameter):
        colours = show(tmp_path, september_html, "large", parameter=parameter)["images"][0]["colours"]
        assert any(c.startswith("#2563EB") for c in colours)


class TestAnOlderCache:
    def test_a_figure_saved_before_the_mosaic_still_shows(self, tmp_path, september_html):
        """A cache saved by the previous version has no mosaic; the figures still show."""
        first = show(tmp_path, september_html, "medium", now="2026-09-22T09:00:00Z")
        import json
        saved = {name: json.loads(body) if name.endswith(".json") and "analysis" in name else body
                 for name, body in first["files"].items()}
        for name, body in saved.items():
            if isinstance(body, dict):
                body.pop("mosaic")
                saved[name] = json.dumps(body)
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"fail": "offline"}]),
                                  "keychain": KEYS, "files": saved, "now": "2026-09-22T10:00:00Z",
                                  "config": {**WIDGET, "widgetFamily": "large"}})
        assert result["error"] is None and result["problems"] == [] and result["images"] == []
        assert any("as of" in t for t in result["texts"])


class TestRandomMonths:
    def test_any_month_fits(self, tmp_path):
        """Months that start on any weekday, six rows of weeks included, at any hour."""
        rng = random.Random(20261028)
        problems = []
        for _ in range(12):
            html, moments = random_month(rng)
            when = moments[0].replace(tzinfo=ZoneInfo("Asia/Jerusalem")).astimezone(timezone.utc)
            for family in ("small", "medium", "large"):
                result = show(tmp_path, html, family, now=when.strftime("%Y-%m-%dT%H:%M:%SZ"))
                if result["error"] or result["problems"]:
                    problems.append((moments[0], family, result["error"], result["problems"][:3]))
        assert not problems, problems[:5]
