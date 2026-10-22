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
    # Formas cortas o traducidas de la guía de Movistar.
    "man city": "manchester city", "man united": "manchester united", "mgladbach": "borussia monchengladbach",
    "shaktar": "shakhtar donetsk", "sl bratislava": "slovan bratislava", "pae aek": "aek athens",
    "salzburgo": "red bull salzburg", "friburgo": "freiburg", "venecia": "venezia",
    "universitatea craiova": "csu craiova", "olympique lyon": "olympique lyonnais",
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
# Los "multi" de las noches europeas: todos los partidos a la vez ("Jornada 3: Multieuropa League + Conference T26/27 ·
# UEFA Europa League"). No llevan "local - visitante".
MULTI = re.compile(
    r"^(?P<directo>DIRECTO\s+)?(?:(?P<ronda>[^:]{1,40}):\s+)?(?P<nombre>Multi.+?)\s+T\d{2,4}(?:/\d{2})?\s*·\s*(?P<comp>.+)$", re.I
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


def nombre_multi(nombre):
    """"Multieuropa League + Conference" → "Multi Europa League + Conference"; "Multiliga de Campeones" → "Multi Champions"."""
    resto = re.sub(r"(?i)^liga de campeones", "Champions", nombre.strip()[len("multi"):].strip())
    return "Multi " + resto[:1].upper() + resto[1:]


def saque(inicio):
    """La emisión en directo empieza unos minutos antes del saque (18:38 para un partido de las 18:45): se redondea al
    siguiente cuarto de hora, que es como se anuncian los partidos."""
    return inicio + dt.timedelta(minutes=(-inicio.minute) % 15, seconds=-inicio.second)


# "Ahora en tus canales" para la pantalla de Inicio: lo que se emite ahora y a continuación.
# (id del canal en la guía, nombre para la tele)
CANALES_AHORA = [
    ("La 1 HD", "La 1"), ("La 2", "La 2"), ("Antena 3 HD", "Antena 3"), ("Cuatro HD", "Cuatro"),
    ("Telecinco HD", "Telecinco"), ("La Sexta HD", "laSexta"), ("Teledeporte", "Teledeporte"),
    ("M+ LaLiga HD", "M+ LaLiga"), ("DAZN LaLiga HD", "DAZN LaLiga"), ("M+ Liga de Campeones HD", "M+ Liga de Campeones"),
]
# Cambia cuando cambia lo que se guarda de la guía: así no se reutiliza una caché del formato anterior.
GUIA_FORMATO = 2
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
            multi = None if m else MULTI.match(titulo)
            if not m and not multi:
                continue
            m = m or multi
            comp = next((c for c in competiciones if any(k.lower() in m["comp"].lower() for k in c["claves"])), None)
            if comp is None:
                continue
            inicio = hora_guia(start)
            if not (desde <= inicio <= hasta):
                continue
            if multi:
                emisiones.append({
                    "competicion": comp["nombre"],
                    "multi": nombre_multi(multi["nombre"]),
                    "ronda": (multi["ronda"] or "").strip(),
                    "inicio": iso(inicio),
                    "canal": canal,
                })
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
    firma = json.dumps([GUIA_FORMATO, etag, competiciones, CANALES_AHORA, desde.date().isoformat()], sort_keys=True)
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


# Filiales ("Real Sociedad B", "Real Madrid Castilla"): no son el primer equipo ni llevan su escudo.
FILIAL = re.compile(r"\b(b|c|ii|castilla|promesas)$")


def buscar_equipo(nombre, equipos, ligas=None):
    """Equipo de football-data con ese nombre (para el escudo y para saber si es español).

    Aquí no hay hora ni rival que confirmen el emparejamiento, así que un nombre parecido no basta ("Almería" no es
    el Palmeiras, ni "Real Oviedo" la Real Sociedad): tiene que ser el mismo, contenerlo palabra por palabra sin dudar
    entre dos equipos, o casi idéntico ("Oporto" y "Porto"). [ligas]: solo equipos de esas competiciones.
    """
    clave = clave_equipo(nombre)
    if not clave or FILIAL.search(clave):
        return None
    palabras = set(clave.split())
    exactos, contienen, casi = [], [], []
    for e in equipos.values():
        if ligas and not set(ligas) & set(e.get("competiciones", [])):
            continue
        claves = {clave_equipo(n) for n in (e["nombre"], e["corto"]) if n}
        if clave in claves:
            exactos.append(e)
        elif any(palabras <= set(k.split()) or set(k.split()) <= palabras for k in claves if k):
            contienen.append(e)
        elif any(difflib.SequenceMatcher(None, clave, k).ratio() >= 0.88 for k in claves if k):
            casi.append(e)
    candidatos = exactos or contienen or casi
    return candidatos[0] if len(candidatos) == 1 else None


def es_espanol(nombre, equipos):
    if clave_equipo(nombre) == "espana":
        return True
    e = buscar_equipo(nombre, equipos)
    return bool(e and "PD" in e["competiciones"])


# ---- Escudos de nuestra biblioteca ---------------------------------------------------------------------

# Nombres revisados a mano (escudos-equivalencias.json); main() los carga.
EQUIVALENCIAS = {}

def cargar_indice_escudos(ruta):
    """escudos-indice.json (herramientas/indice_escudos.py) con las claves de cada nombre ya calculadas."""
    if not os.path.exists(ruta):
        print(f"Aviso: sin {ruta}; solo escudos de football-data", file=sys.stderr)
        return []
    with open(ruta, encoding="utf-8") as f:
        equipos = json.load(f)["equipos"]
    for e in equipos:
        # "Stade Rennais (Rennes)", "Hamburger (HSV)": lo de dentro del paréntesis es otro nombre del mismo equipo.
        nombres = []
        for n in [e["nombre"]] + e["alias"]:
            m = re.match(r"^(.*?)\s*\((.*)\)\s*$", n)
            nombres += [m.group(1), m.group(2)] if m else [n]
        e["_claves"] = {k for k in (clave_equipo(n) for n in nombres) if k}
    return equipos


def cargar_equivalencias(ruta):
    """escudos-equivalencias.json: nombre → [{id, competiciones}] revisados a mano (id null = sin escudo a propósito)."""
    if not os.path.exists(ruta):
        return {}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)["equivalencias"]


