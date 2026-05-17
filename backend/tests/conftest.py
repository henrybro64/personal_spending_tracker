import importlib
import sys
import types
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)


@pytest.fixture
def api_main(monkeypatch: pytest.MonkeyPatch):
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

    api_main = importlib.import_module("backend.main")
    monkeypatch.setattr(api_main, "init_db", lambda *args, **kwargs: None)
    return api_main


@pytest.fixture
def client(api_main) -> TestClient:
    with TestClient(api_main.app) as test_client:
        yield test_client
