#!/usr/bin/env python3
"""
SoloQ Challenge - Script de Sincronización Automática con la API de Riot Games
=============================================================================
Este script toma la lista de los 10 participantes de LAS, consulta la API oficial
de Riot Games y actualiza automáticamente los rangos, divisiones, LP, victorias,
derrotas e iconos de cada jugador.

Uso:
    python update_ranks.py --api-key RGAPI-TU-CLAVE-AQUI
    python update_ranks.py --mock   (Modo de prueba sin necesidad de API key)
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).parent
PLAYERS_FILE = BASE_DIR / "players.json"
INDEX_FILE = BASE_DIR / "index.html"

# Mapeo de servidores y regiones de Riot
REGION_ROUTING = {
    "las": "la2.api.riotgames.com",
    "lan": "la1.api.riotgames.com",
    "br": "br1.api.riotgames.com",
    "na": "na1.api.riotgames.com",
    "euw": "euw1.api.riotgames.com"
}
ACCOUNT_ROUTING = {
    "las": "americas.api.riotgames.com",
    "lan": "americas.api.riotgames.com",
    "br": "americas.api.riotgames.com",
    "na": "americas.api.riotgames.com",
    "euw": "europe.api.riotgames.com"
}

def fetch_json(url, api_key=None):
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("X-Riot-Token", api_key)
    req.add_header("User-Agent", "SoloQChallenge-SyncBot/1.0")
    
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def get_champion_name_map():
    """Descarga el diccionario de campeones de Data Dragon para convertir championId a Nombre"""
    try:
        try:
            version = fetch_json("https://ddragon.leagueoflegends.com/api/versions.json")[0]
        except Exception:
            version = "15.19.1"
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/es_ES/champion.json"
        data = fetch_json(url)
        champ_map = {}
        for champ_id_str, champ_info in data.get("data", {}).items():
            champ_map[int(champ_info["key"])] = champ_info["id"]
        return champ_map
    except Exception as e:
        print(f"[!] Aviso: No se pudo obtener Data Dragon de campeones ({e}).")
        return {}

RUN_ID = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def update_player_via_riot_api(player, api_key, champ_map):
    name = player["name"]
    tag = player["tag"]
    region = player.get("region", "las").lower()

    account_host = ACCOUNT_ROUTING.get(region, "americas.api.riotgames.com")
    platform_host = REGION_ROUTING.get(region, "la2.api.riotgames.com")

    print(f"[*] Consultando a Riot API: {name}#{tag} ({region.upper()})...")

    # 1. Obtener PUUID vía Account-V1
    encoded_name = urllib.parse.quote(name)
    encoded_tag = urllib.parse.quote(tag)
    account_url = f"https://{account_host}/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
    account_data = fetch_json(account_url, api_key)
    puuid = account_data["puuid"]

    time.sleep(0.1) # Evitar saturar el rate limit (20 req / 1 sec)

    # 2. Obtener Summoner ID e Icono vía Summoner-V4
    summoner_url = f"https://{platform_host}/lol/summoner/v4/summoners/by-puuid/{puuid}"
    summoner_data = fetch_json(summoner_url, api_key)
    player["profileIconId"] = summoner_data.get("profileIconId", 1)

    time.sleep(0.1)

    # 3. Obtener Liga y Rango en SoloQ vía League-V4
    league_url = f"https://{platform_host}/lol/league/v4/entries/by-puuid/{puuid}"
    league_entries = fetch_json(league_url, api_key)
    
    soloq_entry = None
    for entry in league_entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            soloq_entry = entry
            break

    if soloq_entry:
        player["tier"] = soloq_entry.get("tier", "UNRANKED")
        player["division"] = soloq_entry.get("rank", "I")
        player["lp"] = soloq_entry.get("leaguePoints", 0)
        player["wins"] = soloq_entry.get("wins", 0)
        player["losses"] = soloq_entry.get("losses", 0)
        # Historial para la grafica de Elo
        import datetime
        base = {"IRON":0,"BRONZE":400,"SILVER":800,"GOLD":1200,"PLATINUM":1600,"EMERALD":2000,"DIAMOND":2400,"MASTER":2800,"GRANDMASTER":2800,"CHALLENGER":2800}
        off = {"IV":0,"III":100,"II":200,"I":300}
        apex = player["tier"] in ("MASTER","GRANDMASTER","CHALLENGER")
        elo = base.get(player["tier"],0) + (0 if apex else off.get(player["division"],0)) + player["lp"]
        hist = player.setdefault("history", [])
        if not hist or hist[-1].get("elo") != elo or hist[-1].get("wins") != player["wins"] or hist[-1].get("losses") != player["losses"]:
            hist.append({"run": RUN_ID, "tier": player["tier"], "division": player["division"], "lp": player["lp"], "elo": elo, "wins": player["wins"], "losses": player["losses"]})
        print(f"    -> Rango: {player['tier']} {player['division']} ({player['lp']} LP) | {player['wins']}W / {player['losses']}L")
    else:
        print(f"    -> Sin partidas de SoloQ clasificatorias registradas esta temporada.")

    time.sleep(0.1)

    # 4. Obtener mejores 3 campeones
    try:
        mastery_url = f"https://{platform_host}/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count=3"
        masteries = fetch_json(mastery_url, api_key)
        top_champs = []
        for m in masteries:
            c_id = m.get("championId")
            if c_id in champ_map:
                top_champs.append(champ_map[c_id])
        if top_champs:
            player["champions"] = top_champs
    except Exception as e:
        print(f"    -> Nota: No se pudieron leer maestrías: {e}")

    return player

def sync_with_html():
    """Actualiza la constante DEFAULT_PLAYERS en index.html con el contenido de players.json"""
    if not PLAYERS_FILE.exists() or not INDEX_FILE.exists():
        return
    with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
        players_data = json.load(f)
    
    json_str = json.dumps(players_data, indent=6, ensure_ascii=False)

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Reemplazo de DEFAULT_PLAYERS = [...]
    start_marker = "const DEFAULT_PLAYERS = ["
    end_marker = "];"
    start_pos = html_content.find(start_marker)
    if start_pos != -1:
        end_pos = html_content.find(end_marker, start_pos)
        if end_pos != -1:
            new_block = f"const DEFAULT_PLAYERS = {json_str};"
            new_html = html_content[:start_pos] + new_block + html_content[end_pos + len(end_marker):]
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                f.write(new_html)
            print("[✓] index.html sincronizado con los nuevos datos.")

def main():
    parser = argparse.ArgumentParser(description="Actualizar rangos de SoloQ Challenge vía Riot API")
    parser.add_argument("--api-key", help="Tu Riot Games API Key (de developer.riotgames.com)")
    parser.add_argument("--no-html", action="store_true", help="No reescribir index.html (la web lee players.json)")
    parser.add_argument("--mock", action="store_true", help="Simular actualización sin API key")
    args = parser.parse_args()

    if not PLAYERS_FILE.exists():
        print(f"[X] Error: No se encontró el archivo {PLAYERS_FILE}")
        sys.exit(1)

    with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
        players = json.load(f)

    if args.mock:
        print("[*] Modo Simulación (Mock): Actualizando jugadores de prueba...")
        for p in players:
            p["wins"] += 1
            p["lp"] = min(100, p["lp"] + 18)
        with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
            json.dump(players, f, indent=2, ensure_ascii=False)
        sync_with_html()
        print("[✓] Actualización mock completada.")
        return

    api_key = args.api_key or os.environ.get("RIOT_API_KEY", "").strip()
    if not api_key:
        print("[!] Por favor proporciona tu Riot API Key con: python update_ranks.py --api-key RGAPI-XXXX")
        print("    O usa: python update_ranks.py --mock para probar la sincronización.")
        sys.exit(1)

    champ_map = get_champion_name_map()
    updated_players = []
    failures = 0

    for player in players:
        try:
            p_updated = update_player_via_riot_api(player, api_key, champ_map)
            updated_players.append(p_updated)
        except urllib.error.HTTPError as e:
            print(f"[!] Error HTTP {e.code} al consultar a {player['name']}: {e.reason}")
            failures += 1
            updated_players.append(player)
        except Exception as e:
            print(f"[!] Error con {player['name']}: {e}")
            failures += 1
            updated_players.append(player)

    if failures == len(players):
        print("[X] Fallaron todos los jugadores (¿API key vencida o inválida?). No se guardó nada.")
        sys.exit(1)

    with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_players, f, indent=2, ensure_ascii=False)

    if not args.no_html:
        sync_with_html()
    print("\n[✓] ¡Todos los jugadores han sido procesados y guardados con éxito!")

if __name__ == "__main__":
    main()
