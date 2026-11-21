from flask import Flask, request, jsonify, render_template_string, redirect
import os, math, datetime, psycopg2

app = Flask(__name__)

# Urutan resmi status pengiriman K13 KirimCepat (Hanya boleh maju satu arah!)
VALID_STATUSES = ["DIBUAT", "DIJEMPUT", "TRANSIT", "DIANTAR", "DITERIMA"]
TARIF_ZONA = {"JATIM_DEKAT": 10000, "JATIM_SEDANG": 15000, "JATIM_JAUH": 22000}

# Penyimpanan in-memory khusus saat mode unit testing (pytest)
USE_MEM_DB = os.getenv("TESTING", "0") == "1"
MEM_SHIPMENTS = {}
MEM_HISTORY = []
MEM_SEQ = 0

def hitung_ongkir(berat_kg, p_cm, l_cm, t_cm, zona="JATIM_DEKAT"):
    """Aturan Bisnis 1 K13: Berat dibulatkan ke atas per kg vs Berat Volumetrik (p*l*t/6000)"""
    if berat_kg <= 0 or p_cm <= 0 or l_cm <= 0 or t_cm <= 0:
        raise ValueError("Berat dan dimensi paket harus lebih dari 0")
    if zona not in TARIF_ZONA:
        raise ValueError("Zona pengiriman tidak valid")
    volumetrik = (p_cm * l_cm * t_cm) / 6000.0
    berat_pakai = max(float(berat_kg), volumetrik)
    berat_bulat = math.ceil(berat_pakai)
    return berat_bulat * TARIF_ZONA[zona], berat_bulat, round(volumetrik, 2)

def generate_resi(seq_num, tgl_str=None):
    """Aturan Bisnis 2 K13: Format resi KC + YYYYMMDD + 6 digit urut, unik"""
    if not tgl_str:
        tgl_str = datetime.datetime.now().strftime("%Y%m%d")
    return f"KC{tgl_str}{int(seq_num):06d}"

def validasi_transisi_status(status_lama, status_baru):
    """Aturan Bisnis 3 K13: Status hanya maju (DIBUAT -> DIJEMPUT -> TRANSIT -> DIANTAR -> DITERIMA)"""
    if status_baru not in VALID_STATUSES:
        return False, "Status tidak dikenal"
    idx_lama = VALID_STATUSES.index(status_lama)
    idx_baru = VALID_STATUSES.index(status_baru)
    if idx_baru != idx_lama + 1:
        return False, f"Transisi tidak sah: {status_lama} tidak boleh langsung ke {status_baru}"
    return True, "OK"

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "database"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "kirimcepat_db"),
        user=os.getenv("DB_USER", "kirimcepat_user"),
        password=os.getenv("DB_PASSWORD", "KirimCepatSecret2026!")
    )

