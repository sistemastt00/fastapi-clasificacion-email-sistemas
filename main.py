"""
main.py — Email Sistemas Bot
FastAPI + Gmail API + Airtable

Arquitectura:
  · Poller en background: lee emails nuevos de sistemas@tutrastero.com cada 60 s
  · Webhook POST /webhook: disparo manual del pipeline
  · Monitor  GET  /monitor: panel de logs en tiempo real
  · Deploy   POST /deploy:  git pull + reinicio (via SIGTERM)
"""
import asyncio
import base64
import collections
import datetime
import json
import logging
import os
import signal
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Cookie, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import bcrypt as _bcrypt, secrets as _secrets, time as _time, base64 as _base64
from typing import Optional
from pathlib import Path as _Path

# ─── Auth compartida con Monitor Global ──────────────────────
_AUTH_USERS_FILE = _Path("/opt/fastapi-monitor-global/users.json")
_AUTH_SESSIONS: dict = {}
_AUTH_ATTEMPTS: dict = {}
_AUTH_MAX, _AUTH_WIN = 5, 600
_AUTH_LOGO_URL = ""
try:
    _lp = _Path("/opt/fastapi-monitor-global/logo.png")
    if _lp.exists():
        _AUTH_LOGO_URL = "data:image/png;base64," + _base64.b64encode(_lp.read_bytes()).decode()
except Exception:
    pass
_AUTH_MONITOR_NAME = "Email Sistemas"

def _auth_load() -> dict:
    if _AUTH_USERS_FILE.exists():
        try: return json.loads(_AUTH_USERS_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def _auth_ok(session) -> bool:
    return bool(session and session in _AUTH_SESSIONS)

def _auth_page(err: str = "") -> str:
    if err == "2":
        err_html = '<div class="error">🔒 Demasiados intentos. Espera 10 minutos.</div>'
    elif err:
        err_html = '<div class="error">⚠ Usuario o contraseña incorrectos</div>'
    else:
        err_html = ""
    logo_html = f'<img src="{_AUTH_LOGO_URL}" alt="Tu Trastero">' if _AUTH_LOGO_URL else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_AUTH_MONITOR_NAME} — Acceso</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#F1F5F9;--su:#fff;--t1:#0F172A;--t2:#475569;--t3:#64748B;--bo:#E2E8F0;--grn:#059669;--rs:8px}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0F172A;--su:#1E293B;--t1:#F1F5F9;--t2:#94A3B8;--t3:#64748B;--bo:#334155}}}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{background:var(--su);border-radius:12px;padding:44px 40px;width:380px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 20px rgba(0,0,0,.06);border:1px solid var(--bo)}}
