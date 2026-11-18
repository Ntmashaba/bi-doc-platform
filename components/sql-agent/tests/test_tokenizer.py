"""Tokenizer tests: strings, comments, identifiers, GO batch splitting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.tokenizer import lex, statements


def words(src):
    return [t for t in lex(src) if t.kind == "word"]


def test_line_comment_skipped():
    toks = lex("SELECT 1 -- sp_add_job fake\nSELECT 2")
    assert not any("sp_add_job" in t.text for t in toks)


def test_block_comment_nested():
    toks = lex("EXEC a /* outer /* inner */ still comment */ EXEC b")
    execs = [t.text for t in toks if t.kind == "word" and t.text == "EXEC"]
    assert execs == ["EXEC", "EXEC"]


def test_string_doubled_quotes():
    toks = lex("N'it''s a job'")
    assert toks[0].kind == "string"
    assert toks[0].text == "it's a job"


def test_string_prefix_n_case_insensitive():
    assert lex("n'x'")[0].kind == "string"
    assert lex("N'x'")[0].kind == "string"


def test_string_not_confused_with_ident():
    # word ending in N directly glued to the quote is not an N-string
    toks = lex("XN'foo'")
    assert toks[0].kind == "word" and toks[0].text == "XN"
    assert toks[1].kind == "string" and toks[1].text == "foo"


def test_bracketed_ident_with_escaped_bracket():
    toks = lex("[weird]]name]")
    assert toks[0].kind == "ident"
    assert toks[0].text == "weird]name"


def test_quoted_ident():
    toks = lex('"My Job"')
    assert toks[0].kind == "ident" and toks[0].text == "My Job"


def test_variables_and_system_functions():
    kinds = [(t.kind, t.text) for t in lex("@x @@ROWCOUNT = 1")]
    assert ("var", "@x") in kinds
    assert ("var", "@@ROWCOUNT") in kinds


def test_go_splits_batches():
    src = "EXEC a\nGO\nEXEC b"
    batches = statements(src)
    assert len(batches) == 2


def test_go_case_and_whitespace():
    src = "EXEC a\n  go  \nEXEC b\n\tGo\t-- trailing comment\nEXEC c"
    assert len(statements(src)) == 3


def test_go_inside_string_does_not_split():
    src = "EXEC a @c = N'\nGO\n'\nEXEC b"
    assert len(statements(src)) == 1


def test_go_inside_block_comment_does_not_split():
    src = "EXEC a\n/*\nGO\n*/\nEXEC b"
    assert len(statements(src)) == 1


def test_go_inside_line_comment_does_not_split():
    src = "EXEC a -- GO\nEXEC b"
    assert len(statements(src)) == 1


def test_go_as_part_of_word_does_not_split():
    src = "EXEC a\nGONE\nEXEC b"
    assert len(statements(src)) == 1


def test_go_repeat_count_repeats_batch():
    src = "EXEC a\nGO 3\nEXEC b"
    batches = statements(src)
    assert len(batches) == 4  # a x3, b x1


def test_no_go_returns_single_batch():
    assert len(statements("EXEC a\nEXEC b")) == 1
    assert statements("") == []
