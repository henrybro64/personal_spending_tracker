import importlib
import os
import functools
import shutil
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_command(
    command: list[str],
    *,
    log_path: Path | None = None,
) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return

    log_excerpt = ""
    if log_path and log_path.exists():
        log_excerpt = f"\npostgres log:\n{log_path.read_text(encoding='utf-8')[-4000:]}\n"

    raise RuntimeError(
        f"Command failed: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
        f"{log_excerpt}"
    )


def _wait_for_postgres(dsn: str, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as connection:
                connection.execute("SELECT 1")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)

    raise RuntimeError(f"PostgreSQL did not become ready in time: {last_error}")


@functools.lru_cache(maxsize=1)
def _pg_bindir() -> str | None:
    pg_config = shutil.which("pg_config")
    if not pg_config:
        return None

    completed = subprocess.run(
        [pg_config, "--bindir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None

    bindir = completed.stdout.strip()
    return bindir or None


def _find_postgres_command(command_name: str) -> str | None:
    discovered = shutil.which(command_name)
    if discovered:
        return discovered

    bindir = _pg_bindir()
    if not bindir:
        return None

    candidate = Path(bindir) / command_name
    return str(candidate) if candidate.exists() else None


def _start_ephemeral_postgres(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[str, dict[str, object]]:
    initdb = _find_postgres_command("initdb")
    pg_ctl = _find_postgres_command("pg_ctl")
    createdb = _find_postgres_command("createdb")
    dropdb = _find_postgres_command("dropdb")

    if not initdb or not pg_ctl or not createdb or not dropdb:
        raise RuntimeError(
            "PostgreSQL CLI tools are required for local backend tests, "
            "or set TEST_DATABASE_URL to an existing database."
        )

    cluster_root = tmp_path_factory.mktemp("postgres-cluster")
    data_dir = cluster_root / "data"
    log_path = cluster_root / "postgres.log"
    socket_dir = cluster_root / "sockets"
    socket_dir.mkdir(parents=True, exist_ok=True)
    port = _pick_free_port()
    database_name = "personal_spending_tracker_test"
    admin_dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    dsn = f"postgresql://postgres@127.0.0.1:{port}/{database_name}"

    _run_command(
        [
            initdb,
            "-D",
            str(data_dir),
            "--auth=trust",
            "--username",
            "postgres",
        ],
        log_path=log_path,
    )
    pg_options = (
        f"-F -p {port} "
        f"-c listen_addresses=127.0.0.1 "
        f"-c unix_socket_directories={socket_dir} "
        f"-c shared_buffers=64MB "
        f"-c max_connections=50"
    )
    _run_command(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            pg_options,
            "start",
        ],
        log_path=log_path,
    )
    _wait_for_postgres(admin_dsn)
    _run_command(
        [createdb, "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", database_name],
        log_path=log_path,
    )
    _wait_for_postgres(dsn)

    return dsn, {
        "dropdb": dropdb,
        "pg_ctl": pg_ctl,
        "data_dir": data_dir,
        "port": port,
        "database_name": database_name,
    }


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    configured = os.getenv("TEST_DATABASE_URL")
    if os.getenv("CI", "").lower() == "true" and not configured:
        raise RuntimeError(
            "TEST_DATABASE_URL must be set in CI. "
            "Configure the postgres service in .github/workflows/ci.yml."
        )

    if configured:
        import backend.database as database

        database.init_db(configured)
        _wait_for_postgres(configured)
        yield configured
        return

    dsn, teardown = _start_ephemeral_postgres(tmp_path_factory)

    try:
        yield dsn
    finally:
        subprocess.run(
            [
                teardown["dropdb"],
                "-h",
                "127.0.0.1",
                "-p",
                str(teardown["port"]),
                "-U",
                "postgres",
                teardown["database_name"],
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                teardown["pg_ctl"],
                "-D",
                str(teardown["data_dir"]),
                "stop",
                "-m",
                "fast",
            ],
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def reset_database(database_url: str) -> None:
    import backend.database as database

    database.init_db(database_url)

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    audit_logs,
                    user_sessions,
                    transactions,
                    statement_uploads,
                    users
                RESTART IDENTITY CASCADE
                """
            )
        connection.commit()


@pytest.fixture
def api_main(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    reset_database,
    tmp_path: Path,
):
    pdfplumber_module = types.ModuleType("pdfplumber")
    pdfplumber_module.open = lambda *args, **kwargs: None
    pdfplumber_utils_module = types.ModuleType("pdfplumber.utils")
    pdfplumber_exceptions_module = types.ModuleType("pdfplumber.utils.exceptions")
    pdfplumber_exceptions_module.PdfminerException = Exception

    pdfminer_module = types.ModuleType("pdfminer")
    pdfminer_pdfdocument_module = types.ModuleType("pdfminer.pdfdocument")
    pdfminer_pdfdocument_module.PDFEncryptionError = Exception
    pdfminer_pdfdocument_module.PDFPasswordIncorrect = Exception
    python_multipart_module = types.ModuleType("python_multipart")
    python_multipart_module.__version__ = "0.0.20"

    monkeypatch.setitem(sys.modules, "pdfplumber", pdfplumber_module)
    monkeypatch.setitem(sys.modules, "pdfplumber.utils", pdfplumber_utils_module)
    monkeypatch.setitem(
        sys.modules,
        "pdfplumber.utils.exceptions",
        pdfplumber_exceptions_module,
    )
    monkeypatch.setitem(sys.modules, "pdfminer", pdfminer_module)
    monkeypatch.setitem(
        sys.modules,
        "pdfminer.pdfdocument",
        pdfminer_pdfdocument_module,
    )
    monkeypatch.setitem(sys.modules, "python_multipart", python_multipart_module)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("BATCH_DATA_DIR", str(tmp_path / "batch-data"))
    monkeypatch.setenv("BATCH_PDF_SOURCE_DIR", str(tmp_path / "batch-pdf"))
    monkeypatch.setenv("BATCH_TEXT_SOURCE_DIR", str(tmp_path / "batch-text"))

    if "backend.main" in sys.modules:
        api_main = importlib.reload(sys.modules["backend.main"])
    else:
        api_main = importlib.import_module("backend.main")
    return api_main


@pytest.fixture
def client(api_main) -> TestClient:
    with TestClient(api_main.app) as test_client:
        yield test_client
