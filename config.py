"""
config.py — Variables de entorno y constantes globales.
Cuenta: sistemas@tutrastero.com
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Gmail OAuth2 ─────────────────────────────────────────────────────────────
GMAIL_CLIENT_ID     = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")
GMAIL_ACCOUNT       = os.getenv("GMAIL_ACCOUNT", "sistemas@tutrastero.com")

# ─── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ─── Airtable ─────────────────────────────────────────────────────────────────
AIRTABLE_TOKEN   = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")

AT_TBL_CLASIFICACION   = os.getenv("AT_TBL_CLASIFICACION", "")
AT_TBL_DEFINICIONES    = os.getenv("AT_TBL_DEFINICIONES", "")
AT_TBL_EJEMPLOS_CLASIF = os.getenv("AT_TBL_EJEMPLOS_CLASIF", "")

# ─── Gmail Label IDs (sistemas@tutrastero.com) ────────────────────────────────
LABEL_ANTIGRAVITY        = os.getenv("LABEL_ANTIGRAVITY", "")
LABEL_MASIP              = os.getenv("LABEL_MASIP", "")
LABEL_MICROSOFT          = os.getenv("LABEL_MICROSOFT", "")
LABEL_OPENAI             = os.getenv("LABEL_OPENAI", "")
LABEL_RADICAL            = os.getenv("LABEL_RADICAL", "")
LABEL_POWER_BI           = os.getenv("LABEL_POWER_BI", "")
LABEL_ZAPIER             = os.getenv("LABEL_ZAPIER", "")
LABEL_AIRTABLE           = os.getenv("LABEL_AIRTABLE", "")
LABEL_CGI_CLASIF         = os.getenv("LABEL_CGI_CLASIF", "")
LABEL_CGI_CLASIF_REQ     = os.getenv("LABEL_CGI_CLASIF_REQ", "")
LABEL_CGI_CLASIF_INF     = os.getenv("LABEL_CGI_CLASIF_INF", "")
LABEL_CGI_CLASIF_INT     = os.getenv("LABEL_CGI_CLASIF_INT", "")
LABEL_CGI_CLASIF_MAL     = os.getenv("LABEL_CGI_CLASIF_MAL", "")
LABEL_CGI_CLASIF_OTR     = os.getenv("LABEL_CGI_CLASIF_OTR", "")
LABEL_CGI_RESPUESTAS     = os.getenv("LABEL_CGI_RESPUESTAS", "")
LABEL_BITRIX24           = os.getenv("LABEL_BITRIX24", "")
LABEL_MAKE               = os.getenv("LABEL_MAKE", "")
LABEL_BOT_MOROSOS        = os.getenv("LABEL_BOT_MOROSOS", "")
LABEL_CONTRATACION       = os.getenv("LABEL_CONTRATACION", "")
LABEL_SISTEMAS_PROCESADO = os.getenv("LABEL_SISTEMAS_PROCESADO", "")
LABEL_YOUSIGN            = os.getenv("LABEL_YOUSIGN", "")

# ─── Deploy webhook ───────────────────────────────────────────────────────────
DEPLOY_TOKEN = os.getenv("DEPLOY_TOKEN", "")
DEPLOY_DIR   = os.getenv("DEPLOY_DIR",   "")

# ─── Gmail Push Notifications (Pub/Sub) ──────────────────────────────────────
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")
PUBSUB_TOKEN = os.getenv("PUBSUB_TOKEN", "")

# ─── Poller ───────────────────────────────────────────────────────────────────
POLL_INTERVAL        = 60    # segundos sin Pub/Sub
POLL_INTERVAL_BACKUP = 300   # segundos con Pub/Sub activo
