from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
import csv
import hmac
import html
import io
import json
import os
import secrets
import socket
import sqlite3
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_HTML = ROOT / "app.html"
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "quiz_records.sqlite3"))
PORT = int(os.environ.get("PORT", "8765"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "teacher123")
AUTH_TOKENS = set()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            cls TEXT,
            seat INTEGER,
            score INTEGER,
            correct INTEGER,
            total_q INTEGER,
            answers TEXT,
            wrong_subs TEXT,
            sub_scores TEXT,
            events TEXT,
            local_time TEXT,
            started_at TEXT,
            submitted_at TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            cls TEXT,
            seat TEXT,
            event_type TEXT,
            payload TEXT,
            created_at TEXT
        )"""
    )
    conn.commit()
    return conn


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def page(body, title="翰林版六年級 分數除法 診斷測驗"):
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
body{{margin:0;background:#fff5f0;color:#1a2130;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC','Noto Sans TC',sans-serif;line-height:1.65}}
.wrap{{width:min(920px,calc(100% - 28px));margin:0 auto;padding:28px 0 44px}}
.card{{background:#fff;border:1px solid #ead7cf;border-radius:14px;padding:20px;box-shadow:0 3px 16px rgba(0,0,0,.08);margin:14px 0}}
h1{{margin:0 0 8px;color:#8b1a06}}a,.btn{{display:inline-flex;align-items:center;justify-content:center;background:#c8401a;color:#fff;text-decoration:none;border-radius:10px;padding:10px 14px;font-weight:800}}
.muted{{color:#4e5a6a}}code{{background:#f4f6f9;padding:2px 5px;border-radius:5px}}
img{{max-width:100%}}
</style></head><body>{body}</body></html>""".encode("utf-8")


def app_html(base_url, start_teacher=False):
    content = APP_HTML.read_text("utf-8")
    quiz_url = f"{base_url}/quiz"
    content = content.replace("window.location.origin + window.location.pathname", json.dumps(quiz_url, ensure_ascii=False))
    if start_teacher:
        content = content.replace("go(location.hash==='#teacher'?'slogin':'sw');", "go('slogin');")
    return content.encode("utf-8")


def home_html(base_url):
    quiz_url = f"{base_url}/quiz"
    return page(
        f"""<main class="wrap">
  <section class="card">
    <h1>翰林版六年級 分數除法 診斷測驗</h1>
    <p class="muted">學生掃描 QR code 線上作答，作答過程與結果會存入雲端資料庫供老師查閱。</p>
    <p><img src="/qr.svg?data={quote(quiz_url)}" alt="學生作答 QR code" width="220" height="220"></p>
    <p><a class="btn" href="/quiz">開啟學生作答頁</a> <a class="btn" href="/teacher">老師後台</a></p>
    <p class="muted">學生網址：<br><code>{html.escape(quiz_url)}</code></p>
    <p class="muted">預設教師密碼是 <code>teacher123</code>。公開部署時請設定環境變數 <code>ADMIN_PASSWORD</code>。</p>
  </section>
</main>"""
    )


def is_teacher_authorized(headers):
    cookie = headers.get("Cookie", "")
    parts = [p.strip() for p in cookie.split(";")]
    token = ""
    for part in parts:
        if part.startswith("teacher_token="):
            token = part.split("=", 1)[1]
            break
    return token in AUTH_TOKENS


