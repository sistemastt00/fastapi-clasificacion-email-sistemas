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

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse

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


@app.get("/monitor", response_class=HTMLResponse)
async def monitor():
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
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:monospace; background:#0f0f1a; padding:24px; color:#eee; }}
  .topbar {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:6px; flex-wrap:wrap; gap:10px; }}
  h1 {{ color:#e94560; font-size:1.3em; }}
  .badge {{ background:#2ecc71; color:#fff; padding:2px 10px; border-radius:10px; font-size:.72em; margin-left:8px; vertical-align:middle; }}
  .toolbar {{ display:flex; gap:8px; align-items:center; }}
  .btn {{ background:#16213e; border:1px solid #2a2a4a; color:#ccc; padding:5px 14px; border-radius:6px; cursor:pointer; font-family:monospace; font-size:12px; transition:background .15s; text-decoration:none; display:inline-block; }}
  .btn:hover {{ background:#1f2f50; color:#fff; }}
  .btn-pause  {{ border-color:#e74c3c; color:#e74c3c; }}
  .btn-resume {{ border-color:#2ecc71; color:#2ecc71; }}
  #ticker {{ color:#555; font-size:12px; min-width:50px; text-align:right; }}
  .sub {{ color:#555; font-size:12px; margin:0 0 16px; }}
  table {{ width:100%; border-collapse:collapse; background:#1a1a2e; border:1px solid #2a2a4a; border-radius:8px; overflow:hidden; }}
  th {{ background:#16213e; color:#aaa; padding:8px 12px; text-align:left; font-size:.8em; letter-spacing:.5px; }}
  .ts {{ white-space:nowrap; width:115px; padding:5px 10px; color:#bbb; font-size:.78em; }}
  .lv {{ width:70px; padding:5px 8px; font-size:.78em; font-weight:600; }}
  .ms {{ padding:5px 10px; font-size:.82em; word-break:break-word; color:#ddd; }}
  td {{ padding:5px 10px; font-size:.82em; vertical-align:top; }}
  .sm-head {{ cursor:pointer; transition:background .1s; }}
  .sm-head:hover td {{ background:#1f2540; }}
  .sm-arrow {{ width:20px; padding:5px 4px; color:#555; font-size:.7em; }}
  .sm-from-h {{ padding:5px 10px; font-size:.82em; min-width:140px; }}
  .sm-mail {{ color:#555; font-size:.75em; }}
  .sm-subj-h {{ padding:5px 10px; font-size:.82em; color:#ddd; max-width:280px; word-break:break-word; }}
</style>
</head>
<body>
  <div class="topbar">
    <h1>📧 Email Sistemas Bot <span class="badge">live</span></h1>
    <div class="toolbar">
      <button class="btn" onclick="collapseAll()">⊟ Summary</button>
      <button class="btn btn-pause"  id="btn-pausar"  onclick="pauseRefresh()">⏸ Pausar</button>
      <button class="btn btn-resume" id="btn-retomar" onclick="resumeRefresh()" disabled>▶ Retomar</button>
      <span id="ticker">5 s</span>
    </div>
  </div>
  <p class="sub">Cuenta: <code>sistemas@tutrastero.com</code> &nbsp;·&nbsp; refresco 5 s</p>

  <div id="summary-view">
    <table>
      <thead><tr><th>Fecha y hora</th><th></th><th>Remitente</th><th>Asunto</th><th>Etiquetas aplicadas</th></tr></thead>
      <tbody>{bloques}</tbody>
    </table>
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
    let remaining = INTERVAL, countdown, reloader;
    function startTimers() {{
      remaining = INTERVAL;
      countdown = setInterval(() => {{
        remaining--;
        document.getElementById('ticker').textContent = remaining + ' s';
        if (remaining <= 0) remaining = INTERVAL;
      }}, 1000);
      reloader = setInterval(() => location.reload(), INTERVAL * 1000);
    }}
    function pauseRefresh() {{
      clearInterval(countdown); clearInterval(reloader);
      document.getElementById('ticker').textContent = '—';
      document.getElementById('btn-pausar').disabled = true;
      document.getElementById('btn-retomar').disabled = false;
    }}
    function resumeRefresh() {{
      document.getElementById('btn-pausar').disabled = false;
      document.getElementById('btn-retomar').disabled = true;
      startTimers();
    }}
    startTimers();
  </script>
</body>
</html>"""
    return html