def escudo_de(nombre, comp, indice, _cache={}, equivalencias=None):
    """
    ID del escudo de un equipo en nuestra biblioteca ("club:celtic") o None. Como buscar_equipo (mismo nombre,
    contenido palabra por palabra o casi idéntico; un filial solo con su propio escudo), pero los empates se
    deciden por el partido: primero los equipos de esa competición en la biblioteca ("escudos_de"), luego los de
    su país, luego el nombre más directo. En una liga nacional, solo clubes de ese país ("pais"). Si aún
    hay dudas, None: mejor las iniciales que el escudo de otro club.
    """
    # Primero lo revisado a mano: si el nombre está, manda (también cuando dice que no lleva escudo).
    revisados = (equivalencias or EQUIVALENCIAS).get(nombre)
    if revisados:
        de_esta = [r for r in revisados if comp.get("nombre") in r.get("competiciones", [])]
        if de_esta or len(revisados) == 1:
            return (de_esta or revisados)[0]["id"]
    clave = clave_equipo(nombre)
    if not clave or not indice:
        return None
    memo = (clave, comp.get("nombre"))
    if memo in _cache:
        return _cache[memo]
    palabras = set(clave.split())
    filial = bool(FILIAL.search(clave))
    selecciones = bool(comp.get("selecciones"))
    exactos, contienen, casi = [], [], []
    for e in indice:
        if e["seleccion"] != selecciones:
            continue
        if clave in e["_claves"]:
            exactos.append(e)
        elif filial:
            continue
        # Contenido palabra por palabra; si el nombre de la biblioteca es el corto, como mucho una palabra menos
        # ("Brighton" vale por "Brighton Hove"; "Tenerife" no vale por "Costa Adeje Tenerife").
        elif any(palabras <= set(k.split()) or (set(k.split()) <= palabras and len(palabras) - len(k.split()) <= 1)
                 for k in e["_claves"]):
            contienen.append(e)
        elif any(difflib.SequenceMatcher(None, clave, k).quick_ratio() >= 0.88
                 and difflib.SequenceMatcher(None, clave, k).ratio() >= 0.88 for k in e["_claves"]):
            casi.append(e)
    # Un nombre solo parecido únicamente vale si el club juega esa competición (según la biblioteca).
    casi = [e for e in casi if set(e["competiciones"]) & set(comp.get("escudos_de", []))]
    elegido = None
    for grupo in (exactos, contienen, casi):
        if grupo:
            elegido = _desempatar(grupo, comp, palabras)
            break
    _cache[memo] = elegido and elegido["id"]
    return _cache[memo]


def _desempatar(candidatos, comp, palabras):
    # En una liga nacional (o una copa española), solo clubes de ese país: "Brest" no es el Dinamo Brest bielorruso.
    if comp.get("pais") and not comp.get("selecciones"):
        candidatos = [e for e in candidatos if e["pais"] == comp["pais"]]
    if len(candidatos) > 1:
        quedan = [e for e in candidatos if set(e["competiciones"]) & set(comp.get("escudos_de", []))]
        if quedan:
            candidatos = quedan
    if len(candidatos) > 1:
        # El nombre más directo: el que menos palabras añade ("Benfica" → "SL Benfica", no "Abrantes e Benfica").
        sobra = {e["id"]: min(len(set(k.split()) - palabras) for k in e["_claves"]) for e in candidatos}
        menos = min(sobra.values())
        candidatos = [e for e in candidatos if sobra[e["id"]] == menos]
    return candidatos[0] if len(candidatos) == 1 else None


