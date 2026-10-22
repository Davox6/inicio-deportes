"""
Genera deportes.json para "Inicio", el launcher de la tele de casa: partidos de fútbol en directo y próximos,
con su canal en la televisión española y los escudos de los equipos.

- Canales y horas: guía de TV pública EPG dobleM (github.com/davidmuma/EPG_dobleM). Los partidos en directo
  aparecen como "DIRECTO Jornada 10: Barcelona - Real Madrid T26/27 · LALIGA EA SPORTS".
- Escudos, estado y marcador: football-data.org (plan gratuito, clave en la variable FOOTBALL_DATA_KEY).

La guía pesa ~5 MB comprimida: se descarga solo si ha cambiado (ETag) y se guarda ya filtrada en la carpeta de
salida, así que la mayoría de ejecuciones solo consultan football-data. Solo biblioteca estándar de Python.
"""

import argparse
import datetime as dt
import difflib
import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

GUIA_URL = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv_light.xml.gz"
FD_URL = "https://api.football-data.org/v4"
USER_AGENT = "inicio-deportes/1.0 (launcher casero)"

HORAS_ATRAS = 3        # partidos empezados hace como mucho 3 h (pueden seguir en juego)
DIAS_ADELANTE = 7      # la guía de TV no llega más allá
# Días que se buscan en football-data según cuántas jornadas se muestran (las de la Champions se separan semanas).
BUSQUEDA_DIAS = {1: 14, 2: 24, 3: 35}
CHAMPIONS_BUSQUEDA = 45
TRAMO_FD = 10          # football-data no admite más de 10 días por petición
DURACION_MIN = 120     # duración aproximada si no hay otra
VERSION = 1


# ---- Utilidades -------------------------------------------------------------------------------------------

def ahora():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def descargar(url, cabeceras=None, timeout=60):
    pet = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(cabeceras or {})})
    return urllib.request.urlopen(pet, timeout=timeout)


def sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


# Palabras que no ayudan a reconocer un equipo ("RCD Espanyol de Barcelona" ~ "Espanyol").
RELLENO = {"fc", "cf", "cd", "ud", "sd", "rc", "rcd", "ca", "sad", "club", "de", "del", "la", "el", "futbol",
           "balompie", "r", "c", "f", "afc", "ac", "as", "ss", "sc", "sv", "tsg", "vfl", "vfb", "bc", "if", "fk", "sk"}

# Nombres que la guía y football-data escriben de forma muy distinta.
ALIAS = {
    "atleti": "atletico madrid", "atletico de madrid": "atletico madrid", "at madrid": "atletico madrid",
    "barca": "barcelona", "athletic bilbao": "athletic", "athletic club": "athletic",
    "betis": "real betis", "deportivo": "deportivo coruna", "rc deportivo": "deportivo coruna",
    "racing": "racing santander", "r racing club": "racing santander", "racing club": "racing santander",
    "alaves": "alaves", "celta": "celta vigo", "rayo": "rayo vallecano",
    "bayern munich": "bayern munchen", "bayern": "bayern munchen", "leipzig": "rb leipzig",
    "psg": "paris saint germain", "inter": "internazionale", "milan": "ac milan",
}


def clave_equipo(nombre):
    s = sin_acentos(nombre).lower().replace(".", " ").replace("-", " ").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    if s in ALIAS:
        s = ALIAS[s]
    palabras = [p for p in s.split() if p not in RELLENO]
    s = " ".join(palabras) or s
    return ALIAS.get(s, s)


