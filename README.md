# inicio-deportes

Datos de partidos de fútbol para **Inicio**, el launcher casero de una tele Android con deco de Orange TV:
qué partidos hay en directo y en los próximos días, en qué canal de la tele española se emiten y los escudos
de los equipos. La tele descarga un único JSON pequeño; todo el trabajo pesado se hace aquí.

## Cómo funciona

Una tarea de GitHub Actions (`.github/workflows/actualizar.yml`) ejecuta `generar.py` cada 15 minutos y publica
el resultado en la rama **`datos`**:

```
https://raw.githubusercontent.com/Davox6/inicio-deportes/datos/deportes.json
```

- **Canales y horas:** guía de TV pública [EPG dobleM](https://github.com/davidmuma/EPG_dobleM). Los partidos en
  directo aparecen como `DIRECTO Jornada 10: Barcelona - Real Madrid T26/27 · LALIGA EA SPORTS`. La guía solo
  se descarga cuando cambia (unas 4 veces al día).
- **Estado, marcador y escudos:** [football-data.org](https://www.football-data.org), plan gratuito
  (LaLiga y Champions). La clave va en el secreto `FOOTBALL_DATA_KEY` del repositorio, nunca en el código.
- **Competiciones:** `competiciones.json` (activar, desactivar o filtrar solo partidos de equipos españoles).

La tele traduce cada canal de la guía al dial del deco con la base de datos de canales del propio deco.

## Formato de `deportes.json`

```json
{
  "version": 1,
  "generado": "2026-10-22T10:30:00Z",
  "guia": "dobleM 22/10/2026 08:47",
  "partidos": [
    {
      "id": "fd123456",
      "competicion": "LaLiga",
      "ronda": "Jornada 10",
      "inicio": "2026-10-25T20:00:00Z",
      "estado": "PROGRAMADO",
      "marcador": null,
      "local": { "nombre": "Barcelona", "escudo": "https://crests.football-data.org/81.png" },
      "visitante": { "nombre": "Real Madrid", "escudo": "https://crests.football-data.org/86.png" },
      "canales": ["DAZN LaLiga HD"]
    }
  ]
}
```

- `estado`: `PROGRAMADO`, `EN_JUEGO`, `DESCANSO`, `FINALIZADO`, `APLAZADO`, `SUSPENDIDO`, `CANCELADO`, o `null`
  si el partido solo sale en la guía (la tele lo deduce de la hora).
- `canales`: nombres de canal tal como vienen en la guía.
- Los escudos son marcas de cada club: aquí solo se enlazan, no se guardan ni se redistribuyen.

## `ahora.json`: lo que se emite en tus canales

Para la pantalla de Inicio (el escaparate de la TDT y "Ahora en tus canales"): la programación de las próximas
~16 horas de los canales de la TDT de Madrid (La 1, La 2, Antena 3, Cuatro, Telecinco, laSexta, Telemadrid, La Otra,
Clan…) y de los canales de fútbol (`CANALES_AHORA` en `generar.py`). La tele elige el programa actual y el siguiente
con su propia hora.

```
https://raw.githubusercontent.com/Davox6/inicio-deportes/datos/ahora.json
```

```json
{ "version": 1, "guia": "dobleM …", "canales": [
  { "id": "Antena 3 HD", "nombre": "Antena 3", "programas": [
    { "titulo": "La Voz", "inicio": "2026-10-23T20:10:00Z", "fin": "2026-10-23T23:00:00Z",
      "episodio": "Audiciones a ciegas (VI)", "genero": "Entretenimiento · Concurso", "anio": 2026, "edad": "TP",
      "descripcion": "«La Voz» continúa con la sexta ronda…",
      "foto": "https://www.movistarplus.es/recorte/n/ficha/MA3P4426681" } ] } ] }
```

- Solo `titulo`, `inicio` y `fin` van siempre; el resto, si la guía lo trae.
- `descripcion`: sin los géneros del principio ni la ficha del final, y como mucho 220 caracteres.
- `foto`: la imagen horizontal (1920×1080) de la ficha de Movistar+ que enlaza la guía. Solo se enlaza.

## Probar en local

```
python generar.py --salida salida
```

Sin `FOOTBALL_DATA_KEY` funciona solo con la guía: sin escudos ni marcador, y solo con los partidos marcados
como DIRECTO.