# Fases de las competiciones europeas (football-data) como se dicen en la tele.
FASES = {
    "PLAYOFFS": "Play-off", "LAST_32": "Dieciseisavos", "LAST_16": "Octavos de final",
    "QUARTER_FINALS": "Cuartos de final", "SEMI_FINALS": "Semifinales", "FINAL": "Final",
}


def ronda_fd(m):
    if m.get("stage") in ("REGULAR_SEASON", "LEAGUE_STAGE", "GROUP_STAGE") and m.get("matchday"):
        return f"Jornada {m['matchday']}"
    return FASES.get(m.get("stage"), "")


# ---- ESPN: estado y marcador ----------------------------------------------------------------------------
# Marcador público de ESPN (no oficial, sin clave): va al día, cosa que football-data gratis no hace. Aquí se usa
# para enlazar cada partido de estas horas con el suyo de ESPN ("espn": liga e id; la tele pregunta por él mientras
# se juega) y para poner estado y resultado a los que solo salen en la guía (Europa League, Hypermotion...).
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/scoreboard?dates={fecha}"
ESPN_HORAS = (3, 36)  # partidos empezados hace como mucho 3 h, o que empiezan en las próximas 36 h
ESTADOS_ESPN = {"pre": "PROGRAMADO", "in": "EN_JUEGO", "post": "FINALIZADO"}
ESTADOS_ESPN_ESPECIALES = {
    "STATUS_HALFTIME": "DESCANSO", "STATUS_POSTPONED": "APLAZADO", "STATUS_CANCELED": "CANCELADO",
    "STATUS_SUSPENDED": "SUSPENDIDO", "STATUS_ABANDONED": "SUSPENDIDO", "STATUS_DELAYED": "PROGRAMADO",
}


def eventos_espn(peticiones):
    """Partidos de ESPN para cada (liga, fecha AAAAMMDD): equipos, hora, estado y marcador."""
    eventos = []
    for liga, fecha in sorted(peticiones):
        try:
            with descargar(ESPN_URL.format(liga=liga, fecha=fecha), timeout=20) as r:
                datos = json.load(r)
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            print(f"Aviso: ESPN {liga} {fecha} no responde ({e})", file=sys.stderr)
            continue
        for ev in datos.get("events", []):
            try:
                lados = {c["homeAway"]: c for c in ev["competitions"][0]["competitors"]}
                tipo = ev["status"]["type"]
                estado = ESTADOS_ESPN_ESPECIALES.get(tipo.get("name")) or ESTADOS_ESPN.get(tipo.get("state"))
                marcador = None
                if tipo.get("state") in ("in", "post"):
                    marcador = [int(lados["home"]["score"]), int(lados["away"]["score"])]
                eventos.append({
                    "liga": liga, "id": str(ev["id"]), "estado": estado, "marcador": marcador,
                    "inicio": dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
                    "local": [lados["home"]["team"].get(k) for k in ("displayName", "shortDisplayName", "name")],
                    "visitante": [lados["away"]["team"].get(k) for k in ("displayName", "shortDisplayName", "name")],
                })
            except (KeyError, IndexError, ValueError, TypeError):
                continue  # un partido con datos incompletos no estropea los demás
    return eventos


def enlazar_espn(partidos, competiciones, t):
    """Enlaza los partidos de estas horas con ESPN y les pone su estado y marcador (si ESPN ya sabe algo)."""
    por_nombre = {c["nombre"]: c for c in competiciones}
    cerca = []
    peticiones = set()
    for p in partidos:
        ligas = por_nombre.get(p["competicion"], {}).get("espn", [])
        inicio = dt.datetime.fromisoformat(p["inicio"].replace("Z", "+00:00"))
        if p.get("multi") or not ligas or not (
                t - dt.timedelta(hours=ESPN_HORAS[0]) <= inicio <= t + dt.timedelta(hours=ESPN_HORAS[1])):
            continue
        cerca.append((p, ligas, inicio))
        # ESPN agrupa por fecha de su zona (EE. UU.): un partido de madrugada en España cae el día anterior.
        for f in {inicio.date(), (inicio - dt.timedelta(hours=6)).date()}:
            peticiones |= {(liga, f.strftime("%Y%m%d")) for liga in ligas}
    if not cerca:
        return 0
    eventos = eventos_espn(peticiones)
    enlazados = 0
    for p, ligas, inicio in cerca:
        mejor, nota = None, 0.0
        for e in eventos:
            if e["liga"] not in ligas or abs((e["inicio"] - inicio).total_seconds()) > 40 * 60:
                continue
            n_l = max(parecido(p["local"]["nombre"], x) for x in e["local"] if x)
            n_v = max(parecido(p["visitante"]["nombre"], x) for x in e["visitante"] if x)
            n = (n_l + n_v) / 2 if min(n_l, n_v) >= 0.3 else 0.0
            if n > nota:
                mejor, nota = e, n
        if mejor is None or nota < 0.6:
            continue
        p["espn"] = {"liga": mejor["liga"], "id": mejor["id"]}
        if mejor["estado"] and mejor["estado"] != "PROGRAMADO":
            p["estado"] = mejor["estado"]
        if mejor["marcador"] is not None:
            p["marcador"] = mejor["marcador"]
        enlazados += 1
    return enlazados


