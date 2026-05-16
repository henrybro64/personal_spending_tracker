import importlib
import sys
import types
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers


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


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_upload_rejects_non_pdf_files(api_main) -> None:
    upload = UploadFile(
        file=BytesIO(b"not a pdf"),
        filename="statement.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await api_main.upload_pdf(file=upload)

    await upload.close()

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only PDF files are allowed"


def test_manual_transaction_rejects_empty_merchant(client: TestClient) -> None:
    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "   ",
            "amount": 42.5,
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Merchant is required."}


def test_manual_transaction_rejects_zero_amount(client: TestClient) -> None:
    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "amount": 0,
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Amount must be non-zero."}
