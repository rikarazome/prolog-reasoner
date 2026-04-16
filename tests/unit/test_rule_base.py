"""Unit tests for RuleBaseStore (v14).

Requires SWI-Prolog for the parse-only syntax validation path (save).
Other tests exercise only the filesystem primitives.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import RuleBaseError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.models import RuleBaseInfo
from prolog_reasoner.rule_base import RuleBaseStore


def _make_store(
    tmp_path: Path,
    *,
    max_rule_size: int = 1_048_576,
    bundled_rules_dir: Path | None = None,
) -> RuleBaseStore:
    settings = Settings(
        llm_api_key="dummy",
        swipl_path="swipl",
        execution_timeout_seconds=5.0,
        rules_dir=tmp_path / "rules",
        bundled_rules_dir=bundled_rules_dir,
        max_rule_size=max_rule_size,
    )
    executor = PrologExecutor(settings)
    return RuleBaseStore(settings, executor)


class TestValidateName:
    """RULEBASE_002 is raised for invalid names; no filesystem access."""

    @pytest.mark.parametrize(
        "name",
        [
            "",                     # empty
            "a" * 65,               # too long
            "piece moves",          # space
            "piece.moves",          # dot
            "piece/moves",          # slash
            "../escape",            # traversal attempt
            "name!",                # punctuation
            "日本語",                # non-ASCII
        ],
    )
    def test_invalid_names_rejected_by_get(self, tmp_path, name):
        store = _make_store(tmp_path)
        with pytest.raises(RuleBaseError) as excinfo:
            store.get(name)
        assert excinfo.value.error_code == "RULEBASE_002"

    @pytest.mark.parametrize(
        "name",
        [
            "a",
            "piece_moves",
            "piece-moves",
            "PieceMoves",
            "chess_v2",
            "a" * 64,
        ],
    )
    def test_valid_names_pass_validation(self, tmp_path, name):
        store = _make_store(tmp_path)
        # No file yet → RULEBASE_001, never RULEBASE_002.
        with pytest.raises(RuleBaseError) as excinfo:
            store.get(name)
        assert excinfo.value.error_code == "RULEBASE_001"

    def test_invalid_name_rejected_by_delete(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(RuleBaseError) as excinfo:
            store.delete("bad/name")
        assert excinfo.value.error_code == "RULEBASE_002"

    @pytest.mark.asyncio
    async def test_invalid_name_rejected_by_save(self, tmp_path):
        """save() must reject the name before touching the executor."""
        store = _make_store(tmp_path)
        with pytest.raises(RuleBaseError) as excinfo:
            await store.save("bad name", "fact(1).")
        assert excinfo.value.error_code == "RULEBASE_002"
        # No file was created.
        assert list((tmp_path / "rules").glob("*")) == [] or not (tmp_path / "rules").exists()


class TestList:
    def test_list_missing_dir_returns_empty(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.list() == []

    def test_list_sorts_by_name(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        (tmp_path / "rules" / "alpha.pl").write_text("a.\n", encoding="utf-8")
        (tmp_path / "rules" / "beta.pl").write_text("b.\n", encoding="utf-8")

        names = [info.name for info in store.list()]
        assert names == ["alpha", "beta", "chess"]

    def test_list_ignores_non_pl_files(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        (tmp_path / "rules" / "README.md").write_text("# docs\n", encoding="utf-8")
        (tmp_path / "rules" / "notes.txt").write_text("hi\n", encoding="utf-8")

        names = [info.name for info in store.list()]
        assert names == ["chess"]

    def test_list_ignores_invalid_stem(self, tmp_path):
        """Files whose stem fails name validation cannot be addressed via
        get/delete, so list() must skip them to stay consistent."""
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "bad name.pl").write_text("x.\n", encoding="utf-8")
        (tmp_path / "rules" / "chess.pl").write_text("piece(king).\n", encoding="utf-8")

        names = [info.name for info in store.list()]
        assert names == ["chess"]

    def test_list_extracts_metadata(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text(
            "% description: Chess piece movement rules\n"
            "% tags: chess, game, boardgame\n"
            "piece(king).\n",
            encoding="utf-8",
        )
        infos = store.list()
        assert len(infos) == 1
        assert infos[0] == RuleBaseInfo(
            name="chess",
            description="Chess piece movement rules",
            tags=["chess", "game", "boardgame"],
        )

    def test_list_without_metadata_uses_defaults(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        infos = store.list()
        assert infos == [RuleBaseInfo(name="chess", description="", tags=[])]

    def test_metadata_block_stops_at_blank_line(self, tmp_path):
        """Metadata after a blank line is ignored even if the comment looks right."""
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text(
            "% description: Real description\n"
            "\n"
            "% description: Ignored because of blank line\n"
            "piece(king).\n",
            encoding="utf-8",
        )
        infos = store.list()
        assert infos[0].description == "Real description"

    def test_metadata_block_stops_at_clause(self, tmp_path):
        """Once we hit a non-comment line, metadata scanning stops."""
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text(
            "% description: Top description\n"
            "piece(king).\n"
            "% description: Ignored — mid-file\n"
            "% tags: ignored\n",
            encoding="utf-8",
        )
        infos = store.list()
        assert infos[0].description == "Top description"
        assert infos[0].tags == []

    def test_metadata_later_entries_override(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text(
            "% description: First\n"
            "% description: Second\n"
            "piece(king).\n",
            encoding="utf-8",
        )
        infos = store.list()
        assert infos[0].description == "Second"

    def test_tags_split_and_strip(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text(
            "% tags:   chess ,  game  ,   ,boardgame\n"
            "piece(king).\n",
            encoding="utf-8",
        )
        infos = store.list()
        assert infos[0].tags == ["chess", "game", "boardgame"]


class TestGet:
    def test_get_returns_file_contents(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        body = "piece(king).\npiece(queen).\n"
        (tmp_path / "rules" / "chess.pl").write_text(body, encoding="utf-8")
        assert store.get("chess") == body

    def test_get_missing_raises_001_with_suggestion(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "piece_moves.pl").write_text("king(e1).\n", encoding="utf-8")
        with pytest.raises(RuleBaseError) as excinfo:
            store.get("piece_move")  # close to piece_moves
        assert excinfo.value.error_code == "RULEBASE_001"
        assert "piece_moves" in str(excinfo.value)

    def test_get_missing_includes_available_list(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "chess.pl").write_text("x.\n", encoding="utf-8")
        with pytest.raises(RuleBaseError) as excinfo:
            store.get("completely_different_name")
        assert excinfo.value.error_code == "RULEBASE_001"
        assert "chess" in str(excinfo.value)

    def test_get_utf8_bom_fallback(self, tmp_path):
        """BOM-prefixed files should be decoded via utf-8-sig."""
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        path = tmp_path / "rules" / "chess.pl"
        path.write_bytes("\ufeffpiece(king).\n".encode("utf-8"))
        content = store.get("chess")
        # BOM must be stripped by utf-8-sig so the round-tripped content is clean.
        assert content == "piece(king).\n"

    def test_get_utf8_content(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        body = 'greeting("こんにちは").\n'
        (tmp_path / "rules" / "chess.pl").write_text(body, encoding="utf-8")
        assert store.get("chess") == body


class TestSave:
    @pytest.mark.asyncio
    async def test_save_creates_file(self, tmp_path):
        store = _make_store(tmp_path)
        created = await store.save("chess", "piece(king).\n")
        assert created is True
        assert (tmp_path / "rules" / "chess.pl").read_text(encoding="utf-8") == "piece(king).\n"

    @pytest.mark.asyncio
    async def test_save_overwrite_returns_false(self, tmp_path):
        store = _make_store(tmp_path)
        await store.save("chess", "piece(king).\n")
        overwritten = await store.save("chess", "piece(queen).\n")
        assert overwritten is False
        assert (tmp_path / "rules" / "chess.pl").read_text(encoding="utf-8") == "piece(queen).\n"

    @pytest.mark.asyncio
    async def test_save_creates_rules_dir(self, tmp_path):
        rules_dir = tmp_path / "rules"
        assert not rules_dir.exists()
        store = _make_store(tmp_path)
        await store.save("chess", "piece(king).\n")
        assert rules_dir.is_dir()

    @pytest.mark.asyncio
    async def test_save_rejects_syntax_error(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(RuleBaseError) as excinfo:
            await store.save("chess", "piece(king")  # missing paren + period
        assert excinfo.value.error_code == "RULEBASE_003"
        assert not (tmp_path / "rules" / "chess.pl").exists()

    @pytest.mark.asyncio
    async def test_save_rejects_oversize(self, tmp_path):
        store = _make_store(tmp_path, max_rule_size=100)
        big = "fact(" + ("a" * 200) + ").\n"
        with pytest.raises(RuleBaseError) as excinfo:
            await store.save("chess", big)
        assert excinfo.value.error_code == "RULEBASE_005"
        assert not (tmp_path / "rules" / "chess.pl").exists()

    @pytest.mark.asyncio
    async def test_save_validation_order_size_before_syntax(self, tmp_path):
        """Size check runs before executor-backed syntax check (cheaper path)."""
        store = _make_store(tmp_path, max_rule_size=10)
        # Oversize AND syntax-broken — size wins because it's checked first.
        with pytest.raises(RuleBaseError) as excinfo:
            await store.save("chess", "bad(code" * 100)
        assert excinfo.value.error_code == "RULEBASE_005"

    @pytest.mark.asyncio
    async def test_save_accepts_directives_with_operators(self, tmp_path):
        """op/3 directives must be applied during parse-only validation
        so subsequent uses of the operator parse correctly."""
        store = _make_store(tmp_path)
        content = (
            ":- op(700, xfx, beats).\n"
            "rock beats scissors.\n"
        )
        await store.save("rps", content)
        assert (tmp_path / "rules" / "rps.pl").exists()

    @pytest.mark.asyncio
    async def test_save_does_not_execute_other_directives(self, tmp_path):
        """Parse-only validation must not run :- halt or arbitrary goals.
        If it did, this save would fail because halt terminates the process
        before parsing completes (exit code != 0 would surface as syntax error).
        """
        store = _make_store(tmp_path)
        # A non-op directive: consult would execute it; parse-only just reads it.
        content = ":- write('should_not_run').\nfact(1).\n"
        await store.save("test", content)
        saved = (tmp_path / "rules" / "test.pl").read_text(encoding="utf-8")
        assert saved == content

    @pytest.mark.asyncio
    async def test_save_is_atomic_no_tmpfile_leak(self, tmp_path):
        """After a successful save, only the target file remains — no
        .{name}.*.tmp leftovers from the mkstemp/replace dance."""
        store = _make_store(tmp_path)
        await store.save("chess", "piece(king).\n")
        entries = sorted(p.name for p in (tmp_path / "rules").iterdir())
        assert entries == ["chess.pl"]

    @pytest.mark.asyncio
    async def test_save_leaves_no_tmpfile_on_syntax_error(self, tmp_path):
        """Validation rejection must not leave tmpfiles behind."""
        (tmp_path / "rules").mkdir()
        store = _make_store(tmp_path)
        with pytest.raises(RuleBaseError):
            await store.save("chess", "bad(")
        entries = list((tmp_path / "rules").iterdir())
        assert entries == []

    @pytest.mark.asyncio
    async def test_save_content_with_leading_metadata_preserved(self, tmp_path):
        store = _make_store(tmp_path)
        content = (
            "% description: Chess movement\n"
            "% tags: chess, game\n"
            "piece(king).\n"
        )
        await store.save("chess", content)
        assert (tmp_path / "rules" / "chess.pl").read_text(encoding="utf-8") == content
        infos = store.list()
        assert infos[0].description == "Chess movement"
        assert infos[0].tags == ["chess", "game"]


class TestDelete:
    def test_delete_removes_file(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        path = tmp_path / "rules" / "chess.pl"
        path.write_text("x.\n", encoding="utf-8")
        store.delete("chess")
        assert not path.exists()

    def test_delete_missing_raises_001(self, tmp_path):
        store = _make_store(tmp_path)
        (tmp_path / "rules").mkdir()
        with pytest.raises(RuleBaseError) as excinfo:
            store.delete("chess")
        assert excinfo.value.error_code == "RULEBASE_001"


class TestSyncBundled:
    def test_sync_none_is_noop(self, tmp_path):
        store = _make_store(tmp_path, bundled_rules_dir=None)
        store.sync_bundled(None)
        assert not (tmp_path / "rules").exists()

    def test_sync_missing_dir_is_noop(self, tmp_path):
        bundled = tmp_path / "does_not_exist"
        store = _make_store(tmp_path, bundled_rules_dir=bundled)
        store.sync_bundled(bundled)
        assert not (tmp_path / "rules").exists()

    def test_sync_copies_pl_files(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        (bundled / "move_eval.pl").write_text("evaluation(0).\n", encoding="utf-8")

        store = _make_store(tmp_path, bundled_rules_dir=bundled)
        store.sync_bundled(bundled)

        rules = tmp_path / "rules"
        assert (rules / "chess.pl").read_text(encoding="utf-8") == "piece(king).\n"
        assert (rules / "move_eval.pl").read_text(encoding="utf-8") == "evaluation(0).\n"

    def test_sync_ignores_non_pl(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        (bundled / "README.md").write_text("docs\n", encoding="utf-8")

        store = _make_store(tmp_path, bundled_rules_dir=bundled)
        store.sync_bundled(bundled)

        rules = tmp_path / "rules"
        assert (rules / "chess.pl").exists()
        assert not (rules / "README.md").exists()

    def test_sync_does_not_overwrite_existing(self, tmp_path):
        """User edits always win — re-syncing must not clobber the rules_dir copy."""
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "chess.pl").write_text("piece(king).\n", encoding="utf-8")
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "chess.pl").write_text("piece(queen).\n", encoding="utf-8")  # user edit

        store = _make_store(tmp_path, bundled_rules_dir=bundled)
        store.sync_bundled(bundled)

        assert (rules / "chess.pl").read_text(encoding="utf-8") == "piece(queen).\n"

    def test_sync_self_copy_guard(self, tmp_path):
        """If bundled_rules_dir resolves to rules_dir, sync must no-op
        (guards against wiping the user's own files)."""
        shared = tmp_path / "rules"
        shared.mkdir()
        (shared / "chess.pl").write_text("piece(king).\n", encoding="utf-8")

        settings = Settings(
            llm_api_key="dummy",
            swipl_path="swipl",
            rules_dir=shared,
            bundled_rules_dir=shared,
        )
        executor = PrologExecutor(settings)
        store = RuleBaseStore(settings, executor)

        store.sync_bundled(shared)
        # File is untouched; no duplicate entries, no overwrite.
        assert (shared / "chess.pl").read_text(encoding="utf-8") == "piece(king).\n"


class TestIntegration:
    """Higher-level flows combining CRUD operations."""

    @pytest.mark.asyncio
    async def test_save_list_get_delete_roundtrip(self, tmp_path):
        store = _make_store(tmp_path)
        await store.save(
            "chess",
            "% description: Chess piece movement\n"
            "% tags: chess, game\n"
            "piece(king).\n",
        )
        await store.save("openings", "% description: Opening book\nopening(italian).\n")

        infos = store.list()
        assert [i.name for i in infos] == ["chess", "openings"]
        assert infos[0].description == "Chess piece movement"
        assert infos[0].tags == ["chess", "game"]

        chess_src = store.get("chess")
        assert "piece(king)" in chess_src

        store.delete("chess")
        remaining = [i.name for i in store.list()]
        assert remaining == ["openings"]

    @pytest.mark.asyncio
    async def test_save_then_sync_preserves_user_copy(self, tmp_path):
        """User-saved copy must survive a subsequent sync_bundled call."""
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "chess.pl").write_text("piece(pawn).\n", encoding="utf-8")

        store = _make_store(tmp_path, bundled_rules_dir=bundled)
        await store.save("chess", "piece(king).\n")  # user wins
        store.sync_bundled(bundled)

        assert store.get("chess") == "piece(king).\n"
