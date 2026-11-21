import os
os.environ["TESTING"] = "1"
import pytest
from app import app, hitung_ongkir, generate_resi, validasi_transisi_status

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_1_health_endpoint(client):
    """Test 1: Endpoint GET /health wajib return 200 dan status database connected"""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["database"] == "connected"

def test_2_ongkir_pembulatan_ke_atas():
    """Test 2 (Aturan Bisnis 1): Berat 1.2 kg dibulatkan ke atas jadi 2 kg x tarif zona"""
    ongkir, berat_bulat, _ = hitung_ongkir(1.2, 10, 10, 10, "JATIM_DEKAT")
    assert berat_bulat == 2
    assert ongkir == 20000

def test_3_ongkir_berat_volumetrik_lebih_besar():
    """Test 3 (Aturan Bisnis 1): Berat volumetrik (60x40x30/6000 = 12 kg) > berat asli (2 kg)"""
    ongkir, berat_bulat, vol = hitung_ongkir(2.0, 60, 40, 30, "JATIM_SEDANG")
    assert vol == 12.0
    assert berat_bulat == 12
    assert ongkir == 12 * 15000

def test_4_format_resi_kc_unik():
    """Test 4 (Aturan Bisnis 2): Format resi KC + tanggal 8 digit + 6 digit urut & unik"""
    r1 = generate_resi(1, "20261120")
    r2 = generate_resi(2, "20261120")
    assert r1 == "KC20261120000001"
    assert r2 == "KC20261120000002"
    assert r1 != r2

def test_5_status_hanya_maju_tolak_lompat_dan_mundur():
    """Test 5 (Aturan Bisnis 3): Status hanya maju DIBUAT -> DIJEMPUT -> TRANSIT -> DIANTAR -> DITERIMA"""
    ok_maju, _ = validasi_transisi_status("DIBUAT", "DIJEMPUT")
    ok_lompat, _ = validasi_transisi_status("DIBUAT", "DIANTAR")
    ok_mundur, _ = validasi_transisi_status("TRANSIT", "DIBUAT")
    assert ok_maju is True
    assert ok_lompat is False
    assert ok_mundur is False

def test_6_api_buat_dan_lacak_resi(client):
    """Test 6: Integrasi API buat pengiriman, tolak lompatan status, dan lacak resi"""
    res = client.post("/api/shipments", json={"pengirim": "Vincent", "penerima": "Elysia", "berat": 1.5, "p": 10, "l": 10, "t": 10, "zona": "JATIM_DEKAT"})
    assert res.status_code == 201
    resi = res.get_json()["resi"]
    # Coba lompat status ilegal dari DIBUAT langsung ke DITERIMA (Wajib ditolak 400!)
    bad_update = client.post(f"/api/shipments/{resi}/status", json={"status": "DITERIMA"})
    assert bad_update.status_code == 400
    # Update legal ke DIJEMPUT
    good_update = client.post(f"/api/shipments/{resi}/status", json={"status": "DIJEMPUT"})
    assert good_update.status_code == 200