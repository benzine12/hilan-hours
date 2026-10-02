# -*- coding: utf-8 -*-
"""English wording for Hilan's Hebrew, and Hebrew that reads the right way.

The terminals this tool runs in — anything built on xterm.js, Terminal.app,
iTerm2 — do not implement the Unicode bidirectional algorithm. They paint
characters in logical order, so a Hebrew word arrives spelled backwards. Known
vocabulary is translated; whatever is left is reordered for display.
"""
import pytest

from hilan.text import display, english, visual


class TestKnownVocabularyBecomesEnglish:
    @pytest.mark.parametrize(
        "hebrew, expected",
        [
            ("נוכחות", "present"),
            ("נכח", "present"),
            ("עבודה מהבית", "work from home"),
            ("חופשה", "vacation"),
            ("מחלה", "sick leave"),
            ("חג", "holiday"),
            ("ערב חג", "holiday eve"),
            ("חול המועד", "mid-festival"),
            ('ערב יוה"כ', "Yom Kippur eve"),
            ("קיים דיווח ללא פרויקט באותה השורה", "a row is reported without a project"),
        ],
    )
    def test_translation(self, hebrew, expected):
        assert english(hebrew) == expected

    def test_surrounding_whitespace_is_tolerated(self):
        assert english("  חופשה  ") == "vacation"

    def test_unknown_text_has_no_translation(self):
        assert english("דיווח ידני") is None

    def test_english_input_is_left_alone(self):
        assert english("vacation") is None


class TestVisualOrder:
    def test_a_hebrew_word_is_reversed_for_display(self):
        assert visual("חג") == "גח"

    def test_a_phrase_reverses_whole(self):
        assert visual("חג חופשה") == "השפוח גח"

    def test_latin_text_is_untouched(self):
        assert visual("worked 9:10") == "worked 9:10"

    def test_digits_between_hebrew_words_keep_their_order(self):
        assert visual("שעון 16:40 בבוקר") == "רקובב 16:40 ןועש"

    def test_latin_between_hebrew_words_keeps_its_order(self):
        assert visual("מנהל IT ראשי") == "ישאר IT להנמ"

    def test_trailing_digits_sit_outside_the_span(self):
        """The span has to end on a Hebrew letter, so a trailing number stays."""
        assert visual("נתוני שעון 16:40") == "ןועש ינותנ 16:40"

    def test_hebrew_embedded_in_english_reverses_only_itself(self):
        assert visual("note: חופשה (day off)") == "note: השפוח (day off)"

    def test_brackets_around_hebrew_stay_where_they_are(self):
        assert visual("(חג)") == "(גח)"

    def test_quotes_inside_a_word_survive(self):
        assert visual('יוה"כ') == 'כ"הוי'

    def test_empty_and_plain_strings(self):
        assert visual("") == ""
        assert visual("123") == "123"


class TestDisplayPrefersEnglish:
    def test_known_terms_come_back_in_english(self):
        assert display("חופשה") == "vacation"

    def test_unknown_hebrew_is_reordered(self):
        assert display("דיווח ידני") == visual("דיווח ידני")

    def test_none_stays_none(self):
        assert display(None) is None


class TestLogicalOverride:
    """A terminal that does implement bidi must not get pre-reversed text."""

    def test_env_switch_disables_reordering(self, monkeypatch):
        monkeypatch.setenv("HILAN_BIDI", "logical")
        assert visual("חג") == "חג"
        assert display("דיווח ידני") == "דיווח ידני"

    def test_translation_still_applies(self, monkeypatch):
        monkeypatch.setenv("HILAN_BIDI", "logical")
        assert display("חופשה") == "vacation"