.logo-area{{text-align:center;margin-bottom:32px}}
.logo-area img{{height:42px;max-width:100%;object-fit:contain;margin-bottom:12px;display:block;margin-left:auto;margin-right:auto}}
h2{{color:var(--t1);font-size:.95em;text-align:center;letter-spacing:1px;text-transform:uppercase;font-weight:700}}
.sub{{color:var(--t3);font-size:.75em;text-align:center;margin-top:4px}}
.error{{background:#FEF2F2;border:1px solid rgba(220,38,38,.25);color:#DC2626;padding:9px 14px;border-radius:var(--rs);font-size:.82em;margin:18px 0 0;text-align:center}}
.field{{margin-top:18px}}
label{{display:block;color:var(--t2);font-size:.75em;letter-spacing:.5px;text-transform:uppercase;margin-bottom:5px;font-weight:600}}
input{{width:100%;background:var(--su);border:1px solid var(--bo);color:var(--t1);padding:10px 14px;border-radius:var(--rs);font-family:inherit;font-size:.95em;outline:none;transition:border-color .15s,box-shadow .15s}}
input:focus{{border-color:var(--grn);box-shadow:0 0 0 3px rgba(5,150,105,.12)}}
.btn{{width:100%;background:var(--t1);border:none;color:#fff;padding:12px;border-radius:var(--rs);font-family:inherit;font-size:.9em;font-weight:600;cursor:pointer;letter-spacing:.05em;text-transform:uppercase;margin-top:24px;transition:opacity .15s}}
.btn:hover{{opacity:.85}}
</style>
</head>
<body>
<div class="box">
  <div class="logo-area">
    {logo_html}
    <h2>{_AUTH_MONITOR_NAME}</h2>
    <div class="sub">FastAPI · tutrastero.com</div>
  </div>
  {err_html}
  <form method="post" action="/login">
    <div class="field"><label>Usuario</label>
      <input type="email" name="username" placeholder="usuario@dominio.com" autofocus required></div>
    <div class="field"><label>Contraseña</label>
      <input type="password" name="password" placeholder="········" required></div>
    <button type="submit" class="btn">Acceder</button>
  </form>
</div>
</body></html>"""

import config
from services import gmail as gmail_svc
from handlers import clasificacion
from handlers.clasificacion import summaries as _summaries

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("email-sistemas")

MAX_HISTORY = 100
_history: collections.deque = collections.deque(maxlen=MAX_HISTORY)


class _MonitorHandler(logging.Handler):
    def emit(self, record):
        _history.append({
            "time":    datetime.datetime.fromtimestamp(record.created).strftime("%d/%m %H:%M:%S"),
            "level":   record.levelname,
            "message": self.format(record),
        })


_mh = _MonitorHandler()
_mh.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_mh)

# ─── Background tasks ─────────────────────────────────────────────────────────

async def _poller():
    interval = config.POLL_INTERVAL_BACKUP if config.PUBSUB_TOPIC else config.POLL_INTERVAL
    mode     = "respaldo (Pub/Sub activo)" if config.PUBSUB_TOPIC else "principal"
    logger.info(f"📧 Poller {mode} iniciado — intervalo {interval}s")
    while True:
        try:
            await clasificacion.process_new_emails()
        except Exception as exc:
            logger.error(f"Poller error: {exc}", exc_info=True)
        await asyncio.sleep(interval)


async def _watch_renewer():
    while True:
        await asyncio.sleep(6 * 24 * 3600)
        try:
            result = await gmail_svc.setup_watch(config.PUBSUB_TOPIC)
            logger.info(f"📧 Gmail watch renovado | expiration={result.get('expiration')}")
        except Exception as exc:
            logger.error(f"Error renovando Gmail watch: {exc}", exc_info=True)

# ─── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_poller())]

    if config.PUBSUB_TOPIC:
        try:
            result = await gmail_svc.setup_watch(config.PUBSUB_TOPIC)
            logger.info(f"📧 Gmail watch configurado | expiration={result.get('expiration')}")
            tasks.append(asyncio.create_task(_watch_renewer()))
        except Exception as exc:
            logger.warning(f"Gmail watch no configurado — usando solo poller: {exc}")

    yield

    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Email Sistemas Bot", lifespan=lifespan)

# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "status": "ok",
        "bot":    "email-sistemas",
        "cuenta": config.GMAIL_ACCOUNT,
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Dispara manualmente el pipeline de clasificación."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "JSON inválido"})

    logger.info(f"Webhook recibido: {json.dumps(payload, ensure_ascii=False)[:200]}")

    try:
        await clasificacion.process_new_emails()
        return {"status": "ok"}
    except Exception as exc:
        logger.error(f"Error en webhook: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/pubsub")
async def pubsub_push(request: Request):
    """Recibe notificaciones push de Gmail vía Google Cloud Pub/Sub."""
    if config.PUBSUB_TOKEN:
        token = request.query_params.get("token", "")
        if token != config.PUBSUB_TOKEN:
            raise HTTPException(status_code=403, detail="Token inválido")

    try:
        body   = await request.json()
        data_b64 = body.get("message", {}).get("data", "")
        if data_b64:
            info = json.loads(base64.b64decode(data_b64 + "==").decode())
            logger.info(f"📬 Pub/Sub | {info.get('emailAddress')} | historyId={info.get('historyId')}")
    except Exception as exc:
        logger.warning(f"Pub/Sub parse warning: {exc}")

    asyncio.create_task(clasificacion.process_new_emails())
    return {"status": "ok"}


@app.post("/reprocess")
async def reprocess(request: Request, session: Optional[str] = Cookie(default=None)):
    """Reprocesa todos los emails actuales del INBOX (recupera los no clasificados)."""
    token = request.query_params.get("token", "")
    if not _auth_ok(session) and not (config.DEPLOY_TOKEN and token == config.DEPLOY_TOKEN):
        raise HTTPException(status_code=403, detail="No autorizado")
    messages = await gmail_svc.list_inbox(max_results=100)
    logger.info(f"[reprocess] {len(messages)} email(s) en INBOX encontrados")
    ok, err = 0, 0
    for msg in messages:
        try:
            await clasificacion._process_email(msg)
            ok += 1
        except Exception as exc:
            logger.error(f"[reprocess] Error {msg.get('id')}: {exc}")
            err += 1
    logger.info(f"[reprocess] Completado — ok={ok} err={err}")
    return {"status": "ok", "procesados": ok, "errores": err}


@app.post("/deploy")
async def deploy(request: Request, background_tasks: BackgroundTasks):
    token = request.query_params.get("token", "")
    if not config.DEPLOY_TOKEN or token != config.DEPLOY_TOKEN:
        raise HTTPException(status_code=403, detail="Token inválido")

    if not config.DEPLOY_DIR:
        raise HTTPException(status_code=500, detail="DEPLOY_DIR no configurado")

    result = subprocess.run(
        ["git", "-C", config.DEPLOY_DIR, "pull"],
        capture_output=True, text=True, timeout=30,
    )
    output = (result.stdout + result.stderr).strip()
    logger.info(f"[deploy] git pull → {output}")

    background_tasks.add_task(_restart_after_delay)
    return {"status": "ok", "git": output}


async def _restart_after_delay():
    await asyncio.sleep(1)
    logger.info("[deploy] Reiniciando proceso…")
    os.kill(os.getpid(), signal.SIGTERM)


@app.get("/login", response_class=HTMLResponse)
def _login_page(error: str = ""):
    return _auth_page(error)


@app.post("/login")
async def _login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    now = _time.time()
    attempts = [t for t in _AUTH_ATTEMPTS.get(ip, []) if now - t < _AUTH_WIN]
    if len(attempts) >= _AUTH_MAX:
        _AUTH_ATTEMPTS[ip] = attempts
        return RedirectResponse("/login?error=2", status_code=302)
    users = _auth_load()
    h = users.get(username)
    ok = False
    if h:
        try: ok = _bcrypt.checkpw(password.encode(), h.encode())
        except Exception: pass
    if ok:
        _AUTH_ATTEMPTS.pop(ip, None)
        token = _secrets.token_hex(32)
        _AUTH_SESSIONS[token] = username
        resp = RedirectResponse("/monitor", status_code=302)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=86400)
        return resp
    attempts.append(now)
    _AUTH_ATTEMPTS[ip] = attempts
    return RedirectResponse("/login?error=1" if len(attempts) < _AUTH_MAX else "/login?error=2", status_code=302)


@app.get("/logout")
def _logout(session: Optional[str] = Cookie(default=None)):
    _AUTH_SESSIONS.pop(session or "", None)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


@app.get("/monitor", response_class=HTMLResponse)
async def monitor(session: Optional[str] = Cookie(default=None)):
    if not _auth_ok(session):
        return RedirectResponse("/login", status_code=302)
    return _render_monitor()


# ─── Monitor HTML ─────────────────────────────────────────────────────────────

def _render_monitor():
    # ── Log view ─────────────────────────────────────────────────────────────
    filas = ""
    for entry in reversed(list(_history)):
        bg    = {"ERROR": "#2d0a0a", "WARNING": "#2d1f00"}.get(entry["level"], "#1a1a2e")
        color = {"ERROR": "#e74c3c", "WARNING": "#f39c12", "INFO": "#3498db"}.get(entry["level"], "#aaa")
        msg   = entry["message"].replace("<", "&lt;").replace(">", "&gt;")
        filas += (
            f'<tr style="background:{bg}">'
            f'<td class="ts">{entry["time"]}</td>'
            f'<td class="lv" style="color:{color}">{entry["level"]}</td>'
            f'<td class="ms">{msg}</td>'
            f'</tr>'
        )
    if not filas:
        filas = '<tr><td colspan="3" style="text-align:center;color:#555;padding:30px">Sin eventos aún…</td></tr>'

    # ── Summary view ──────────────────────────────────────────────────────────
    bloques = ""
    for i, s in enumerate(list(_summaries)):
        n_lbl     = s.get("n_labels", 0)
        lbl_color = "#2ecc71" if n_lbl > 0 else "#555"
        from_display = s["from_name"] or s["from_email"]
        subj = s["subject"].replace("<", "&lt;")[:60]
        log_rows = ""
        for entry in s.get("logs", []):
            lc  = {"ERROR": "#e74c3c", "WARNING": "#f39c12", "INFO": "#3498db"}.get(entry["level"], "#aaa")
            msg = entry["message"].replace("<", "&lt;").replace(">", "&gt;")
            log_rows += (
                f'<tr>'
                f'<td style="color:#888;white-space:nowrap;padding:3px 10px;font-size:.75em">{entry["time"]}</td>'
                f'<td style="color:{lc};padding:3px 6px;font-size:.75em;font-weight:600">{entry["level"]}</td>'
                f'<td style="padding:3px 10px;font-size:.78em;color:#ccc;word-break:break-word">{msg}</td>'
                f'</tr>'
            )
        if not log_rows:
            log_rows = '<tr><td colspan="3" style="color:#444;padding:8px 14px;font-size:.78em">Sin logs capturados</td></tr>'

        bloques += f"""
        <tr class="sm-head" onclick="toggle({i})" title="Clic para expandir">
          <td class="ts">{s["time"]}</td>
          <td class="sm-arrow" id="arr-{i}">▶</td>
          <td class="sm-from-h">{from_display}<br><span class="sm-mail">{s["from_email"]}</span></td>
          <td class="sm-subj-h">{subj}</td>
          <td style="color:{lbl_color};white-space:nowrap">{s["etiquetas"]}</td>
        </tr>
        <tr class="sm-detail" id="det-{i}" style="display:none">
          <td colspan="5" style="padding:0;background:#0d0d1f">
            <table style="width:100%;border-collapse:collapse;background:transparent;border:none;border-radius:0;margin:0">
              {log_rows}
            </table>
          </td>
        </tr>"""

    if not bloques:
        bloques = '<tr><td colspan="5" style="text-align:center;color:#555;padding:30px">Sin emails procesados aún…</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Monitor — Email Sistemas</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#F1F5F9;--su:#fff;--bo:#E2E8F0;--boh:#CBD5E1;
      --t1:#0F172A;--t2:#475569;--t3:#94A3B8;
      --grn:#059669;--grn-bg:#ECFDF5;--grn-brd:rgba(5,150,105,.18);
      --red:#DC2626;--red-bg:#FEF2F2;--red-brd:rgba(220,38,38,.18);
      --amb:#D97706;--amb-bg:#FFFBEB;--amb-brd:rgba(217,119,6,.18);
      --rs:6px;
    }}
    @media(prefers-color-scheme:dark){{:root{{
      --bg:#0F172A;--su:#1E293B;--bo:#334155;--boh:#475569;
      --t1:#F1F5F9;--t2:#94A3B8;--t3:#475569;
      --grn-bg:#022c22;--red-bg:#1e0606;--amb-bg:#1c1107;
      --grn-brd:rgba(5,150,105,.3);--red-brd:rgba(220,38,38,.3);--amb-brd:rgba(217,119,6,.3);
    }}}}
    :root[data-theme=dark]{{--bg:#0F172A;--su:#1E293B;--bo:#334155;--boh:#475569;--t1:#F1F5F9;--t2:#94A3B8;--t3:#475569;--grn-bg:#022c22;--red-bg:#1e0606;--amb-bg:#1c1107;--grn-brd:rgba(5,150,105,.3);--red-brd:rgba(220,38,38,.3);--amb-brd:rgba(217,119,6,.3)}}
    :root[data-theme=light]{{--bg:#F1F5F9;--su:#fff;--bo:#E2E8F0;--boh:#CBD5E1;--t1:#0F172A;--t2:#475569;--t3:#94A3B8;--grn-bg:#ECFDF5;--red-bg:#FEF2F2;--amb-bg:#FFFBEB;--grn-brd:rgba(5,150,105,.18);--red-brd:rgba(220,38,38,.18);--amb-brd:rgba(217,119,6,.18)}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.5;color:var(--t1);background:var(--bg);font-variant-numeric:tabular-nums}}
    /* Nav */
    .nav{{position:sticky;top:0;z-index:50;background:var(--su);border-bottom:1px solid var(--bo);display:flex;align-items:center;gap:10px;padding:0 18px;height:48px}}
    .nav-title{{font-size:14px;font-weight:700;color:var(--t1);display:flex;align-items:center;gap:8px;white-space:nowrap}}
    .live{{display:inline-flex;align-items:center;gap:4px;background:var(--grn-bg);color:var(--grn);border:1px solid var(--grn-brd);border-radius:20px;padding:2px 9px;font-size:11px;font-weight:700;letter-spacing:.04em}}
    .live::before{{content:'';width:6px;height:6px;border-radius:50%;background:currentColor;flex-shrink:0}}
    .nav-actions{{margin-left:auto;display:flex;gap:6px;align-items:center;flex-shrink:0}}
    .nb{{padding:5px 11px;border-radius:var(--rs);font-size:12px;font-weight:500;color:var(--t2);border:1px solid var(--bo);background:var(--su);cursor:pointer;font-family:inherit;transition:background .15s}}
    .nb:hover{{background:var(--bg)}}
    .nb:disabled{{opacity:.35;cursor:default}}
    .nb-danger{{color:var(--red);border-color:var(--red-brd)}}
    .nb-danger:hover{{background:var(--red-bg)}}
    /* Content */
    .content{{padding:16px 18px}}
    .sub{{font-size:12px;color:var(--t3);margin:0 0 12px}}
    /* Table */
    table{{width:100%;border-collapse:collapse;background:var(--su);border:1px solid var(--bo);border-radius:8px;overflow:hidden}}
    th{{background:var(--bg);color:var(--t2);padding:8px 12px;text-align:left;font-size:.78em;letter-spacing:.5px;font-weight:600;text-transform:uppercase}}
    td{{padding:6px 12px;font-size:.88em;border-top:1px solid var(--bo);color:var(--t2);vertical-align:top}}
    /* Expandable rows - level 1 (calls/emails) */
    .sm-head{{cursor:pointer;transition:background .1s}}
    .sm-head:hover td{{background:var(--bg)}}
    .sm-arrow,.fn-arrow{{width:20px;padding:6px 4px;color:var(--t3);font-size:.85em}}
    .sm-detail{{background:var(--su)}}
    .sm-tel{{padding:6px 12px;color:var(--t1);min-width:140px}}
    .sm-label{{color:var(--t3);font-size:.85em;white-space:nowrap}}
    .sm-val{{font-size:.88em;color:var(--t2)}}
    .ts{{white-space:nowrap;width:130px;padding:6px 12px;color:var(--t1);font-weight:600}}
    /* Expandable rows - level 2 (functions) */
    .fn-head{{cursor:pointer;background:var(--bg);transition:background .1s;border-top:1px solid var(--bo)}}
    .fn-head:hover td{{background:var(--boh)}}
    .fn-ts{{white-space:nowrap;width:115px;padding:5px 12px;color:var(--t3);font-size:.85em}}
    .fn-detail td{{background:var(--bg);font-size:.85em}}
    .wh-cell{{padding:5px 10px}}
    /* Badges / chips */
    .badge{{background:var(--grn-bg);color:var(--grn);border:1px solid var(--grn-brd);padding:1px 8px;border-radius:10px;font-size:.72em;font-weight:700}}
    /* Scrollbar */
    ::-webkit-scrollbar{{width:6px;height:6px}}
    ::-webkit-scrollbar-track{{background:var(--bg)}}
    ::-webkit-scrollbar-thumb{{background:var(--bo);border-radius:3px}}
    /* Email Sistemas specific */
    .lv{{width:70px;padding:5px 8px;font-size:.78em;font-weight:600}}
    .ms{{padding:5px 10px;font-size:.82em;word-break:break-word}}
    .sm-from-h{{padding:5px 10px;min-width:140px}}
    .sm-mail{{color:var(--t3);font-size:.75em}}
    .sm-subj-h{{padding:5px 10px;max-width:280px;word-break:break-word}}
  </style>
<script>(function(){{var t=localStorage.getItem('monTheme')||'light';document.documentElement.setAttribute('data-theme',t);}})();</script>
</head>
<body>
<div class="nav">
  <span class="nav-title">📧 Email Sistemas Bot <span class="live">live</span></span>
  <div class="nav-actions">
    <button class="nb" id="btn-theme" onclick="toggleTheme()" title="Cambiar tema">☀️</button>
    <button class="nb" onclick="collapseAll()">⊟ Summary</button>
    <button class="nb nb-danger" id="btn-pausar" onclick="pauseRefresh()">⏸ Pausar</button>
    <button class="nb" id="btn-retomar" onclick="resumeRefresh()" disabled>▶ Retomar</button>
  </div>
</div>
<div class="content">
  <p class="sub">Cuenta: <code>sistemas@tutrastero.com</code> &nbsp;·&nbsp; refresco 5 s</p>

  <div id="summary-view">
    <table>
      <thead><tr><th>Fecha y hora</th><th></th><th>Remitente</th><th>Asunto</th><th>Etiquetas aplicadas</th></tr></thead>
      <tbody>{bloques}</tbody>
    </table>
  </div>
</div>
  <script>
    function collapseAll() {{
      document.querySelectorAll('.sm-detail').forEach(d => d.style.display = 'none');
      document.querySelectorAll('.sm-arrow').forEach(a => a.textContent = '▶');
    }}
    function toggle(i) {{
      const det = document.getElementById('det-' + i);
      const arr = document.getElementById('arr-' + i);
      if (det.style.display === 'none') {{
        det.style.display = 'table-row';
        arr.textContent = '▼';
      }} else {{
        det.style.display = 'none';
        arr.textContent = '▶';
      }}
    }}
    const INTERVAL = 5;
    let reloader;
    function startTimers() {{
      reloader = setInterval(softReload, INTERVAL * 1000);
    }}
    function pauseRefresh() {{
      clearInterval(reloader);
      document.getElementById('btn-pausar').disabled = true;
      document.getElementById('btn-retomar').disabled = false;
    }}
    function resumeRefresh() {{
      document.getElementById('btn-pausar').disabled = false;
      document.getElementById('btn-retomar').disabled = true;
      startTimers();
    }}
    async function softReload() {{
      try {{
        const openIds = {{}};
        document.querySelectorAll('[id]').forEach(function(el) {{
          if (el.style.display && el.style.display !== 'none') openIds[el.id] = el.style.display;
        }});
        const res = await fetch(window.location.href, {{cache:'no-store'}});
        if (!res.ok) return;
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const newBodyHTML = Array.from(doc.body.children).filter(function(el) {{ return el.tagName !== 'SCRIPT'; }}).map(function(el) {{ return el.outerHTML; }}).join('');
        document.body.innerHTML = newBodyHTML;
        setTheme(localStorage.getItem('monTheme')||'light');
        Object.keys(openIds).forEach(function(id) {{ var el = document.getElementById(id); if (el) el.style.display = openIds[id]; }});
      }} catch(e) {{ console.error('Soft reload error:', e); }}
    }}
    startTimers();
    function setTheme(val){{localStorage.setItem('monTheme',val);document.documentElement.setAttribute('data-theme',val);var b=document.getElementById('btn-theme');if(b)b.textContent=val==='dark'?'🌙':'☀️';}}
    function toggleTheme(){{var t=document.documentElement.getAttribute('data-theme')||'light';setTheme(t==='dark'?'light':'dark');}}
    (function(){{var t=localStorage.getItem('monTheme')||'light';var b=document.getElementById('btn-theme');if(b)b.textContent=t==='dark'?'🌙':'☀️';}})();
  </script>
</body>
</html>"""
    return html