def init_db_and_seed():
    if USE_MEM_DB:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            id SERIAL PRIMARY KEY,
            resi VARCHAR(30) UNIQUE NOT NULL,
            pengirim VARCHAR(100) NOT NULL,
            penerima VARCHAR(100) NOT NULL,
            kota_asal VARCHAR(50) NOT NULL,
            kota_tujuan VARCHAR(50) NOT NULL,
            zona VARCHAR(30) NOT NULL,
            berat_tagih INT NOT NULL,
            ongkir INT NOT NULL,
            status VARCHAR(20) NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shipment_logs (
            id SERIAL PRIMARY KEY,
            resi VARCHAR(30) NOT NULL,
            status VARCHAR(20) NOT NULL,
            keterangan TEXT,
            waktu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("SELECT COUNT(*) FROM shipments;")
    if cur.fetchone()[0] == 0:
        # Seeder data contoh awal Jawa Timur
        contoh = [
            ("KC20261120000001", "Toko Elektronik Surabaya", "Budi Malang", "Surabaya", "Malang", "JATIM_DEKAT", 2, 20000, "TRANSIT"),
            ("KC20261120000002", "Batik Madura", "Siti Banyuwangi", "Bangkalan", "Banyuwangi", "JATIM_JAUH", 3, 66000, "DIBUAT")
        ]
        for r, pg, pn, ka, kt, zn, bt, ok, st in contoh:
            cur.execute("INSERT INTO shipments (resi,pengirim,penerima,kota_asal,kota_tujuan,zona,berat_tagih,ongkir,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);", (r,pg,pn,ka,kt,zn,bt,ok,st))
            cur.execute("INSERT INTO shipment_logs (resi,status,keterangan) VALUES (%s,'DIBUAT','Paket terdaftar di sistem KirimCepat');", (r,))
            if st == "TRANSIT":
                cur.execute("INSERT INTO shipment_logs (resi,status,keterangan) VALUES (%s,'DIJEMPUT','Kurir menjemput paket'),(%s,'TRANSIT','Paket menuju hub Malang');", (r,r))
    conn.commit()
    cur.close()
    conn.close()

@app.route("/health", methods=["GET"])
def health():
    if USE_MEM_DB:
        return jsonify({"status": "UP", "service": "KirimCepat-K13", "database": "connected"}), 200
    try:
        init_db_and_seed()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        return jsonify({"status": "UP", "service": "KirimCepat-K13", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "DOWN", "database": str(e)}), 500

@app.route("/api/shipments", methods=["POST"])
def create_shipment():
    global MEM_SEQ
    d = request.get_json() or request.form
    try:
        berat = float(d.get("berat", 1))
        p = float(d.get("p", 10))
        l = float(d.get("l", 10))
        t = float(d.get("t", 10))
        zona = d.get("zona", "JATIM_DEKAT")
        ongkir, berat_bulat, _ = hitung_ongkir(berat, p, l, t, zona)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    pengirim = d.get("pengirim", "Pengirim")
    penerima = d.get("penerima", "Penerima")
    asal = d.get("kota_asal", "Surabaya")
    tujuan = d.get("kota_tujuan", "Malang")

    if USE_MEM_DB:
        MEM_SEQ += 1
        resi = generate_resi(MEM_SEQ)
        MEM_SHIPMENTS[resi] = {"resi": resi, "pengirim": pengirim, "penerima": penerima, "kota_asal": asal, "kota_tujuan": tujuan, "zona": zona, "berat_tagih": berat_bulat, "ongkir": ongkir, "status": "DIBUAT"}
        MEM_HISTORY.append({"resi": resi, "status": "DIBUAT", "keterangan": f"Paket dibuat ({asal} -> {tujuan})"})
        return jsonify(MEM_SHIPMENTS[resi]), 201

    init_db_and_seed()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM shipments;")
    next_id = cur.fetchone()[0]
    resi = generate_resi(next_id)
    cur.execute("INSERT INTO shipments (resi,pengirim,penerima,kota_asal,kota_tujuan,zona,berat_tagih,ongkir,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DIBUAT');", (resi,pengirim,penerima,asal,tujuan,zona,berat_bulat,ongkir))
    cur.execute("INSERT INTO shipment_logs (resi,status,keterangan) VALUES (%s,'DIBUAT',%s);", (resi, f"Paket dibuat ({asal} -> {tujuan})"))
    conn.commit(); cur.close(); conn.close()
    if not request.is_json:
        return redirect(f"/?resi={resi}")
    return jsonify({"resi": resi, "berat_tagih": berat_bulat, "ongkir": ongkir, "status": "DIBUAT"}), 201

@app.route("/api/shipments/<resi>/status", methods=["POST"])
def update_status(resi):
    d = request.get_json() or request.form
    status_baru = d.get("status", "")
    ket = d.get("keterangan", f"Update status ke {status_baru}")

    if USE_MEM_DB:
        if resi not in MEM_SHIPMENTS:
            return jsonify({"error": "Resi tidak ditemukan"}), 404
        ok, pesan = validasi_transisi_status(MEM_SHIPMENTS[resi]["status"], status_baru)
        if not ok:
            return jsonify({"error": pesan}), 400
        MEM_SHIPMENTS[resi]["status"] = status_baru
        MEM_HISTORY.append({"resi": resi, "status": status_baru, "keterangan": ket})
        return jsonify(MEM_SHIPMENTS[resi]), 200

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT status FROM shipments WHERE resi = %s;", (resi,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "Resi tidak ditemukan"}), 404
    ok, pesan = validasi_transisi_status(row[0], status_baru)
    if not ok:
        cur.close(); conn.close()
        return jsonify({"error": pesan}), 400
    cur.execute("UPDATE shipments SET status = %s WHERE resi = %s;", (status_baru, resi))
    cur.execute("INSERT INTO shipment_logs (resi,status,keterangan) VALUES (%s,%s,%s);", (resi, status_baru, ket))
    conn.commit(); cur.close(); conn.close()
    if not request.is_json:
        return redirect(f"/?resi={resi}")
    return jsonify({"resi": resi, "status": status_baru}), 200

@app.route("/api/track/<resi>", methods=["GET"])
def track_resi(resi):
    if USE_MEM_DB:
        if resi not in MEM_SHIPMENTS:
            return jsonify({"error": "Resi tidak ditemukan"}), 404
        logs = [h for h in MEM_HISTORY if h["resi"] == resi]
        return jsonify({"paket": MEM_SHIPMENTS[resi], "riwayat": logs}), 200

    init_db_and_seed()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT resi,pengirim,penerima,kota_asal,kota_tujuan,zona,berat_tagih,ongkir,status FROM shipments WHERE resi = %s;", (resi,))
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        return jsonify({"error": "Resi tidak ditemukan"}), 404
    cur.execute("SELECT status, keterangan, waktu FROM shipment_logs WHERE resi = %s ORDER BY id ASC;", (resi,))
    logs = [{"status": l[0], "keterangan": l[1], "waktu": str(l[2])} for l in cur.fetchall()]
    cur.close(); conn.close()
    paket = {"resi": r[0], "pengirim": r[1], "penerima": r[2], "kota_asal": r[3], "kota_tujuan": r[4], "zona": r[5], "berat_tagih": r[6], "ongkir": r[7], "status": r[8]}
    return jsonify({"paket": paket, "riwayat": logs}), 200

@app.route("/", methods=["GET"])
def index():
    init_db_and_seed()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT resi,pengirim,penerima,kota_asal,kota_tujuan,berat_tagih,ongkir,status FROM shipments ORDER BY id DESC LIMIT 10;")
    rows = cur.fetchall()
    q_resi = request.args.get("resi", rows[0][0] if rows else "")
    logs = []
    if q_resi:
        cur.execute("SELECT status, keterangan, waktu FROM shipment_logs WHERE resi = %s ORDER BY id ASC;", (q_resi,))
        logs = cur.fetchall()
    cur.close(); conn.close()
    return render_template_string("""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>KirimCepat - K13</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="bg-light py-4"><div class="container">
    <div class="p-4 mb-4 bg-primary text-white rounded-3 shadow-sm d-flex justify-content-between align-items-center">
        <div><h2 class="fw-bold mb-0">🚚 KirimCepat (K13) — Ekspedisi Jawa Timur</h2><small>Kelompok 9 • CI/CD Multibranch Pipeline Jenkins & Docker Compose</small></div>
        <a href="/health" class="btn btn-light btn-sm fw-bold">Cek /health</a>
    </div>
    <div class="row g-4">
        <div class="col-md-5"><div class="card shadow-sm p-3"><h5>Buat Pengiriman Baru</h5>
        <form action="/api/shipments" method="POST">
            <div class="row g-2 mb-2"><div class="col"><input name="pengirim" class="form-control form-control-sm" placeholder="Pengirim" required></div><div class="col"><input name="penerima" class="form-control form-control-sm" placeholder="Penerima" required></div></div>
            <div class="row g-2 mb-2"><div class="col"><input name="kota_asal" class="form-control form-control-sm" placeholder="Kota Asal (Surabaya)" required></div><div class="col"><input name="kota_tujuan" class="form-control form-control-sm" placeholder="Kota Tujuan (Malang)" required></div></div>
            <div class="row g-2 mb-2"><div class="col"><input name="berat" type="number" step="0.1" class="form-control form-control-sm" placeholder="Berat (kg)" required></div>
            <div class="col"><select name="zona" class="form-select form-select-sm"><option value="JATIM_DEKAT">Jatim Dekat (10rb/kg)</option><option value="JATIM_SEDANG">Jatim Sedang (15rb/kg)</option><option value="JATIM_JAUH">Jatim Jauh (22rb/kg)</option></select></div></div>
            <div class="row g-2 mb-3"><div class="col"><input name="p" type="number" class="form-control form-control-sm" placeholder="P (cm)" value="10"></div><div class="col"><input name="l" type="number" class="form-control form-control-sm" placeholder="L (cm)" value="10"></div><div class="col"><input name="t" type="number" class="form-control form-control-sm" placeholder="T (cm)" value="10"></div></div>
            <button class="btn btn-primary btn-sm w-100">Terbitkan Resi & Hitung Ongkir</button>
        </form></div></div>
        <div class="col-md-7"><div class="card shadow-sm p-3 mb-3"><h5>Lacak Resi Publik: <span class="text-primary">{{q_resi}}</span></h5>
        <ul class="list-group list-group-flush mb-2">{% for l in logs %}<li class="list-group-item small"><b>{{l[0]}}</b> — {{l[1]}} <span class="text-muted float-end">{{l[2]}}</span></li>{% endfor %}</ul></div>
        <div class="card shadow-sm p-3"><h5>Daftar Paket & Update Status Kurir</h5>
        <table class="table table-sm small align-middle"><thead><tr><th>Resi</th><th>Rute</th><th>Berat/Ongkir</th><th>Status</th><th>Aksi Kurir</th></tr></thead><tbody>
        {% for r in rows %}<tr><td><a href="/?resi={{r[0]}}">{{r[0]}}</a></td><td>{{r[3]}} → {{r[4]}}</td><td>{{r[5]}}kg / Rp{{r[6]}}</td><td><span class="badge bg-info text-dark">{{r[7]}}</span></td>
        <td><form action="/api/shipments/{{r[0]}}/status" method="POST" class="d-flex gap-1">
        <select name="status" class="form-select form-select-sm"><option>DIJEMPUT</option><option>TRANSIT</option><option>DIANTAR</option><option>DITERIMA</option></select>
        <button class="btn btn-success btn-sm">Update</button></form></td></tr>{% endfor %}
        </tbody></table></div></div>
    </div></div></body></html>""", rows=rows, q_resi=q_resi, logs=logs)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)