"""
Monitor DIAN - Devoluciones (Persona natural > Videoatención > Devoluciones)
Avisa por Telegram cuando NO aparece el pop-up "No se encontraron especialidades".

Corre en GitHub Actions (ver .github/workflows/monitor.yml).

Variables de entorno (en GitHub: Settings > Secrets and variables > Actions):
  TELEGRAM_TOKEN    token del bot (de @BotFather)
  TELEGRAM_CHAT_ID  tu chat id
  HEADLESS=0        (opcional, solo local) para ver el navegador

Uso:
  python monitor_dian.py          -> revisa una vez
  python monitor_dian.py --test   -> solo manda un mensaje de prueba a Telegram

Avisos:
  - abierto  -> alerta con sonido + captura (en cada revisión mientras siga abierto)
  - error    -> un solo aviso hasta que vuelva a funcionar
  - 1 vez al día (desde las 8 a. m.) -> "sigue vivo" silencioso, para saber que corre
  - cerrado  -> no avisa
"""
import os
import re
import sys
import json
import shutil
import pathlib
import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://agendamiento.dian.gov.co/"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
TZ = ZoneInfo("America/Bogota")
HORA_HEARTBEAT = 8  # hora local desde la que se manda el "sigue vivo" diario

BASE = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE / "estado_dian.json"
SHOT = BASE / "dian.png"

NO_HAY = re.compile(r"No se encontraron especialidades", re.I)


# ---------------- Telegram ----------------
def tg_text(msg: str, silencioso: bool = False):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": msg[:4000],
            "disable_notification": "true" if silencioso else "false",
        },
        timeout=20,
    )
    r.raise_for_status()


def tg_photo(path: pathlib.Path, caption: str):
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": f},
            timeout=30,
        )
    r.raise_for_status()


# ---------------- Estado (para no spamear) ----------------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


# ---------------- Navegación ----------------
def click_css(page, selector: str, timeout=30000, has_text=None):
    loc = page.locator(selector)
    if has_text:
        loc = loc.filter(has_text=has_text)
    loc = loc.filter(visible=True).first
    loc.wait_for(state="visible", timeout=timeout)
    loc.click()


def popup_visible(page, timeout=30000) -> bool:
    try:
        page.get_by_text(NO_HAY).filter(visible=True).first.wait_for(
            state="visible", timeout=timeout
        )
        return True
    except PWTimeout:
        return False


def revisar() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(locale="es-CO", viewport={"width": 1280, "height": 900})
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)

            # Pantalla inicial (tras ~10 s de carga): "Agendar cita"
            click_css(page, '[nombre="btnSolicitarCita"]', timeout=60000)
            # Persona Natural
            click_css(page, '.btnTipoPersona[llave="1"]')
            # Videoatención
            click_css(page, '.btnTipoAtencion[llave="2"]')

            # Por si el pop-up sale ya al elegir Videoatención
            if popup_visible(page, timeout=3000):
                return "cerrado"

            # Devoluciones
            click_css(page, ".btnCategoria", has_text="Devoluciones")

            return "cerrado" if popup_visible(page) else "abierto"
        finally:
            try:
                page.screenshot(path=str(SHOT), full_page=True)
            except Exception:
                pass
            browser.close()


# ---------------- Main ----------------
def main():
    if not TOKEN or not CHAT_ID:
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (configúralos como secrets).")
        sys.exit(1)

    if "--test" in sys.argv:
        tg_text("Prueba del monitor DIAN ✅ (desde la nube)")
        return

    now = datetime.datetime.now(TZ)
    ahora = now.strftime("%Y-%m-%d %H:%M:%S")
    hoy = now.strftime("%Y-%m-%d")
    state = load_state()
    previo = state.get("estado")

    try:
        resultado = revisar()
        if resultado == "abierto":
            # Doble verificación: evita falsos positivos cuando la página está lenta
            shutil.copy(SHOT, BASE / "dian_sospechoso.png")
            resultado = revisar()
            if resultado != "abierto":
                print(f"[{ahora}] falso positivo descartado (ver dian_sospechoso.png)")
    except Exception as e:
        print(f"[{ahora}] ERROR: {e}")
        # Avisa del error solo una vez seguida
        if previo != "error":
            tg_text(f"⚠️ Monitor DIAN falló (revisar selectores/página): {type(e).__name__}: {str(e)[:500]}")
        state["estado"] = "error"
        save_state(state)
        return  # salida 0: así GitHub no te manda un correo por cada fallo

    print(f"[{ahora}] {resultado}")

    if resultado == "abierto":
        aviso = "🚨🟢 DEVOLUCIONES ABIERTAS 🟢🚨\nhttps://agendamiento.dian.gov.co/"
        if SHOT.exists():
            tg_photo(SHOT, aviso)
        else:
            tg_text(aviso)
    elif previo == "error":
        tg_text("✅ Monitor DIAN funcionando de nuevo (cerrado)", silencioso=True)

    # "Sigue vivo" una vez al día
    if now.hour >= HORA_HEARTBEAT and state.get("heartbeat") != hoy:
        tg_text(f"🤖 Monitor DIAN activo. Última revisión {ahora}: {resultado}", silencioso=True)
        state["heartbeat"] = hoy

    state["estado"] = resultado
    save_state(state)


if __name__ == "__main__":
    main()