def juntar(emisiones, fd_partidos, equipos, competiciones, desde, hasta, limites, indice_escudos=()):
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
    for e in [e for e in emisiones if "multi" not in e]:
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
                # En las competiciones españolas (Hypermotion, Copa...) solo valen escudos de LaLiga: "Sporting" es el de
                # Gijón, no el de Portugal.
                ligas = ["PD"] if comp.get("espanola") else None
                eq_l, eq_v = buscar_equipo(e["local"], equipos, ligas), buscar_equipo(e["visitante"], equipos, ligas)
                partidos[k] = {
                    "id": "g" + re.sub(r"[^a-z0-9]+", "-", k),
                    "competicion": e["competicion"], "ronda": e["ronda"], "inicio": iso(saque(inicio)),
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

    # 3) Los multi: uno por cada competición que nombran ("Multieuropa League + Conference" va en las dos) y solo si a
    # esa hora hay partidos suyos; así no cuela una reposición (el de las 21:00 a veces no lleva "DIRECTO").
    def hora(p):
        return dt.datetime.fromisoformat(p["inicio"].replace("Z", "+00:00"))
    for e in [e for e in emisiones if "multi" in e]:
        inicio = saque(dt.datetime.fromisoformat(e["inicio"].replace("Z", "+00:00")))
        if not (desde - dt.timedelta(hours=1) <= inicio <= hasta):
            continue
        nombre = e["multi"].lower()
        for comp in [c["nombre"] for c in competiciones if any(k in nombre for k in c.get("multi", []))] or [e["competicion"]]:
            if not any(p["competicion"] == comp and "multi" not in p and abs(hora(p) - inicio) <= dt.timedelta(minutes=20)
                       for p in partidos.values()):
                continue
            k = f"multi|{comp}|{iso(inicio)}"
            if k not in partidos:
                partidos[k] = {
                    "id": re.sub(r"[^a-z0-9]+", "-", sin_acentos(k).lower()),
                    "competicion": comp, "ronda": e["ronda"], "inicio": iso(inicio), "estado": None, "marcador": None,
                    "multi": e["multi"],
                    # Las versiones anteriores de la tele esperan dos equipos.
                    "local": {"nombre": e["multi"], "escudo": None}, "visitante": {"nombre": "", "escudo": None},
                    "canales": [],
                }
            if e["canal"] not in partidos[k]["canales"]:
                partidos[k]["canales"].append(e["canal"])

    resultado = []
    for p in partidos.values():
        nombres = p.pop("_nombres", None) or ()
        p["prioridad"] = por_nombre.get(p["competicion"], {}).get("rango", 99)
        p["destacado"] = any(clave_equipo(n) in GRANDES for n in nombres if n) or \
            any(clave_equipo(x["nombre"]) in GRANDES for x in (p["local"], p["visitante"]))
        # Con algún equipo español (de LaLiga): va antes que los demás de su competición.
        p["espanol"] = p["destacado"] or any(es_espanol(n, equipos) for n in nombres[::2] if n)
        # El escudo de nuestra biblioteca (la tele lo lleva dentro): se prueba con cada nombre que se tiene del
        # equipo (football-data y guía) y el que se ve en la tele.
        if not p.get("multi"):
            comp = por_nombre.get(p["competicion"], {})
            for i, lado in enumerate(("local", "visitante")):
                nombres_lado = [n for n in nombres[i * 2:i * 2 + 2] if n] + [p[lado]["nombre"]]
                p[lado]["escudo_id"] = next(
                    (x for x in (escudo_de(n, comp, indice_escudos) for n in nombres_lado) if x), None)
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
    ap.add_argument("--escudos", default="escudos-indice.json")
    ap.add_argument("--equivalencias", default="escudos-equivalencias.json")
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

    indice = cargar_indice_escudos(args.escudos)
    EQUIVALENCIAS.update(cargar_equivalencias(args.equivalencias))
    partidos = juntar(emisiones, fd_partidos, equipos, competiciones, desde, hasta, limites, indice)
    print(f"ESPN: {enlazar_espn(partidos, competiciones, t)} partidos enlazados")
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
