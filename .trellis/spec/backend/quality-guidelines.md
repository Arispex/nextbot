# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

Backend quality in this project is driven more by static analysis and typed helper patterns than by automated tests.

Detected tooling:
- Ruff
- Pyright

Current code style strongly prefers:
- `from __future__ import annotations`
- explicit return types on public functions
- small private normalization/validation helpers
- dataclasses for payload / config DTOs
- manual DB session control

---

## Required Patterns

- Use type hints broadly, especially in backend modules.
- Prefer small private helper functions for field normalization and validation.
- Reuse shared route helpers such as `api_success(...)`, `api_error(...)`, and `read_json_object(...)` for Web UI APIs.
- Close database sessions explicitly with `finally: session.close()`.
- For write operations, pair `commit()` with `rollback()` in exception paths.
- Keep frontend-facing API copy decoupled from backend success messages; backend errors should return reasons, not UI action text.

### Examples
- `server/settings_service.py` — typed helpers, normalization, atomic file write.
- `server/routes/webui_groups.py` — dataclass payload + field validators + API envelope usage.
- `server/routes/webui_users.py` — typed CRUD route flow with validation helpers.
- `nextbot/command_config.py` — dataclasses, validation, and runtime cache logic.

---

## Forbidden Patterns

- Do not return ad hoc JSON shapes from Web UI APIs when shared envelope helpers already exist.
- Do not leak unexpected exception text to API clients.
- Do not leave DB sessions open.
- Do not skip `rollback()` after a failed write.
- Do not add new backend code that logs secrets or tokens.
- Do not introduce a new migration framework or test conventions in docs unless the repository actually adopts them.

### Existing anti-patterns to be aware of
- Web UI token is currently logged in `server/web_server.py`; treat that as a risk, not a standard.
- Validation logic is duplicated between some bot plugins and Web UI routes; avoid copying more divergence unless necessary.

### NapCat file upload — do NOT use local paths
NapCat runs in a separate Docker container. When calling `upload_group_file` / `upload_private_file` via OneBot V11, the `file` parameter must NOT be a local filesystem path (e.g. `/tmp/foo.wld`) — NapCat cannot access the host's `/tmp`. Always pass `base64://<data>` directly:
```python
await bot.call_api("upload_group_file", group_id=..., file=f"base64://{b64}", name=file_name)
```

---

## Testing Requirements

There is currently **no dedicated automated test suite** in the repository.

In practice, backend changes are checked with:
- static analysis / syntax validation
- manual feature testing
- startup/runtime verification

### Tools detected
- `ruff` configured in `pyproject.toml`
- `pyright` configured in `pyproject.toml`

### Practical checks for backend work
- `uv run ruff check .`
- `uv run pyright`
- targeted syntax checks when doing narrow changes, for example `python3 -m py_compile ...`
- manual verification by starting the app: `uv run python bot.py`

If you add behavior that affects Web UI APIs or bot commands, manual end-to-end verification is currently expected.

---

## Code Review Checklist

Reviewers should check:
- Does the code match the existing module layout (`nextbot/` vs `server/`)?
- Are request/response envelopes consistent with `server/routes/__init__.py`?
- Are validation rules explicit and close to the route/domain code that uses them?
- Are DB sessions always closed?
- Is `rollback()` present for failed writes?
- Are unexpected exceptions logged and converted to sanitized `500 internal_error` responses?
- Are logs useful and free of sensitive data?
- If changing config/schema/bootstrap logic, does startup behavior in `bot.py` and `nextbot/db.py` still make sense?

---

## Tooling Reference

Relevant config and examples:
- `pyproject.toml` — Ruff and Pyright configuration
- `README.md` — `uv sync` and `uv run python bot.py`
- `server/screenshot.py` — Playwright setup note for render-related work

No CI workflow, test suite, `Makefile`, or migration tool config was found in the repository at the time these guidelines were written.
