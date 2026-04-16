"""Rule base CRUD store (v14).

1 .pl file = 1 named rule base. Flat directory under ``settings.rules_dir``.
Name resolution, dedup, and error → ExecutionResult conversion are the
caller's responsibility (server.py / reasoner.py). This module only provides
the raw CRUD primitives and raises ``RuleBaseError`` on failure.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from pathlib import Path

from prolog_reasoner.config import Settings
from prolog_reasoner.errors import RuleBaseError
from prolog_reasoner.executor import PrologExecutor
from prolog_reasoner.logger import SecureLogger
from prolog_reasoner.models import RuleBaseInfo

logger = SecureLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_METADATA_PATTERN = re.compile(
    r"^%\s*(description|tags)\s*:\s*(.*)$", re.IGNORECASE
)
_PL_SUFFIX = ".pl"


def dedup_names(names: list[str]) -> list[str]:
    """Drop duplicate rule base names, preserving first-occurrence order.

    Centralizes the ``list(dict.fromkeys(...))`` idiom used by server.py,
    reasoner.py and translator.py so all three stay in sync (§4.10).
    """
    return list(dict.fromkeys(names))


def _validate_name(name: str) -> None:
    """Raise RuleBaseError(RULEBASE_002) on invalid names."""
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise RuleBaseError(
            (
                f"Invalid rule base name {name!r}. Must match "
                f"[a-zA-Z0-9_-]{{1,64}} (no dots, slashes, spaces)."
            ),
            error_code="RULEBASE_002",
        )


def _read_utf8(path: Path) -> str:
    """Read UTF-8 file, stripping a leading BOM if present.

    ``utf-8-sig`` decodes identically to ``utf-8`` for BOM-less input, so
    using it primarily covers both cases. Plain ``utf-8`` would pass the
    BOM through as U+FEFF, which SWI-Prolog then rejects as a syntax error.
    """
    return path.read_text(encoding="utf-8-sig")


def _extract_metadata(text: str) -> tuple[str, list[str]]:
    """Extract ``description``/``tags`` from the leading comment block.

    Scans contiguous ``%`` lines from the start of the file, stopping at the
    first blank line, directive, or actual clause. Later entries for the
    same key override earlier ones.
    """
    description = ""
    tags: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            break
        if not stripped.startswith("%"):
            break
        m = _METADATA_PATTERN.match(stripped)
        if not m:
            continue
        key = m.group(1).lower()
        value = m.group(2).strip()
        if key == "description":
            description = value
        elif key == "tags":
            tags = [t.strip() for t in value.split(",") if t.strip()]
    return description, tags


def _not_found_error(name: str, available: list[str]) -> str:
    suggestions = difflib.get_close_matches(name, available, n=1, cutoff=0.6)
    msg = f"Rule base {name!r} not found."
    if suggestions:
        msg += f" Did you mean {suggestions[0]!r}?"
    shown = available[:5]
    more = f" (and {len(available) - 5} more)" if len(available) > 5 else ""
    msg += f" Available: {shown}{more}"
    return msg


class RuleBaseStore:
    """Filesystem-backed rule base CRUD."""

    def __init__(self, settings: Settings, executor: PrologExecutor) -> None:
        self.rules_dir: Path = settings.rules_dir
        self.max_size: int = settings.max_rule_size
        self._executor = executor

    def sync_bundled(self, bundled_dir: Path | None) -> None:
        """Copy-on-first-use: seed rules_dir from bundled_dir (v14, §4.10).

        Only ``.pl`` files are copied, content-only (source permissions are
        *not* preserved — bundled package files are often read-only, but
        ``rules_dir`` belongs to the user and must stay writable). Existing
        files in ``rules_dir`` are never overwritten (user changes win).
        No-op when ``bundled_dir`` is None, missing, or identical to
        ``rules_dir``.

        Callers: ``server.py._init()`` invokes this automatically on the
        MCP path. Library users who construct ``RuleBaseStore`` directly
        and want bundled fork content (Phase C) are responsible for
        calling ``sync_bundled()`` themselves before the first access.

        Raises:
            RuleBaseError(RULEBASE_004): I/O failure during copy (escalated
                to ConfigurationError(CONFIG_002) by the startup path).
        """
        if bundled_dir is None:
            return
        try:
            if not bundled_dir.is_dir():
                return
            # Self-copy guard (resolved-path compare).
            try:
                if bundled_dir.resolve() == self.rules_dir.resolve():
                    return
            except OSError:
                pass
            self.rules_dir.mkdir(parents=True, exist_ok=True)
            for src in bundled_dir.glob(f"*{_PL_SUFFIX}"):
                if not src.is_file():
                    continue
                target = self.rules_dir / src.name
                if target.exists():
                    continue
                # copyfile copies content only, not the source's mode bits.
                # Bundled package files may be read-only; the copy must remain
                # writable so users can edit / delete / overwrite their rules.
                shutil.copyfile(src, target)
        except OSError as exc:
            raise RuleBaseError(
                f"Failed to sync bundled rules from {bundled_dir}: {exc}",
                error_code="RULEBASE_004",
            ) from exc

    def list(self) -> list[RuleBaseInfo]:
        """Return RuleBaseInfo for every ``.pl`` file, sorted by name."""
        try:
            if not self.rules_dir.is_dir():
                return []
            entries: list[RuleBaseInfo] = []
            for path in self.rules_dir.glob(f"*{_PL_SUFFIX}"):
                if not path.is_file():
                    continue
                name = path.stem
                if not _NAME_PATTERN.fullmatch(name):
                    # Ignore files whose stem would fail name validation;
                    # they cannot be addressed via get/delete anyway.
                    continue
                try:
                    text = _read_utf8(path)
                except OSError as exc:
                    raise RuleBaseError(
                        f"Failed to read {path}: {exc}",
                        error_code="RULEBASE_004",
                    ) from exc
                description, tags = _extract_metadata(text)
                entries.append(
                    RuleBaseInfo(name=name, description=description, tags=tags)
                )
        except RuleBaseError:
            raise
        except OSError as exc:
            raise RuleBaseError(
                f"Failed to list rules_dir {self.rules_dir}: {exc}",
                error_code="RULEBASE_004",
            ) from exc
        entries.sort(key=lambda info: info.name)
        return entries

    def get(self, name: str) -> str:
        """Read rule base content.

        Raises:
            RuleBaseError(RULEBASE_002): invalid name.
            RuleBaseError(RULEBASE_001): rule base not found.
            RuleBaseError(RULEBASE_004): other I/O failure.
        """
        _validate_name(name)
        path = self.rules_dir / f"{name}{_PL_SUFFIX}"
        if not path.is_file():
            available = self._available_names_safe()
            raise RuleBaseError(
                _not_found_error(name, available),
                error_code="RULEBASE_001",
            )
        try:
            return _read_utf8(path)
        except OSError as exc:
            raise RuleBaseError(
                f"Failed to read rule base {name!r}: {exc}",
                error_code="RULEBASE_004",
            ) from exc

    async def save(self, name: str, content: str) -> bool:
        """Save (create or overwrite) a rule base.

        Validation order: name → size → parse-only syntax → atomic write.

        Returns:
            True if newly created, False if an existing file was overwritten.

        Raises:
            RuleBaseError(RULEBASE_002): invalid name.
            RuleBaseError(RULEBASE_005): content exceeds max_rule_size.
            RuleBaseError(RULEBASE_003): parse-only syntax error.
            RuleBaseError(RULEBASE_004): I/O failure.
        """
        _validate_name(name)

        size = len(content.encode("utf-8"))
        if size > self.max_size:
            raise RuleBaseError(
                (
                    f"Rule base exceeds max size {self.max_size} bytes "
                    f"(got {size}). Configurable via "
                    f"PROLOG_REASONER_MAX_RULE_SIZE."
                ),
                error_code="RULEBASE_005",
            )

        syntax_error = await self._executor.validate_syntax(content)
        if syntax_error is not None:
            raise RuleBaseError(
                f"Syntax error: {syntax_error}",
                error_code="RULEBASE_003",
            )

        target = self.rules_dir / f"{name}{_PL_SUFFIX}"
        existed = target.exists()
        try:
            self.rules_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=str(self.rules_dir)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fp:
                    fp.write(content)
                os.replace(tmp_path, target)
            except Exception:
                # Clean up the tmpfile on any failure path.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise RuleBaseError(
                f"Failed to write rule base {name!r}: {exc}",
                error_code="RULEBASE_004",
            ) from exc
        return not existed

    def delete(self, name: str) -> None:
        """Delete a rule base.

        Raises:
            RuleBaseError(RULEBASE_002): invalid name.
            RuleBaseError(RULEBASE_001): rule base not found.
            RuleBaseError(RULEBASE_004): I/O failure.
        """
        _validate_name(name)
        path = self.rules_dir / f"{name}{_PL_SUFFIX}"
        if not path.is_file():
            available = self._available_names_safe()
            raise RuleBaseError(
                _not_found_error(name, available),
                error_code="RULEBASE_001",
            )
        try:
            path.unlink()
        except OSError as exc:
            raise RuleBaseError(
                f"Failed to delete rule base {name!r}: {exc}",
                error_code="RULEBASE_004",
            ) from exc

    def _available_names_safe(self) -> list[str]:
        """Collect available names for error messages; swallow I/O errors."""
        try:
            if not self.rules_dir.is_dir():
                return []
            names = [
                p.stem
                for p in self.rules_dir.glob(f"*{_PL_SUFFIX}")
                if p.is_file() and _NAME_PATTERN.fullmatch(p.stem)
            ]
            names.sort()
            return names
        except OSError:
            return []
