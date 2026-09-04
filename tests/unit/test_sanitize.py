"""Unit tests for filename sanitization and date parsing."""

import pytest


def test_sanitize_title_lowercases_and_replaces_spaces(mod):
    assert mod.sanitize_title('My Great Episode') == 'my_great_episode'


def test_sanitize_title_strips_special_characters(mod):
    result = mod.sanitize_title('Café & Special "Ep" 5!')
    # ASCII-normalized, lowercased, non [a-z0-9._-] removed.
    assert result == 'cafe_special_ep_5'


def test_sanitize_title_rfc822_date_prefix(mod):
    result = mod.sanitize_title('Hello World', 'Wed, 02 Oct 2002 13:00:00 GMT')
    assert result == '2002-10-02_hello_world'


def test_sanitize_title_timezone_offset_date_prefix(mod):
    result = mod.sanitize_title('Hello World', 'Wed, 02 Oct 2002 13:00:00 +0000')
    assert result == '2002-10-02_hello_world'


def test_sanitize_title_iso_date_prefix(mod):
    result = mod.sanitize_title('Hello World', '2002-10-02T13:00:00+00:00')
    assert result == '2002-10-02_hello_world'


def test_sanitize_title_preformatted_date(mod):
    result = mod.sanitize_title('Hello World', '2002-10-02')
    assert result == '2002-10-02_hello_world'


def test_sanitize_title_no_prefix_when_date_unparseable(mod):
    result = mod.sanitize_title('Hello World', 'not a date')
    assert result == 'hello_world'


def test_sanitize_title_blank_title_falls_back(mod):
    result = mod.sanitize_title('!!!  ---', None)
    assert result == 'untitled'


def test_sanitize_title_collapses_repeated_separators(mod):
    assert mod.sanitize_title('A__B--C__D') == 'a_b-c_d'


@pytest.mark.parametrize(
    'title,expected',
    [
        ('  LeadingTrailing  ', 'leadingtrailing'),
        ('UPPER CASE', 'upper_case'),
        ('hyphen-ated words', 'hyphen-ated_words'),
    ],
)
def test_sanitize_title_strips_edge_whitespace_and_dashes(mod, title, expected):
    assert mod.sanitize_title(title) == expected


def test_entry_date_prefix_from_published_parsed(mod):
    import time as _time

    entry = {'published_parsed': _time.struct_time((2021, 3, 4, 5, 6, 7, 0, 0, -1))}
    assert mod.entry_date_prefix(entry) == '2021-03-04'


def test_entry_date_prefix_prefers_published_parsed_over_string(mod):
    entry = {
        'published': 'Wed, 02 Oct 2002 13:00:00 GMT',
        'published_parsed': __import__('time').struct_time((2001, 1, 2, 0, 0, 0, 0, 0, -1)),
    }
    assert mod.entry_date_prefix(entry) == '2001-01-02'


def test_entry_date_prefix_none_when_no_date(mod):
    assert mod.entry_date_prefix({'title': 'x'}) is None


def test_sanitize_filename_from_entry(mod):
    entry = {
        'title': 'Episode One',
        'published': 'Wed, 02 Oct 2002 13:00:00 GMT',
    }
    assert mod.sanitize_filename_from_entry(entry) == '2002-10-02_episode_one'