def qr_svg(data):
    matrix = make_qr_matrix(data[:70])
    scale = 8
    quiet = 4
    size = (len(matrix) + quiet * 2) * scale
    rects = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                rects.append(f'<rect x="{(x+quiet)*scale}" y="{(y+quiet)*scale}" width="{scale}" height="{scale}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}"><rect width="100%" height="100%" fill="#fff"/><g fill="#c8401a">{"".join(rects)}</g></svg>'.encode()


def make_qr_matrix(text):
    version, ecc_len, data_len = 4, 20, 80
    size = 17 + 4 * version
    modules = [[None] * size for _ in range(size)]

    def set_mod(x, y, dark):
        if 0 <= x < size and 0 <= y < size:
            modules[y][x] = dark

    def finder(x, y):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx, yy = x + dx, y + dy
                if 0 <= xx < size and 0 <= yy < size:
                    dark = 0 <= dx <= 6 and 0 <= dy <= 6 and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                    set_mod(xx, yy, dark)

    finder(0, 0)
    finder(size - 7, 0)
    finder(0, size - 7)
    for i in range(8, size - 8):
        set_mod(i, 6, i % 2 == 0)
        set_mod(6, i, i % 2 == 0)
    set_mod(8, size - 8, True)

    bits = [0, 1, 0, 0]
    data = text.encode("utf-8")
    bits += [(len(data) >> i) & 1 for i in range(7, -1, -1)]
    for b in data:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, data_len * 8 - len(bits))
    while len(bits) % 8:
        bits.append(0)
    codewords = [sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)]
    pads = [0xEC, 0x11]
    k = 0
    while len(codewords) < data_len:
        codewords.append(pads[k % 2])
        k += 1
    codewords += rs_ecc(codewords, ecc_len)
    all_bits = [(b >> i) & 1 for b in codewords for i in range(7, -1, -1)]

    i = 0
    upward = True
    x = size - 1
    while x > 0:
        if x == 6:
            x -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for y in rows:
            for dx in (0, 1):
                xx = x - dx
                if modules[y][xx] is None:
                    bit = all_bits[i] if i < len(all_bits) else 0
                    modules[y][xx] = bool(bit) ^ ((xx + y) % 2 == 0)
                    i += 1
        upward = not upward
        x -= 2
    draw_format(modules, 1, 0)
    return [[bool(v) for v in row] for row in modules]


def rs_ecc(data, degree):
    gen = [1]
    for i in range(degree):
        gen = poly_mul(gen, [1, gf_pow(2, i)])
    res = [0] * degree
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for j in range(degree):
            res[j] ^= gf_mul(gen[j + 1], factor)
    return res


def poly_mul(p, q):
    out = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] ^= gf_mul(a, b)
    return out


def gf_mul(x, y):
    z = 0
    for i in range(8):
        if (y >> i) & 1:
            z ^= x << i
    for i in range(14, 7, -1):
        if (z >> i) & 1:
            z ^= 0x11D << (i - 8)
    return z


def gf_pow(x, n):
    y = 1
    for _ in range(n):
        y = gf_mul(y, x)
    return y


