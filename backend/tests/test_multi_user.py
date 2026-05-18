from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.requests import Request


def _register(
    client: TestClient,
    *,
    email: str,
    password: str = "password123",
    display_name: str,
) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
        },
    )
    assert response.status_code == 201


def _login(
    client: TestClient,
    *,
    email: str,
    password: str = "password123",
) -> None:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200


def _logout(client: TestClient) -> None:
    response = client.post("/auth/logout")
    assert response.status_code == 200


def _upload_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    request.state.request_id = "upload-request-id"
    return request


def test_register_accepts_plain_username(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": "henrybro",
            "password": "password123",
            "display_name": "Henry",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["email"] == "henrybro"


def test_users_only_see_their_own_manual_transactions(client: TestClient) -> None:
    _register(client, email="alice@example.com", display_name="Alice")
    alice_manual = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Alice Coffee",
            "amount": 12.5,
            "category": None,
            "payment_method": "Cash",
        },
    )
    assert alice_manual.status_code == 201
    _logout(client)

    _register(client, email="bob@example.com", display_name="Bob")
    bob_manual = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-21",
            "merchant": "Bob Taxi",
            "amount": 18.0,
            "category": None,
            "payment_method": "Cash",
        },
    )
    assert bob_manual.status_code == 201

    bob_transactions = client.get("/transactions")
    assert bob_transactions.status_code == 200
    assert [item["merchant"] for item in bob_transactions.json()["transactions"]] == [
        "Bob Taxi"
    ]
    _logout(client)

    _login(client, email="alice@example.com")
    alice_transactions = client.get("/transactions")
    assert alice_transactions.status_code == 200
    assert [item["merchant"] for item in alice_transactions.json()["transactions"]] == [
        "Alice Coffee"
    ]


@pytest.mark.anyio
async def test_duplicate_statement_detection_is_scoped_per_user(
    client: TestClient,
    api_main,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api_main,
        "extract_pdf_text",
        lambda path, password=None: "synthetic statement text",
    )
    monkeypatch.setattr(
        api_main,
        "parse_transactions",
        lambda text: [
            {
                "date": "2024-04-20",
                "merchant": "Shared Merchant",
                "amount": 19.25,
                "category": "Dining",
                "currency": "USD",
            }
        ],
    )

    pdf_bytes = b"%PDF-1.4 synthetic statement"

    alice = client.post(
        "/auth/register",
        json={
            "email": "alice@example.com",
            "password": "password123",
            "display_name": "Alice",
        },
    )
    assert alice.status_code == 201
    alice_user = alice.json()["user"]

    first_upload = await api_main.upload_pdf(
        request=_upload_request(),
        file=UploadFile(
            file=BytesIO(pdf_bytes),
            filename="statement.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        ),
        password=None,
        current_user=alice_user,
    )
    assert first_upload["original_file_name"] == "statement.pdf"

    with pytest.raises(HTTPException) as duplicate_error:
        await api_main.upload_pdf(
            request=_upload_request(),
            file=UploadFile(
                file=BytesIO(pdf_bytes),
                filename="statement.pdf",
                headers=Headers({"content-type": "application/pdf"}),
            ),
            password=None,
            current_user=alice_user,
        )

    assert duplicate_error.value.status_code == 409
    assert "already imported" in duplicate_error.value.detail

    bob = client.post(
        "/auth/register",
        json={
            "email": "bob@example.com",
            "password": "password123",
            "display_name": "Bob",
        },
    )
    assert bob.status_code == 201
    bob_user = bob.json()["user"]

    bob_upload = await api_main.upload_pdf(
        request=_upload_request(),
        file=UploadFile(
            file=BytesIO(pdf_bytes),
            filename="statement.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        ),
        password=None,
        current_user=bob_user,
    )
    assert bob_upload["original_file_name"] == "statement.pdf"

    _login(client, email="bob@example.com")
    bob_uploads = client.get("/uploads/recent?limit=5")
    assert bob_uploads.status_code == 200
    assert len(bob_uploads.json()["uploads"]) == 1
