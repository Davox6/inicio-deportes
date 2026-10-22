"""Índice de escudos para el generador: solo nombres e identificadores (sin imágenes).

Sale de la biblioteca local de escudos (FootyLogos, en tv-panasonic/assets logos/biblioteca-futbol) y se guarda en
escudos-indice.json. Con él, generar.py elige el escudo de cada equipo por su nombre; la tele lleva las imágenes
dentro de la app y las busca por ese identificador (p. ej. "club:celtic" → escudos/club_celtic.webp).

Si existe el paquete de la app (escudos-app/escudos.json, con alias de la guía de TV y de football-data), sus alias
se añaden a los de la biblioteca.

    python herramientas/indice_escudos.py "<ruta a biblioteca-futbol>" [<ruta a escudos-app>]
"""
import json
import os
import sqlite3
import sys


def main():
    biblioteca = sys.argv[1]
    paquete = sys.argv[2] if len(sys.argv) > 2 else None
    db = sqlite3.connect(os.path.join(biblioteca, "catalogo.sqlite"))
    comps = {}
    for eid, slug in db.execute("select entity_id, competition_slug from entity_competitions"):
        comps.setdefault(eid, []).append(slug)
    equipos = {}
    for eid, name, typ, cc, aliases in db.execute(
        "select id, name, type, country_code, aliases_json from entities where type in ('club', 'national-team')"
    ):
        equipos[eid] = {
            "id": eid,
            "nombre": name,
            "alias": json.loads(aliases or "[]"),
            "pais": cc or "",
            "seleccion": typ == "national-team",
            "competiciones": sorted(comps.get(eid, [])),
        }
    extra = 0
    if paquete and os.path.exists(os.path.join(paquete, "escudos.json")):
        with open(os.path.join(paquete, "escudos.json"), encoding="utf-8") as f:
            datos = json.load(f)
        for e in datos if isinstance(datos, list) else datos.get("escudos", []):
            if e.get("id") in equipos:
                nuevos = [a for a in e.get("alias", []) if a not in equipos[e["id"]]["alias"]]
                equipos[e["id"]]["alias"] += nuevos
                extra += len(nuevos)
    salida = {
        "_comentario": "Equipos de la biblioteca de escudos (FootyLogos, 22/10/2026): id, nombre, alias, país y "
                       "competiciones. Lo genera herramientas/indice_escudos.py; no contiene imágenes.",
        "equipos": sorted(equipos.values(), key=lambda e: e["id"]),
    }
    with open("escudos-indice.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
    print(f"escudos-indice.json: {len(equipos)} equipos ({extra} alias del paquete de la app)")


if __name__ == "__main__":
    main()