def parecido(a, b):
    """0..1: cómo de probable es que dos nombres sean el mismo equipo."""
    ka, kb = clave_equipo(a), clave_equipo(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    # Palabras completas: "lillestrom" contiene "lille", pero no es el Lille.
    if ta <= tb or tb <= ta:
        return 0.9
    comunes = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(comunes * 0.85, difflib.SequenceMatcher(None, ka, kb).ratio())


# ---- Guía de TV -------------------------------------------------------------------------------------------

TITULO = re.compile(
    r"^(?P<directo>DIRECTO\s+)?(?:(?P<ronda>[^:]{1,40}):\s+)?(?P<local>.+?)\s+-\s+(?P<visitante>.+?)\s+T\d{2,4}(?:/\d{2})?\s*·\s*(?P<comp>.+)$"
)
NO_PARTIDO = re.compile(r"\b(previa|post|resumen|res[uú]menes|la casa del f[uú]tbol)\b", re.I)
# Canales solo para bares: no están en la oferta de casa.
CANAL_BAR = re.compile(r"\bBAR\b")
# Canales de los que se recogen partidos: Movistar Plus+ (M+), DAZN, LaLiga TV y los de la TDT que dan fútbol.
# La tele decide luego cuáles se pueden ver (los que tiene el deco, y los de DAZN en su app).
CANALES_GUIA = re.compile(r"^(M\+ |Movistar Plus|DAZN|LaLiga TV|La 1 HD$|La 2$|Teledeporte$)")
# Los tres grandes: sus partidos van primero dentro de su competición.
GRANDES = {"real madrid", "barcelona", "atletico madrid"}


def hora_guia(s):
    # "20261025210000 +0200"
    return dt.datetime.strptime(s, "%Y%m%d%H%M%S %z")


# "Ahora en tus canales" para la pantalla de Inicio: lo que se emite ahora y a continuación.
# (id del canal en la guía, nombre para la tele)
CANALES_AHORA = [
    ("La 1 HD", "La 1"), ("La 2", "La 2"), ("Antena 3 HD", "Antena 3"), ("Cuatro HD", "Cuatro"),
    ("Telecinco HD", "Telecinco"), ("La Sexta HD", "laSexta"), ("Teledeporte", "Teledeporte"),
    ("M+ LaLiga HD", "M+ LaLiga"), ("DAZN LaLiga HD", "DAZN LaLiga"), ("M+ Liga de Campeones HD", "M+ Liga de Campeones"),
]
AHORA_HORAS = 16  # cubre de sobra hasta la siguiente actualización de la guía
# "Mañaneros 360 T3 E44" → "Mañaneros 360": temporada y episodio no aportan nada en la tele.
TEMPORADA = re.compile(r"\s+T\d{1,4}(?:/\d{2})?(?:\s+E\d+)?$|\s+E\d+$")


def leer_guia(competiciones, desde, hasta):
    """Descarga la guía: emisiones en directo de las competiciones activas y la programación de CANALES_AHORA."""
    emisiones = []
    ids_ahora = {c for c, _ in CANALES_AHORA}
    programas = {c: [] for c in ids_ahora}
    t = ahora()
    ventana = (t - dt.timedelta(hours=1), t + dt.timedelta(hours=AHORA_HORAS))
    with descargar(GUIA_URL, timeout=120) as r:
        generada = None
        for _, el in ET.iterparse(gzip.GzipFile(fileobj=r), events=("end",)):
            if el.tag == "tv":
                generada = el.get("generator-info-name")
            if el.tag != "programme":
                continue
            titulo = (el.findtext("title") or "").strip()
            start, stop, canal = el.get("start"), el.get("stop"), el.get("channel")
            el.clear()  # libera memoria: la guía descomprimida pasa de 50 MB
            if canal in ids_ahora:
                ini, fin = hora_guia(start), hora_guia(stop)
                if fin > ventana[0] and ini < ventana[1]:
                    programas[canal].append({"titulo": TEMPORADA.sub("", titulo), "inicio": iso(ini), "fin": iso(fin)})
            # Los partidos llevan "DIRECTO" delante, pero no siempre con días de antelación: sin él también se
            # guardan, y luego solo cuentan si empiezan a la hora del partido según football-data (no reposiciones).
            if NO_PARTIDO.search(titulo) or CANAL_BAR.search(canal or "") or not CANALES_GUIA.match(canal or ""):
                continue
            m = TITULO.match(titulo)
            if not m:
                continue
            comp = next((c for c in competiciones if any(k.lower() in m["comp"].lower() for k in c["claves"])), None)
            if comp is None:
                continue
            inicio = hora_guia(start)
            if not (desde <= inicio <= hasta):
                continue
            emisiones.append({
                "competicion": comp["nombre"],
                "ronda": (m["ronda"] or "").strip(),
                "local": m["local"].strip(),
                "visitante": m["visitante"].strip(),
                "inicio": iso(inicio),
                "fin": iso(hora_guia(stop)),
                "canal": canal,
                "directo": bool(m["directo"]),
            })
    return emisiones, programas, generada


def guia_con_cache(competiciones, desde, hasta, cache_path):
    """La guía solo cambia 4 veces al día: si no ha cambiado (ETag), se reutiliza lo ya filtrado."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    etag = None
    try:
        with descargar(GUIA_URL, {"Range": "bytes=0-0"}, timeout=30) as r:
            etag = r.headers.get("ETag")
    except urllib.error.URLError as e:
        print(f"Aviso: no se pudo consultar la guía ({e}); se usa la última", file=sys.stderr)
    firma = json.dumps([etag, competiciones, CANALES_AHORA, desde.date().isoformat()], sort_keys=True)
    if (etag and cache.get("firma") == firma) or (etag is None and cache):
        return cache["emisiones"], cache.get("programas", {}), cache.get("generada"), False
    emisiones, programas, generada = leer_guia(competiciones, desde - dt.timedelta(days=1), hasta)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"firma": firma, "generada": generada, "emisiones": emisiones, "programas": programas}, f, ensure_ascii=False)
    return emisiones, programas, generada, True


# ---- football-data.org ------------------------------------------------------------------------------------

_peticiones = []


def fd(ruta, clave):
    """Petición a football-data respetando su límite gratuito (10 por minuto): si hace falta, espera."""
    import time
    ahora_s = time.monotonic()
    while len([x for x in _peticiones if ahora_s - x < 60]) >= 9:
        time.sleep(3)
        ahora_s = time.monotonic()
    _peticiones.append(ahora_s)
    with descargar(f"{FD_URL}{ruta}", {"X-Auth-Token": clave}) as r:
        return json.load(r)


def equipos_fd(codigos, clave, cache_path):
    """Equipos (nombre, nombre corto y escudo) de las competiciones con datos; se renuevan una vez al día."""
    hoy = ahora().date().isoformat()
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("dia") == hoy and set(cache.get("codigos", [])) == set(codigos):
            return cache["equipos"]
    equipos = {}
    for codigo in codigos:
        for e in fd(f"/competitions/{codigo}/teams", clave).get("teams", []):
            equipos[str(e["id"])] = {
                "nombre": e.get("name"), "corto": e.get("shortName"), "escudo": png(e.get("crest")),
                "competiciones": sorted(set(equipos.get(str(e["id"]), {}).get("competiciones", []) + [codigo])),
            }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"dia": hoy, "codigos": codigos, "equipos": equipos}, f, ensure_ascii=False)
    return equipos


def png(url):
    # La tele (Android 10) no dibuja SVG: football-data tiene los mismos escudos en PNG de 200 px.
    if not url:
        return None
    return re.sub(r"\.svg$", ".png", url)


ESTADOS = {
    "SCHEDULED": "PROGRAMADO", "TIMED": "PROGRAMADO", "IN_PLAY": "EN_JUEGO", "PAUSED": "DESCANSO",
    "FINISHED": "FINALIZADO", "AWARDED": "FINALIZADO", "POSTPONED": "APLAZADO", "SUSPENDED": "SUSPENDIDO",
    "CANCELLED": "CANCELADO",
}


def proximas_jornadas(competiciones_fd, fd_partidos, desde):
    """
    De cada competición, solo sus próximas 'jornadas' jornadas (por orden de fecha). Las jornadas con pocos partidos
    (un aplazado de otra jornada) no cuentan como una de las N, pero salen si caen dentro de las elegidas.
    """
    elegidos = []
    for c in competiciones_fd:
        codigo = c["football_data"]
        propios = sorted(
            ((dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")), m) for m in fd_partidos
             if m["competition"]["code"] == codigo and m["homeTeam"].get("name")),
            key=lambda x: x[0],
        )
        propios = [(i, m) for i, m in propios if i >= desde]
        grupos = {}
        for i, m in propios:
            grupos.setdefault((m.get("stage"), m.get("matchday")), []).append(i)
        orden = sorted(grupos, key=lambda k: min(grupos[k]))
        grandes = [k for k in orden if len(grupos[k]) >= 3][: c.get("jornadas", 1)]
        if not grandes:
            grandes = orden[:1]
        fin = max(max(grupos[k]) for k in grandes) if grandes else desde
        elegidos += [m for i, m in propios if i <= fin]
    return elegidos


def ampliar_busqueda(c):
    return CHAMPIONS_BUSQUEDA if c.get("football_data") == "CL" else BUSQUEDA_DIAS.get(c.get("jornadas", 1), 21)


def partidos_fd(competiciones_fd, clave, desde, limites, cache_path):
    """
    Partidos de cada competición hasta su propio horizonte ("dias" en competiciones.json: la Champions se ve con
    semanas de antelación, aunque su canal no se sepa hasta que la guía de TV lo publique). En tramos de 10 días.
    """
    partidos = {}
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    for c in competiciones_fd:
        inicio, fin = desde.date(), limites[c["football_data"]].date()
        while inicio <= fin:
            tramo = min(fin, inicio + dt.timedelta(days=TRAMO_FD - 1))
            ruta = f"/competitions/{c['football_data']}/matches?dateFrom={inicio.isoformat()}&dateTo={tramo.isoformat()}"
            lejano = inicio > desde.date() + dt.timedelta(days=TRAMO_FD)
            guardado = cache.get(ruta)
            if lejano and guardado and ahora().timestamp() - guardado["t"] < 6 * 3600:
                matches = guardado["matches"]
            else:
                matches = fd(ruta, clave).get("matches", [])
                if lejano:
                    cache[ruta] = {"t": ahora().timestamp(), "matches": matches}
            for m in matches:
                partidos[m["id"]] = m
            inicio = tramo + dt.timedelta(days=1)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cache.items() if ahora().timestamp() - v["t"] < 6 * 3600}, f, ensure_ascii=False)
    return list(partidos.values())


# ---- Cruce ------------------------------------------------------------------------------------------------

# Nombres cortos de football-data que en España se dicen de otra forma.
CORTOS = {"Barça": "Barcelona", "Atleti": "Atlético", "Santander": "Racing", "Sevilla FC": "Sevilla"}


def nombre_visible(nombre_guia, equipo):
    """Nombre corto y claro para la tele: el de la guía sin siglas ("RCD Espanyol de Barcelona" → "Espanyol")."""
    if equipo and equipo.get("corto"):
        return CORTOS.get(equipo["corto"], equipo["corto"])
    limpio = re.sub(r"^(?:(?:FC|CF|CD|UD|SD|RC|RCD|CA|R\.)\s+)+", "", nombre_guia)
    limpio = re.sub(r"\s+(?:FC|CF|CD|UD|SD|SAD)$", "", limpio)
    return limpio


def buscar_equipo(nombre, equipos):
    mejor, nota = None, 0.0
    for e in equipos.values():
        n = max(parecido(nombre, e["nombre"] or ""), parecido(nombre, e["corto"] or ""))
        if n > nota:
            mejor, nota = e, n
    return mejor if nota >= 0.75 else None


def es_espanol(nombre, equipos):
    if clave_equipo(nombre) == "espana":
        return True
    e = buscar_equipo(nombre, equipos)
    return bool(e and "PD" in e["competiciones"])


# Fases de las competiciones europeas (football-data) como se dicen en la tele.
FASES = {
    "PLAYOFFS": "Play-off", "LAST_32": "Dieciseisavos", "LAST_16": "Octavos de final",
    "QUARTER_FINALS": "Cuartos de final", "SEMI_FINALS": "Semifinales", "FINAL": "Final",
}


def ronda_fd(m):
    if m.get("stage") in ("REGULAR_SEASON", "LEAGUE_STAGE", "GROUP_STAGE") and m.get("matchday"):
        return f"Jornada {m['matchday']}"
    return FASES.get(m.get("stage"), "")


def juntar(emisiones, fd_partidos, equipos, competiciones, desde, hasta, limites):
    por_nombre = {c["nombre"]: c for c in competiciones}
    partidos = {}

    def clave_partido(local, visitante, inicio):
        return f"{inicio[:10]}|{clave_equipo(local)}|{clave_equipo(visitante)}"

    # 1) Partidos de football-data (LaLiga, Champions): fecha, estado, marcador y escudos seguros.
    codigo_a_comp = {c.get("football_data"): c["nombre"] for c in competiciones if c.get("football_data")}
    for m in fd_partidos:
        inicio = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        if not (desde <= inicio <= limites.get(m["competition"]["code"], hasta)):
            continue
        local, visitante = m["homeTeam"], m["awayTeam"]
        if not local.get("name") or not visitante.get("name"):
            continue  # eliminatorias aún sin rivales
        eq_l = equipos.get(str(local.get("id"))) or {"nombre": local["name"], "corto": local.get("shortName"), "escudo": png(local.get("crest"))}
        eq_v = equipos.get(str(visitante.get("id"))) or {"nombre": visitante["name"], "corto": visitante.get("shortName"), "escudo": png(visitante.get("crest"))}
        marcador = m.get("score", {}).get("fullTime", {})
        partidos[f"fd{m['id']}"] = {
            "id": f"fd{m['id']}",
            "competicion": codigo_a_comp.get(m["competition"]["code"], m["competition"]["name"]),
            "ronda": ronda_fd(m),
            "inicio": iso(inicio),
            "estado": ESTADOS.get(m["status"], "PROGRAMADO"),
            "marcador": [marcador.get("home"), marcador.get("away")] if marcador.get("home") is not None else None,
            "local": {"nombre": nombre_visible(local["name"], eq_l), "escudo": eq_l.get("escudo")},
            "visitante": {"nombre": nombre_visible(visitante["name"], eq_v), "escudo": eq_v.get("escudo")},
            "_nombres": (eq_l["nombre"], eq_l.get("corto") or "", eq_v["nombre"], eq_v.get("corto") or ""),
            "canales": [],
        }

    # 2) Emisiones de la guía: se cuelgan del partido de football-data que encaje, o crean uno nuevo.
    for e in emisiones:
        inicio = dt.datetime.fromisoformat(e["inicio"].replace("Z", "+00:00"))
        if not (desde - dt.timedelta(hours=1) <= inicio <= hasta):
            continue
        comp = por_nombre[e["competicion"]]
        mejor, nota = None, 0.0
        for p in partidos.values():
            dif = abs((dt.datetime.fromisoformat(p["inicio"].replace("Z", "+00:00")) - inicio).total_seconds())
            # En directo, la emisión empieza unos minutos antes del saque; sin la marca, tiene que cuadrar más.
            if dif > (3 * 3600 if e["directo"] else 35 * 60):
                continue
            n_l = max(parecido(e["local"], p["_nombres"][0]), parecido(e["local"], p["_nombres"][1]))
            n_v = max(parecido(e["visitante"], p["_nombres"][2]), parecido(e["visitante"], p["_nombres"][3]))
            n = min(n_l, n_v) if p["competicion"] != e["competicion"] else max(min(n_l, n_v), (n_l + n_v) / 2 - 0.1)
            if n > nota:
                mejor, nota = p, n
        if mejor is None or nota < 0.6:
            if not e["directo"]:
                continue  # sin partido de football-data que lo confirme, puede ser una reposición
            if comp.get("solo_espanoles") and not (es_espanol(e["local"], equipos) or es_espanol(e["visitante"], equipos)):
                continue
            k = clave_partido(e["local"], e["visitante"], e["inicio"])
            if k not in partidos:
                # La emisión en directo empieza unos minutos antes del saque (18:38 para un partido de las 18:45):
                # se redondea al siguiente cuarto de hora, que es como se anuncian los partidos.
                saque = inicio + dt.timedelta(minutes=(-inicio.minute) % 15, seconds=-inicio.second)
                eq_l, eq_v = buscar_equipo(e["local"], equipos), buscar_equipo(e["visitante"], equipos)
                partidos[k] = {
                    "id": "g" + re.sub(r"[^a-z0-9]+", "-", k),
                    "competicion": e["competicion"], "ronda": e["ronda"], "inicio": iso(saque),
                    # Sin football-data no se sabe el estado: la tele lo deduce de la hora.
                    "estado": None, "marcador": None,
                    "local": {"nombre": nombre_visible(e["local"], eq_l), "escudo": eq_l and eq_l["escudo"]},
                    "visitante": {"nombre": nombre_visible(e["visitante"], eq_v), "escudo": eq_v and eq_v["escudo"]},
                    "_nombres": (e["local"], "", e["visitante"], ""), "canales": [],
                }
            mejor = partidos[k]
        if not mejor["ronda"] and e["ronda"]:
            mejor["ronda"] = e["ronda"]
        if e["canal"] not in mejor["canales"]:
            mejor["canales"].append(e["canal"])

    resultado = []
    for p in partidos.values():
        nombres = p.pop("_nombres", None) or ()
        p["prioridad"] = por_nombre.get(p["competicion"], {}).get("rango", 99)
        p["destacado"] = any(clave_equipo(n) in GRANDES for n in nombres if n) or \
            any(clave_equipo(x["nombre"]) in GRANDES for x in (p["local"], p["visitante"]))
        # Con algún equipo español (de LaLiga): va antes que los demás de su competición.
        p["espanol"] = p["destacado"] or any(es_espanol(n, equipos) for n in nombres[::2] if n)
        resultado.append(p)
    resultado.sort(key=lambda p: (p["inicio"], p["prioridad"]))
    return resultado


def escribir_ahora(programas, generada, carpeta):
    """ahora.json: programación de los próximos ~16 h de cada canal de CANALES_AHORA (la tele elige el actual)."""
    canales = [{"id": c, "nombre": n, "programas": sorted(programas.get(c, []), key=lambda p: p["inicio"])}
               for c, n in CANALES_AHORA]
    destino = os.path.join(carpeta, "ahora.json")
    contenido = {"version": 1, "guia": generada, "canales": canales}
    if os.path.exists(destino):
        with open(destino, encoding="utf-8") as f:
            if json.load(f) == contenido:
                return
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Escrito {destino}: {sum(len(c['programas']) for c in canales)} programas en {len(canales)} canales")


# ---- Principal --------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--config", default="competiciones.json")
    args = ap.parse_args()
    os.makedirs(args.salida, exist_ok=True)

    with open(args.config, encoding="utf-8") as f:
        competiciones = [c for c in json.load(f)["competiciones"] if c.get("activa")]
    clave = os.environ.get("FOOTBALL_DATA_KEY", "").strip()

    t = ahora()
    desde, hasta = t - dt.timedelta(hours=HORAS_ATRAS), t + dt.timedelta(days=DIAS_ADELANTE)

    emisiones, programas, generada, guia_nueva = guia_con_cache(competiciones, desde, hasta, os.path.join(args.salida, "guia-filtrada.json"))
    print(f"Guía: {len(emisiones)} emisiones en directo ({'descargada' if guia_nueva else 'sin cambios'}; {generada})")

    competiciones_fd = [c for c in competiciones if c.get("football_data")]
    codigos = [c["football_data"] for c in competiciones_fd]
    # Horizonte de cada competición de football-data (por defecto, el de la guía de TV).
    # Se busca con margen (una jornada de Champions puede caer a 6 semanas) y luego se dejan las próximas N jornadas.
    busqueda = {c["football_data"]: t + dt.timedelta(days=ampliar_busqueda(c)) for c in competiciones_fd}
    limites = dict(busqueda)
    equipos, fd_partidos = {}, []
    if clave and codigos:
        try:
            equipos = equipos_fd(codigos, clave, os.path.join(args.salida, "equipos.json"))
            fd_partidos = partidos_fd(competiciones_fd, clave, desde, busqueda, os.path.join(args.salida, "fd-cache.json"))
            fd_partidos = proximas_jornadas(competiciones_fd, fd_partidos, desde)
        except urllib.error.URLError as e:
            print(f"Aviso: football-data no responde ({e}); solo con la guía", file=sys.stderr)
    else:
        print("Aviso: sin FOOTBALL_DATA_KEY; solo con la guía (sin escudos ni marcador)", file=sys.stderr)
    print(f"football-data: {len(fd_partidos)} partidos, {len(equipos)} equipos")

    escribir_ahora(programas, generada, args.salida)

    partidos = juntar(emisiones, fd_partidos, equipos, competiciones, desde, hasta, limites)
    salida = {
        "version": VERSION,
        "generado": iso(t),
        "guia": generada,
        "partidos": partidos,
    }
    destino = os.path.join(args.salida, "deportes.json")
    nuevo = json.dumps(salida, ensure_ascii=False, indent=1)
    # Si solo cambia la hora de generación, no se reescribe (así no hay commit).
    if os.path.exists(destino):
        with open(destino, encoding="utf-8") as f:
            viejo = json.load(f)
        if viejo.get("partidos") == partidos and viejo.get("guia") == generada:
            print(f"Sin cambios: {len(partidos)} partidos")
            return
    with open(destino, "w", encoding="utf-8") as f:
        f.write(nuevo)
    print(f"Escrito {destino}: {len(partidos)} partidos, {sum(1 for p in partidos if p['canales'])} con canal")


if __name__ == "__main__":
    main()
