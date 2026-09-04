# 49. The shipped configuration is the product

Date: Sprint 62 (EASY INSTALL — desktop edition)

## Status

Accepted. Applies the easy-install requirement's central rule to the storage layer; the
editions it introduces are how later installer work will branch.

## Context

The easy-install requirement is one sentence with teeth: *"Any architectural choice that
improves theoretical flexibility but forces normal users to manually configure infrastructure
should be rejected unless required."* Its acceptance list describes a Windows machine with no
Python, no Node, no Docker, no Ollama and no developer tools.

Thursday's code had been careful about exactly this since ADR 0006: `build_state_store` picks
an in-process store when no Redis URL is set, `build_queue` does the same, SQLite is the
default driver, and `NullRepository` makes a database optional. Every port had its offline
adapter and every test ran on them.

None of that reached a user, because `settings.yaml` — the file a fresh install actually
loads — said `redis://localhost:6379/0`.

So the shipped configuration selected `RedisStateStore`, and `redis` is not a declared
dependency of this project. A normal user's first state operation raised
`ModuleNotFoundError: No module named 'redis'`: a Python import error, for a service they
never chose to need, on a machine where the correct implementation was sitting unused two
function calls away.

The same file left every `persist_*` flag off, so a desktop user could tell Thursday something,
restart their PC, and find it forgotten — with the SQLite file the flags would have written to
already created.

## Decision

**The shipped configuration is part of the product, and gets the same scrutiny as the code.**

A default that lives in a config file is not weaker than one in code — it is *stronger*,
because it is what actually runs. Twelve hundred tests passed while this was broken, and they
all passed for the same reason: the test fixture builds `Settings(...)` with its own values
and never reads the file a user reads. Code defaults were correct and irrelevant.

Concretely: `redis.url` is `null`, persistence is on, and both changes are in the file rather
than the code.

**`Settings.external_services()` makes "needs nothing" answerable.** It returns what somebody
would have to install and run before this configuration works. The desktop edition must return
an empty list, and a test asserts it. The installer, the health check and the suite then ask
the same question of the same code — so a future setting that quietly adds a dependency shows
up in all three at once, rather than in a support thread.

**An `edition` setting, defaulting to `desktop`.** `hub` is the multi-device deployment that
earns PostgreSQL and Redis by being multi-process; `developer` is `hub` with everything
visible. The requirement asks for exactly this split, and naming it in settings means the
health check can say *"start Redis"* rather than letting a connection error say it worse.

**Test ephemerality is now an explicit choice.** `settings.yaml` turns persistence on, so
`conftest`'s fixture turns it off in one visible line, and the four persistence tests that mean
"no database configured" say so with a named constant instead of relying on an unset default.
A test whose premise is an ambient default is a test that changes meaning when somebody changes
the default — which is what happened here, 107 times, the moment persistence went on.

## Consequences

A fresh install now needs nothing: SQLite in a file, state in the process, and memory that
survives a restart. `python -m apps.cli` and the API both come up from the shipped file alone.

Turning persistence on means a desktop install writes to disk from the first turn, which is the
intended behaviour and also means the migration path matters more than it did — a user's
database now has their data in it, so a future schema change has to migrate rather than
recreate. `alembic check` in CI already guards the schema; the restore path from Sprint 47 is
what covers the rest.

What this does **not** do: build an installer. There is no `ThursdaySetup.exe`, no bundled
runtime, no hardware detection driving a recommendation, and no setup wizard. This sprint
removed the reasons an installer would have needed to install PostgreSQL and Redis — which is
the part that had to be true before an installer could be simple, and is a long way from the
installer existing. The requirement's own acceptance test is "a fresh Windows machine", and
nothing here has been near one.
