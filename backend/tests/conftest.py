import importlib
import os
import functools
import shutil
import socket
import subprocess
import sys
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


def _run_command(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return
    raise RuntimeError(
        f"Command failed: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


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


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    initdb = _find_postgres_command("initdb")
    pg_ctl = _find_postgres_command("pg_ctl")
    createdb = _find_postgres_command("createdb")
    dropdb = _find_postgres_command("dropdb")

    if not initdb or not pg_ctl or not createdb or not dropdb:
        raise RuntimeError("PostgreSQL CLI tools are required for backend tests.")

    cluster_root = tmp_path_factory.mktemp("postgres-cluster")
    data_dir = cluster_root / "data"
    log_path = cluster_root / "postgres.log"
    port = _pick_free_port()
    database_name = "personal_spending_tracker_test"
    dsn = f"postgresql://postgres@localhost:{port}/{database_name}"

    _run_command(
        [
            initdb,
            "-D",
            str(data_dir),
            "--auth=trust",
            "--username",
            "postgres",
        ]
    )
    _run_command(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            f"-F -p {port} -c dynamic_shared_memory_type=posix",
            "start",
        ]
    )
    _run_command([createdb, "-U", "postgres", "-p", str(port), database_name])

    try:
        yield dsn
    finally:
        subprocess.run(
            [dropdb, "-U", "postgres", "-p", str(port), database_name],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "stop", "-m", "fast"],
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
