"""
Monitor DIAN - Devoluciones (Persona natural > Videoatención > Devoluciones)
Avisa por Telegram cuando NO aparece el pop-up "No se encontraron especialidades".

Corre en GitHub Actions (ver .github/workflows/monitor.yml).

Variables de entorno (en GitHub: Settings > Secrets and variables > Actions):
  TELEGRAM_TOKEN    token del bot (de @BotFather)
  TELEGRAM_CHAT_ID  chat ids separados por coma. El primero (tú) recibe todo;
                    los demás solo reciben la alerta de ABIERTO.
  HEADLESS=0        (opcional, solo local) para ver el navegador

Uso:
  python monitor_dian.py          -> revisa una vez
  python monitor_dian.py --test   -> solo manda un mensaje de prueba a Telegram

Avisos:
  - abierto  -> alerta con sonido + captura (en cada revisión mientras siga abierto)
  - error    -> un solo aviso hasta que vuelva a funcionar
  - cada 6 h (00, 06, 12, 18 hora Bogotá) -> resumen silencioso: corridas,
                cerrado/abierto/error (solo al primer chat id)
  - cerrado  -> no avisa (va en el resumen)
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
CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
CHAT_ID = CHAT_IDS[0] if CHAT_IDS else ""
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
TZ = ZoneInfo("America/Bogota")

BASE = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE / "estado_dian.json"
SHOT = BASE / "dian.png"

NO_HAY = re.compile(r"No se encontraron especialidades", re.I)


# ---------------- Telegram ----------------
def tg_text(msg: str, silencioso: bool = False, chat_id: str = None):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={
            "chat_id": chat_id or CHAT_ID,
            "text": msg[:4000],
            "disable_notification": "true" if silencioso else "false",
        },
        timeout=20,
    )
    r.raise_for_status()


def tg_photo(path: pathlib.Path, caption: str, chat_id: str = None):
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id": chat_id or CHAT_ID, "caption": caption},
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


# ---------------- Reporte cada 6 horas ----------------
HORAS_REPORTE = 6
ESPERADAS = HORAS_REPORTE * 6  # una corrida cada 10 min


def bloque_de(now: datetime.datetime) -> str:
    """Bloques fijos de 6 h en hora Bogotá: 00-06, 06-12, 12-18, 18-24."""
    return f"{now:%Y-%m-%d}T{now.hour // HORAS_REPORTE * HORAS_REPORTE:02d}"


def stats_vacias() -> dict:
    return {"total": 0, "cerrado": 0, "abierto": 0, "error": 0, "ultima": ""}


def reporte_si_toca(state: dict, now: datetime.datetime):
    """Si cambiamos de bloque, manda el resumen del bloque anterior y reinicia."""
    actual = bloque_de(now)
    anterior = state.get("bloque")
    if anterior is None:
        state["bloque"], state["stats"] = actual, stats_vacias()
        return
    if anterior == actual:
        return
    st = state.get("stats") or stats_vacias()
    h = int(anterior[-2:])
    rango = f"{anterior[:10]} {h:02d}:00–{(h + HORAS_REPORTE) % 24:02d}:00"
    msg = (
        f"📊 Monitor DIAN · {rango}\n"
        f"Corridas: {st['total']} de ~{ESPERADAS}\n"
        f"🔴 cerrado: {st['cerrado']}\n"
        f"🟢 abierto: {st['abierto']}\n"
        f"⚠️ error: {st['error']}\n"
        f"Última: {st['ultima'] or '—'}"
    )
    try:
        tg_text(msg, silencioso=True)
    except Exception as e:
        print(f"no se pudo mandar el reporte: {e}")
        return  # se reintenta en la próxima corrida
    state["bloque"], state["stats"] = actual, stats_vacias()


def registrar(state: dict, resultado: str, now: datetime.datetime):
    st = state.setdefault("stats", stats_vacias())
    st["total"] += 1
    st[resultado] = st.get(resultado, 0) + 1
    st["ultima"] = f"{now:%H:%M} {resultado}"
    state["estado"] = resultado


# ---------------- Main ----------------
def main():
    if not TOKEN or not CHAT_ID:
        print("Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (configúralos como secrets).")
        sys.exit(1)

    if "--test" in sys.argv:
        for cid in CHAT_IDS:
            try:
                tg_text("Prueba del monitor DIAN ✅ (desde la nube)", chat_id=cid)
                print(f"prueba enviada a {cid}")
            except Exception as e:
                print(f"no se pudo enviar a {cid}: {e} (¿ya le dio /start al bot?)")
        return

    now = datetime.datetime.now(TZ)
    ahora = now.strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    previo = state.get("estado")

    # Primero cierra el bloque anterior (si cambió), luego cuenta esta corrida
    reporte_si_toca(state, now)

    try:
        resultado = revisar()
        if resultado == "abierto":
            # Doble verificación: evita falsos positivos cuando la página está lenta
            if SHOT.exists():
                shutil.copy(SHOT, BASE / "dian_sospechoso.png")
            resultado = revisar()
            if resultado != "abierto":
                print(f"[{ahora}] falso positivo descartado (ver dian_sospechoso.png)")
    except Exception as e:
        print(f"[{ahora}] ERROR: {e}")
        # Avisa del error solo una vez seguida
        if previo != "error":
            try:
                tg_text(f"⚠️ Monitor DIAN falló (revisar selectores/página): {type(e).__name__}: {str(e)[:500]}")
            except Exception as e2:
                print(f"no se pudo avisar el error: {e2}")
        registrar(state, "error", now)
        save_state(state)
        return  # salida 0: así GitHub no te manda un correo por cada fallo

    print(f"[{ahora}] {resultado}")
    registrar(state, resultado, now)
    save_state(state)

    if resultado == "abierto":
        aviso = "🚨🟢 DEVOLUCIONES ABIERTAS 🟢🚨\nhttps://agendamiento.dian.gov.co/"
        for cid in CHAT_IDS:
            try:
                if SHOT.exists():
                    tg_photo(SHOT, aviso, chat_id=cid)
                else:
                    tg_text(aviso, chat_id=cid)
            except Exception as e:
                print(f"no se pudo avisar a {cid}: {e}")


if __name__ == "__main__":
    main()
