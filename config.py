"""
config.py — Variables de entorno y constantes globales.
Cuenta: sistemas@tutrastero.com
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Gmail OAuth2 (cuenta sistemas@tutrastero.com) ────────────────────────────
GMAIL_CLIENT_ID     = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")
GMAIL_ACCOUNT       = "sistemas@tutrastero.com"

# ─── Airtable ─────────────────────────────────────────────────────────────────
AIRTABLE_TOKEN   = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appMNiPmgPOBdXZxt")

AT_TBL_CLASIFICACION = "tblKSSUdMWhL1n2Sw"   # Clasificación (Email CGI)
AT_TBL_DEFINICIONES  = "tblcU03Ozh0QzuRGe"   # BC_Definiciones

# ─── Gmail Label IDs (sistemas@tutrastero.com) ────────────────────────────────
LABEL_ANTIGRAVITY    = "Label_7997386001421869927"
LABEL_MASIP          = "Label_2962354931939249799"
LABEL_MICROSOFT      = "Label_5254690772725038610"
LABEL_OPENAI         = "Label_1106210798511103896"
LABEL_RADICAL        = "Label_1929032039141863806"
LABEL_POWER_BI       = "Label_3205035227754163668"
LABEL_ZAPIER         = "Label_2731391320599546679"
LABEL_AIRTABLE       = "Label_1436498337126737124"
LABEL_CGI_CLASIF     = "Label_11758837844116755"    # CGI - Clasificación (padre)
LABEL_CGI_CLASIF_REQ = "Label_1352616144555492539"  # CGI - Clasificación/Requerimiento
LABEL_CGI_CLASIF_INF = "Label_3622155732607941100"  # CGI - Clasificación/Informativo
LABEL_CGI_CLASIF_INT = "Label_2253500933509770866"  # CGI - Clasificación/Interno
LABEL_CGI_CLASIF_MAL = "Label_8979777093010278871"  # CGI - Clasificación/Malicioso
LABEL_CGI_CLASIF_OTR = "Label_677008129193176150"   # CGI - Clasificación/Otros
LABEL_CGI_RESPUESTAS = "Label_6979103201746992157"  # CGI - Respuestas
LABEL_BITRIX24       = "Label_3590730105972156662"
LABEL_MAKE           = "Label_6974528327848552321"
LABEL_BOT_MOROSOS    = "Label_6267798719667247739"  # Bot Llamada Morosos
LABEL_CONTRATACION   = "Label_8799039038660883083"  # Presupuestos y Contratación Online

# ─── Deploy webhook ───────────────────────────────────────────────────────────
DEPLOY_TOKEN = os.getenv("DEPLOY_TOKEN", "")
DEPLOY_DIR   = os.getenv("DEPLOY_DIR",   "")

# ─── Gmail Push Notifications (Pub/Sub) ──────────────────────────────────────
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")
PUBSUB_TOKEN = os.getenv("PUBSUB_TOKEN", "")

# ─── Poller ───────────────────────────────────────────────────────────────────
POLL_INTERVAL        = 60    # segundos sin Pub/Sub
POLL_INTERVAL_BACKUP = 300   # segundos con Pub/Sub activo