def draw_format(m, ecc, mask):
    size = len(m)
    data = (ecc << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = ((data << 10) | rem) ^ 0x5412
    coords = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    coords += [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8), (size - 5, 8), (size - 6, 8), (size - 7, 8), (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5), (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for i, (x, y) in enumerate(coords):
        m[y][x] = bool((bits >> (i % 15)) & 1)


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, body, content_type="text/html; charset=utf-8", status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def base_url(self):
        if PUBLIC_BASE_URL:
            return PUBLIC_BASE_URL
        scheme = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("Host", f"{local_ip()}:{PORT}")
        return f"{scheme}://{host}".rstrip("/")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(home_html(self.base_url()))
        elif parsed.path == "/quiz":
            self.send_bytes(app_html(self.base_url()))
        elif parsed.path == "/teacher":
            self.send_bytes(app_html(self.base_url(), start_teacher=True))
        elif parsed.path == "/qr.svg":
            data = unquote(parse_qs(parsed.query).get("data", [""])[0])
            self.send_bytes(qr_svg(data), "image/svg+xml")
        elif parsed.path == "/healthz":
            self.send_bytes(b'{"ok":true}', "application/json")
        elif parsed.path == "/api/submissions":
            if not is_teacher_authorized(self.headers):
                self.send_bytes(b'{"error":"unauthorized"}', "application/json", 401)
                return
            q = parse_qs(parsed.query)
            cls = q.get("cls", [""])[0]
            conn = db()
            if cls:
                rows = conn.execute("SELECT * FROM submissions WHERE cls=? ORDER BY created_at DESC", (cls,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
            out = []
            for r in rows:
                out.append({
                    "id": r["id"],
                    "sessionId": r["session_id"],
                    "cls": r["cls"],
                    "seat": r["seat"],
                    "score": r["score"],
                    "correct": r["correct"],
                    "totalQ": r["total_q"],
                    "localTime": r["local_time"],
                    "startedAt": r["started_at"],
                    "submittedAt": r["submitted_at"],
                    "answers": json.loads(r["answers"] or "[]"),
                    "wrongSubs": json.loads(r["wrong_subs"] or "[]"),
                    "subScores": json.loads(r["sub_scores"] or "{}"),
                    "events": json.loads(r["events"] or "[]"),
                })
            self.send_bytes(json.dumps(out, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        elif parsed.path == "/api/export.csv":
            if not is_teacher_authorized(self.headers):
                self.send_bytes(b"unauthorized", "text/plain", 401)
                return
            conn = db()
            rows = conn.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["created_at", "cls", "seat", "score", "correct", "total_q", "sub_scores", "answers", "events"])
            for r in rows:
                writer.writerow([r["created_at"], r["cls"], r["seat"], r["score"], r["correct"], r["total_q"], r["sub_scores"], r["answers"], r["events"]])
            self.send_bytes(out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        data = self.read_json()
        conn = db()
        if self.path == "/api/teacher-login":
            if hmac.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD):
                token = secrets.token_urlsafe(32)
                AUTH_TOKENS.add(token)
                self.send_bytes(
                    b'{"ok":true}',
                    "application/json",
                    headers={"Set-Cookie": f"teacher_token={token}; HttpOnly; SameSite=Lax; Path=/"},
                )
            else:
                self.send_bytes(b'{"ok":false}', "application/json", 401)
        elif self.path == "/api/progress":
            student = data.get("student") or {}
            event = data.get("event") or {}
            conn.execute(
                "INSERT INTO progress(session_id,cls,seat,event_type,payload,created_at) VALUES(?,?,?,?,?,?)",
                (data.get("sessionId"), student.get("cls"), student.get("seat"), event.get("type"), json.dumps(event, ensure_ascii=False), now_text()),
            )
            conn.commit()
            self.send_bytes(b'{"ok":true}', "application/json")
        elif self.path == "/api/submit":
            sid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO submissions(
                    id,session_id,cls,seat,score,correct,total_q,answers,wrong_subs,sub_scores,events,
                    local_time,started_at,submitted_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid,
                    data.get("sessionId"),
                    data.get("cls"),
                    data.get("seat"),
                    data.get("score"),
                    data.get("correct"),
                    data.get("totalQ"),
                    json.dumps(data.get("answers", []), ensure_ascii=False),
                    json.dumps(data.get("wrongSubs", []), ensure_ascii=False),
                    json.dumps(data.get("subScores", {}), ensure_ascii=False),
                    json.dumps(data.get("events", []), ensure_ascii=False),
                    data.get("localTime"),
                    data.get("startedAt"),
                    data.get("submittedAt"),
                    now_text(),
                ),
            )
            conn.commit()
            self.send_bytes(json.dumps({"ok": True, "id": sid}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        else:
            self.send_error(404)


if __name__ == "__main__":
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db().close()
    host = local_ip()
    computer_name = socket.gethostname().removesuffix(".local")
    print(f"學生 QR 首頁：http://{host}:{PORT}/")
    print(f"本機名稱網址：http://{computer_name}.local:{PORT}/")
    print(f"學生作答頁：http://{host}:{PORT}/quiz")
    print(f"教師密碼：{'已由 ADMIN_PASSWORD 設定' if os.environ.get('ADMIN_PASSWORD') else 'teacher123（公開部署請修改）'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
