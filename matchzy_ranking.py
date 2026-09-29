#!/usr/bin/env python3
"""
matchzy_ranking.py — Gera um dashboard HTML de ranking (leaderboard) a partir
do banco de dados do MatchZy, somando as estatisticas de todas as partidas
finalizadas no servidor em que os dois times estavam completos (padrão 5x5).
Espectadores são ignorados.

Uso:
    python3 matchzy_ranking.py                      # usa ./matchzy.db e gera ./index.html
    python3 matchzy_ranking.py --db /caminho/para/matchzy.db --out index.html

Pra consolidar vários bancos (ex: o servidor foi resetado e o banco antigo
ficou salvo), passe todos no --db:
    python3 matchzy_ranking.py --db antigo.db novo.db

Se você não passar --db, o script tenta achar o arquivo sozinho a partir do
diretório onde é executado (ver DEFAULT_DB_CANDIDATES abaixo).

O banco padrão do MatchZy fica em:
    csgo/addons/counterstrikesharp/plugins/MatchZy/matchzy.db

Rode este script sempre que quiser atualizar o ranking (por exemplo, depois
de cada sessão de mix) — ele lê o banco de novo e regenera o HTML do zero.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_CANDIDATES = [
    "matchzy.db",
    "csgo/addons/counterstrikesharp/plugins/MatchZy/matchzy.db",
    "game/csgo/addons/counterstrikesharp/plugins/MatchZy/matchzy.db",
]


def find_dbs(explicit_paths: list[str] | None) -> list[Path]:
    if explicit_paths:
        paths = []
        for raw in explicit_paths:
            p = Path(raw).expanduser()
            if not p.exists():
                sys.exit(f"Banco não encontrado em: {p}")
            paths.append(p)
        return paths
    for candidate in DEFAULT_DB_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return [p]
    sys.exit(
        "Não encontrei o matchzy.db automaticamente.\n"
        "Rode de novo com --db apontando pro arquivo, por exemplo:\n"
        "  python3 matchzy_ranking.py --db "
        "/var/lib/pelican/volumes/<uuid>/game/csgo/addons/counterstrikesharp/plugins/MatchZy/matchzy.db"
    )


# mapas finalizados (com vencedor e horário de fim) em que os dois times
# tinham pelo menos `team_size` jogadores (sem contar espectadores)
COMPLETE_MAPS_SQL = """
    SELECT {cols}
    FROM matchzy_stats_maps m
    WHERE m.end_time IS NOT NULL AND m.end_time != ''
      AND m.winner IS NOT NULL AND m.winner != ''
      AND (
        SELECT COUNT(*) FROM (
          SELECT p.team FROM matchzy_stats_players p
          WHERE p.matchid = m.matchid AND p.mapnumber = m.mapnumber
            AND p.team != 'Spectator'
          GROUP BY p.team
          HAVING COUNT(*) >= ?
        )
      ) = 2
"""


def load_maps(db_paths: list[Path], team_size: int) -> list[dict]:
    """Lê os mapas completos de todos os bancos, já com os jogadores de cada um.

    Os matchid recomeçam em 1 em cada banco, então cada mapa ganha uma chave
    (índice do banco, matchid, mapnumber). Se o mesmo mapa aparecer em mais de
    um banco (ex: um backup mais novo que contém o antigo), só conta uma vez.
    """
    maps: list[dict] = []
    seen: set[tuple] = set()
    for i, db_path in enumerate(db_paths):
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(COMPLETE_MAPS_SQL.format(cols="m.*"), (team_size,))
        for m in cur.fetchall():
            fingerprint = (m["start_time"], m["mapname"], m["mapnumber"], m["team1_score"], m["team2_score"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            cur2 = con.cursor()
            cur2.execute(
                "SELECT team1_name, team2_name FROM matchzy_stats_matches WHERE matchid = ?",
                (m["matchid"],),
            )
            teams = cur2.fetchone()
            # espectadores não jogaram o mapa, então não contam
            cur2.execute(
                "SELECT * FROM matchzy_stats_players "
                "WHERE matchid = ? AND mapnumber = ? AND team != 'Spectator'",
                (m["matchid"], m["mapnumber"]),
            )
            maps.append(
                {
                    **dict(m),
                    "key": (i, m["matchid"], m["mapnumber"]),
                    "rounds": m["team1_score"] + m["team2_score"],
                    "team1_name": teams["team1_name"] if teams else "",
                    "team2_name": teams["team2_name"] if teams else "",
                    "players": [dict(r) for r in cur2.fetchall()],
                }
            )
        con.close()
    return maps


def fetch_players(maps: list[dict], min_rounds: int, confidence: int = 100) -> list[dict]:
    rows = [(m, r) for m in maps for r in m["players"]]

    agg: dict[int, dict] = {}
    for m, r in rows:
        steamid = r["steamid64"]
        p = agg.setdefault(
            steamid,
            {
                "steamid64": steamid,
                "name": r["name"],
                "last_seen": "",
                "matches": set(),
                "maps": set(),
                "rounds": 0,
                "kills": 0,
                "deaths": 0,
                "assists": 0,
                "damage": 0,
                "hs_kills": 0,
                "k2": 0,
                "k3": 0,
                "k4": 0,
                "k5": 0,
                "v1_count": 0,
                "v1_wins": 0,
                "v2_count": 0,
                "v2_wins": 0,
                "wins": 0,
                "history": [],
            },
        )
        p["matches"].add(m["key"][:2])
        p["maps"].add(m["key"])
        p["rounds"] += m["rounds"]
        won = r["team"] == m["winner"]
        if won:
            p["wins"] += 1
        on_team1 = r["team"] == m["team1_name"]
        p["history"].append(
            {
                "mid": map_id(m),
                "slug": map_slug(m["mapname"]),
                "date": (m["start_time"] or "")[:16],
                "map": map_label(m["mapname"]),
                "won": won,
                "score": m["team1_score"] if on_team1 else m["team2_score"],
                "opp": m["team2_score"] if on_team1 else m["team1_score"],
                "kills": r["kills"],
                "deaths": r["deaths"],
                "assists": r["assists"],
                "adr": round(r["damage"] / max(m["rounds"], 1), 1),
                "hs_pct": round(r["head_shot_kills"] / r["kills"] * 100) if r["kills"] else 0,
            }
        )
        p["kills"] += r["kills"]
        p["deaths"] += r["deaths"]
        p["assists"] += r["assists"]
        p["damage"] += r["damage"]
        p["hs_kills"] += r["head_shot_kills"]
        p["k2"] += r["enemy2ks"]
        p["k3"] += r["enemy3ks"]
        p["k4"] += r["enemy4ks"]
        p["k5"] += r["enemy5ks"]
        p["v1_count"] += r["v1_count"]
        p["v1_wins"] += r["v1_wins"]
        p["v2_count"] += r["v2_count"]
        p["v2_wins"] += r["v2_wins"]
        # usa o nome da partida mais recente (nomes de Steam mudam)
        # (compara pela data, já que o matchid recomeça em cada banco)
        seen_at = m["start_time"] or ""
        if seen_at >= p["last_seen"]:
            p["last_seen"] = seen_at
            p["name"] = r["name"]

    players = []
    for p in agg.values():
        rounds = p["rounds"]

        kills, deaths, damage = p["kills"], p["deaths"], p["damage"]
        kd = kills / max(deaths, 1)
        kpr = kills / max(rounds, 1)
        adr = damage / max(rounds, 1)
        hs_pct = (p["hs_kills"] / kills * 100) if kills else 0.0
        clutch_att = p["v1_count"] + p["v2_count"]
        clutch_won = p["v1_wins"] + p["v2_wins"]
        clutch_pct = (clutch_won / clutch_att * 100) if clutch_att else 0.0
        multi_kills = p["k2"] + p["k3"] + p["k4"] + p["k5"]

        # "MixScore": heurística simples e transparente pra ordenar o ranking
        # por padrão. Ajuste os pesos abaixo se quiser priorizar outra coisa
        # (ex: dar mais peso a clutches, ou tirar o ADR da conta).
        rating = round(
            kpr * 40          # ritmo de kill por round
            + (adr / 100) * 25  # dano médio por round
            + hs_pct * 0.05     # bônus leve por precisão
            + clutch_pct * 0.10  # bônus leve por clutch
            + kd * 5,            # K/D geral
            2,
        )

        players.append(
            {
                "name": p["name"] or "(desconhecido)",
                "steamid64": str(p["steamid64"]),
                "matches": len(p["matches"]),
                "maps": len(p["maps"]),
                "rounds": rounds,
                "kills": kills,
                "deaths": deaths,
                "assists": p["assists"],
                "damage": damage,
                "kd": round(kd, 2),
                "kpr": round(kpr, 2),
                "adr": round(adr, 1),
                "hs_pct": round(hs_pct, 1),
                "multi_kills": multi_kills,
                "clutch_won": clutch_won,
                "clutch_att": clutch_att,
                "clutch_pct": round(clutch_pct, 1),
                "wins": p["wins"],
                "losses": len(p["maps"]) - p["wins"],
                "win_pct": round(p["wins"] / len(p["maps"]) * 100, 1),
                "k3": p["k3"],
                "k4": p["k4"],
                "aces": p["k5"],
                "history": sorted(p["history"], key=lambda h: h["date"], reverse=True),
                "raw_rating": rating,
            }
        )

    # MixScore final com "peso de confiança": quem jogou pouco fica puxado pra
    # média do mix e vai ganhando o próprio valor conforme acumula rounds.
    #   final = (rounds * bruto + confidence * média) / (rounds + confidence)
    # Com confidence=100, um jogador com 100 rounds (~5 mapas) fica no meio do
    # caminho entre a própria nota e a média. confidence=0 desliga o ajuste.
    total_rounds = sum(p["rounds"] for p in players) or 1
    mean = sum(p["raw_rating"] * p["rounds"] for p in players) / total_rounds
    for p in players:
        p["rating"] = round((p["rounds"] * p["raw_rating"] + confidence * mean) / (p["rounds"] + confidence), 2)
    players = [p for p in players if p["rounds"] >= min_rounds]

    # ranking por vitórias; desempate por aproveitamento e depois pelo MixScore
    players.sort(key=lambda x: (x["wins"], x["win_pct"], x["rating"]), reverse=True)
    for i, p in enumerate(players, start=1):
        p["rank"] = i
    return players


def team_label(raw: str) -> str:
    # o MatchZy nomeia os times como "team_<capitão>" com espaços trocados por "_"
    name = raw[5:] if raw.startswith("team_") else raw
    return " ".join(name.replace("_", " ").split()) or raw


def map_slug(raw: str) -> str:
    # "de_dust2" -> "dust2" (nome do arquivo em assets/maps/)
    return (raw[3:] if raw.startswith(("de_", "cs_")) else raw).lower()


def map_id(m: dict) -> str:
    return "-".join(str(x) for x in m["key"])


def map_label(raw: str) -> str:
    name = raw[3:] if raw.startswith(("de_", "cs_")) else raw
    return name.replace("_", " ").title()


def fetch_maps(maps: list[dict], current_names: dict[str, str]) -> list[dict]:
    result = []
    for m in maps:
        rounds = max(m["rounds"], 1)
        # MVP do mapa = mais kills (desempate por dano)
        mvp = max(m["players"], key=lambda r: (r["kills"], r["damage"]), default=None)
        board = {"team1": [], "team2": []}
        for r in m["players"]:
            side = "team1" if r["team"] == m["team1_name"] else "team2"
            sid = str(r["steamid64"])
            board[side].append(
                {
                    "sid": sid,
                    "name": current_names.get(sid, r["name"]),
                    "k": r["kills"],
                    "d": r["deaths"],
                    "a": r["assists"],
                    "adr": round(r["damage"] / rounds, 1),
                    "hs": round(r["head_shot_kills"] / r["kills"] * 100) if r["kills"] else 0,
                    "mk": r["enemy3ks"] + r["enemy4ks"] + r["enemy5ks"],
                }
            )
        for side in board.values():
            side.sort(key=lambda x: (x["k"], x["adr"]), reverse=True)
        result.append(
            {
                "id": map_id(m),
                "map": map_label(m["mapname"]),
                "slug": map_slug(m["mapname"]),
                "date": (m["start_time"] or "")[:16],
                "team1": team_label(m["team1_name"]) if m["team1_name"] else "Time 1",
                "team2": team_label(m["team2_name"]) if m["team2_name"] else "Time 2",
                "score1": m["team1_score"],
                "score2": m["team2_score"],
                "winner": team_label(m["winner"]),
                # nome mais recente do jogador, igual ao da tabela
                "mvp": current_names.get(str(mvp["steamid64"]), mvp["name"]) if mvp else None,
                "mvp_sid": str(mvp["steamid64"]) if mvp else None,
                "mvp_kills": mvp["kills"] if mvp else 0,
                "mvp_deaths": mvp["deaths"] if mvp else 0,
                "mvp_adr": round(mvp["damage"] / rounds, 1) if mvp else 0,
                "board": board,
            }
        )
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


def pair_stats(maps: list[dict], players: list[dict], min_games: int) -> tuple[list[dict], list[dict]]:
    """Duplas (mesmo time) e rivalidades (times opostos) entre os jogadores do ranking.

    Também preenche em cada jogador: melhor parceiro, freguês (quem ele mais venceu)
    e carrasco (quem mais venceu ele).
    """
    by_sid = {p["steamid64"]: p for p in players}
    duos: dict[tuple, dict] = {}
    rivals: dict[tuple, dict] = {}
    for m in maps:
        teams: dict[str, list[str]] = {}
        for r in m["players"]:
            sid = str(r["steamid64"])
            if sid in by_sid:
                teams.setdefault(r["team"], []).append(sid)
        for team, sids in teams.items():
            won = team == m["winner"]
            for i, a in enumerate(sids):
                for b in sids[i + 1 :]:
                    key = tuple(sorted((a, b)))
                    d = duos.setdefault(key, {"a": key[0], "b": key[1], "games": 0, "wins": 0})
                    d["games"] += 1
                    d["wins"] += won
        names = list(teams)
        if len(names) == 2:
            t1, t2 = names
            for a in teams[t1]:
                for b in teams[t2]:
                    key = tuple(sorted((a, b)))
                    r = rivals.setdefault(key, {"a": key[0], "b": key[1], "games": 0, "a_wins": 0, "b_wins": 0})
                    r["games"] += 1
                    winner_sid = a if m["winner"] == t1 else b
                    r["a_wins" if winner_sid == key[0] else "b_wins"] += 1

    def name(sid: str) -> str:
        return by_sid[sid]["name"]

    for p in players:
        sid = p["steamid64"]
        mine = [d for d in duos.values() if sid in (d["a"], d["b"]) and d["games"] >= 2]
        best = max(mine, key=lambda d: (d["wins"], d["wins"] / d["games"]), default=None)
        p["partner"] = (
            {"sid": best["b"] if best["a"] == sid else best["a"], "games": best["games"], "wins": best["wins"]}
            if best and best["wins"]
            else None
        )
        faced = [r for r in rivals.values() if sid in (r["a"], r["b"]) and r["games"] >= 2]

        def wins_over(r: dict) -> tuple[int, int]:
            mine_w = r["a_wins"] if r["a"] == sid else r["b_wins"]
            return mine_w, r["games"] - mine_w

        fregues = max(faced, key=lambda r: (wins_over(r)[0] - wins_over(r)[1], wins_over(r)[0]), default=None)
        carrasco = max(faced, key=lambda r: (wins_over(r)[1] - wins_over(r)[0], wins_over(r)[1]), default=None)
        p["fregues"] = None
        p["carrasco"] = None
        if fregues and wins_over(fregues)[0] > wins_over(fregues)[1]:
            w, l = wins_over(fregues)
            p["fregues"] = {"sid": fregues["b"] if fregues["a"] == sid else fregues["a"], "wins": w, "losses": l}
        if carrasco and wins_over(carrasco)[1] > wins_over(carrasco)[0]:
            w, l = wins_over(carrasco)
            p["carrasco"] = {"sid": carrasco["b"] if carrasco["a"] == sid else carrasco["a"], "wins": w, "losses": l}
        for k in ("partner", "fregues", "carrasco"):
            if p[k]:
                p[k]["name"] = name(p[k]["sid"])

    top_duos = sorted(
        (d for d in duos.values() if d["games"] >= min_games),
        key=lambda d: (d["wins"], d["wins"] / d["games"], d["games"]),
        reverse=True,
    )[:8]
    top_rivals = sorted(
        (r for r in rivals.values() if r["games"] >= min_games),
        key=lambda r: (r["games"], -abs(r["a_wins"] - r["b_wins"])),
        reverse=True,
    )[:8]
    for d in top_duos:
        d["a_name"], d["b_name"] = name(d["a"]), name(d["b"])
    for r in top_rivals:
        r["a_name"], r["b_name"] = name(r["a"]), name(r["b"])
    return top_duos, top_rivals


def split_nights(maps: list[dict], gap_hours: int = 4) -> list[list[dict]]:
    """Agrupa os mapas em noites de mix: um intervalo de mais de `gap_hours` separa uma noite da outra."""
    from datetime import datetime, timedelta

    parse = lambda m: datetime.strptime(m["start_time"][:19], "%Y-%m-%d %H:%M:%S")
    ordered = sorted((m for m in maps if m.get("start_time")), key=parse)
    nights: list[list[dict]] = []
    for m in ordered:
        if nights and parse(m) - parse(nights[-1][-1]) <= timedelta(hours=gap_hours):
            nights[-1].append(m)
        else:
            nights.append([m])
    return nights


def night_label(night: list[dict]) -> str:
    from datetime import datetime, timedelta

    # mapa começado de madrugada conta como a noite anterior
    first = datetime.strptime(night[0]["start_time"][:19], "%Y-%m-%d %H:%M:%S") - timedelta(hours=6)
    return first.strftime("%d/%m")


def build_scope(maps: list[dict], min_rounds: int, confidence: int, min_pair_games: int) -> dict:
    players = fetch_players(maps, min_rounds, confidence)
    names = {p["steamid64"]: p["name"] for p in players}
    duos, rivals = pair_stats(maps, players, min_pair_games)
    return {"players": players, "maps": fetch_maps(maps, names), "duos": duos, "rivals": rivals}


HTML_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ranking do Mix</title>
<link rel="icon" type="image/svg+xml" href="assets/favicon.svg" />
<link rel="apple-touch-icon" href="assets/apple-touch-icon.png" />
<meta name="theme-color" content="#0c0d10" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
<style>
  :root {
    color-scheme: dark;
    --bg:            #0c0d10;
    --surface:       #15171b;
    --surface-2:     #1c1f24;
    --surface-3:     #252930;
    --border:        rgba(255,255,255,0.07);
    --border-strong: rgba(255,255,255,0.14);
    --text:          #f4f4f5;
    --text-2:        #a1a1aa;
    --text-3:        #8a8a93;
    --grid:          #24272d;
    --baseline:      #3a3e46;
    --accent:        #f5a524;   /* texto/brilho */
    --accent-mark:   #c98500;   /* barras e pontos (validado p/ superfície escura) */
    --accent-wash:   rgba(245,165,36,0.10);
    --accent-glow:   rgba(245,165,36,0.22);
    --good:          #3ecf6a;
    --bad:           #ff7a7a;
    --gold:          #f5c451;
    --silver:        #c9ced6;
    --bronze:        #d6925e;
    --shadow:        0 1px 0 rgba(255,255,255,0.03) inset, 0 12px 32px rgba(0,0,0,0.35);
    --hero-grid:     rgba(255,255,255,0.035);
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      color-scheme: light;
      --bg:            #f4f3ef;
      --surface:       #ffffff;
      --surface-2:     #f6f5f2;
      --surface-3:     #ecebe6;
      --border:        rgba(11,11,11,0.08);
      --border-strong: rgba(11,11,11,0.16);
      --text:          #0f0f10;
      --text-2:        #52525b;
      --text-3:        #5b5b63;
      --grid:          #e7e5df;
      --baseline:      #c3c2b7;
      --accent:        #9a5b00;
      --accent-mark:   #c97800;
      --accent-wash:   rgba(201,120,0,0.09);
      --accent-glow:   rgba(201,120,0,0.16);
      --good:          #177a35;
      --bad:           #c23030;
      --gold:          #b8860b;
      --silver:        #7b8490;
      --bronze:        #a0582a;
      --shadow:        0 1px 2px rgba(11,11,11,0.04), 0 8px 24px rgba(11,11,11,0.06);
      --hero-grid:     rgba(11,11,11,0.04);
    }
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --bg:            #f4f3ef;
    --surface:       #ffffff;
    --surface-2:     #f6f5f2;
    --surface-3:     #ecebe6;
    --border:        rgba(11,11,11,0.08);
    --border-strong: rgba(11,11,11,0.16);
    --text:          #0f0f10;
    --text-2:        #52525b;
    --text-3:        #5b5b63;
    --grid:          #e7e5df;
    --baseline:      #c3c2b7;
    --accent:        #9a5b00;
    --accent-mark:   #c97800;
    --accent-wash:   rgba(201,120,0,0.09);
    --accent-glow:   rgba(201,120,0,0.16);
    --good:          #177a35;
    --bad:           #c23030;
    --gold:          #b8860b;
    --silver:        #7b8490;
    --bronze:        #a0582a;
    --shadow:        0 1px 2px rgba(11,11,11,0.04), 0 8px 24px rgba(11,11,11,0.06);
    --hero-grid:     rgba(11,11,11,0.04);
  }

  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
    background: var(--bg);
    color: var(--text);
    -webkit-font-smoothing: antialiased;
  }
  .display { font-family: "Barlow Condensed", "Arial Narrow", system-ui, sans-serif; }
  .num { font-variant-numeric: tabular-nums; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 0 16px; }
  svg.i { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex: none; }

  /* ---------- hero ---------- */
  .hero {
    position: relative; overflow: hidden;
    border-bottom: 1px solid var(--border);
    background:
      radial-gradient(900px 380px at 85% -10%, var(--accent-glow), transparent 60%),
      radial-gradient(600px 300px at -10% 120%, var(--accent-wash), transparent 60%),
      var(--bg);
  }
  .hero::before {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    background-image:
      linear-gradient(var(--hero-grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--hero-grid) 1px, transparent 1px);
    background-size: 44px 44px;
    mask-image: linear-gradient(to bottom, black, transparent 90%);
    -webkit-mask-image: linear-gradient(to bottom, black, transparent 90%);
  }
  .hero .wrap { position: relative; padding-top: 36px; padding-bottom: 32px; }
  .hero-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 28px; }
  .brand { display: flex; align-items: center; gap: 10px; font-size: 12px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-2); }
  .brand .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 12px var(--accent); }
  .theme-btn {
    display: inline-flex; align-items: center; gap: 8px;
    font: inherit; font-size: 12px; font-weight: 500; color: var(--text-2);
    background: var(--surface); border: 1px solid var(--border-strong);
    border-radius: 999px; padding: 7px 12px; cursor: pointer;
  }
  .theme-btn:hover { color: var(--text); }
  .theme-btn svg.i { width: 15px; height: 15px; }
  h1 {
    margin: 0; font-size: clamp(44px, 8vw, 88px); line-height: 0.9; font-weight: 800;
    letter-spacing: -0.01em; text-transform: uppercase;
  }
  h1 .accent { color: var(--accent); }
  .hero-sub { margin: 14px 0 0; color: var(--text-2); font-size: 14px; }
  .hero-sub b { color: var(--text); font-weight: 600; }

  .kpis {
    margin-top: 28px; display: grid; grid-template-columns: repeat(5, 1fr);
    background: var(--surface); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow);
  }
  .kpi { padding: 16px 20px; border-left: 1px solid var(--border); }
  .kpi:first-child { border-left: 0; }
  .kpi .l { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3); }
  .kpi .v { font-size: 38px; font-weight: 700; line-height: 1; margin-top: 6px; }
  @media (max-width: 760px) {
    .kpis { grid-template-columns: repeat(2, 1fr); }
    .kpi { border-left: 0; border-top: 1px solid var(--border); }
    .kpi:nth-child(-n+2) { border-top: 0; }
    .kpi:nth-child(even) { border-left: 1px solid var(--border); }
    .kpi:last-child { grid-column: span 2; border-left: 0; }
  }

  /* ---------- filtro fixo ---------- */
  .filterbar {
    position: sticky; top: 0; z-index: 20;
    background: color-mix(in srgb, var(--bg) 82%, transparent);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
  }
  .filterbar .wrap { display: flex; align-items: center; gap: 12px; padding-top: 10px; padding-bottom: 10px; flex-wrap: wrap; }
  .filterbar .lbl { font-size: 12px; color: var(--text-3); font-weight: 500; }
  .seg { display: inline-flex; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 3px; gap: 2px; }
  .seg button {
    font: inherit; font-size: 13px; font-weight: 600; color: var(--text-2);
    background: none; border: 0; border-radius: 7px; padding: 6px 12px; cursor: pointer; white-space: nowrap;
  }
  .seg button:hover { color: var(--text); }
  .seg button[aria-pressed="true"] { background: var(--accent-wash); color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .search-wrap { margin-left: auto; position: relative; }
  .search-wrap svg.i { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); width: 15px; height: 15px; color: var(--text-3); pointer-events: none; }
  .search {
    font: inherit; font-size: 13px; color: var(--text);
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 8px 12px 8px 32px; width: 220px; max-width: 100%;
  }
  .search::placeholder { color: var(--text-3); }
  .search:focus { outline: 2px solid var(--accent-mark); outline-offset: -1px; }
  @media (max-width: 640px) { .search-wrap { margin-left: 0; width: 100%; } .search { width: 100%; } .filterbar .lbl { display: none; } }

  /* ---------- seções ---------- */
  section.block { margin-top: 48px; }
  .sec-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
  .sec-head h2 { margin: 0; font-size: 28px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.01em; }
  .sec-head h2 small { font-family: Inter, system-ui, sans-serif; font-size: 13px; font-weight: 500; color: var(--text-3); text-transform: none; letter-spacing: 0; margin-left: 10px; }
  .sec-head .note { font-size: 13px; color: var(--text-3); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow); }

  /* ---------- pódio ---------- */
  .podium { display: grid; grid-template-columns: 1fr 1.12fr 1fr; gap: 16px; align-items: end; }
  .pod { position: relative; overflow: hidden; padding: 22px 22px 20px; --medal: var(--silver); }
  .pod.p1 { --medal: var(--gold); padding-top: 30px; padding-bottom: 26px; }
  .pod.p3 { --medal: var(--bronze); }
  .pod::before {
    content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--medal);
  }
  .pod::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(420px 200px at 100% 0%, color-mix(in srgb, var(--medal) 14%, transparent), transparent 70%);
  }
  .pod .ghost {
    position: absolute; right: -6px; top: -26px; font-size: 180px; font-weight: 800; line-height: 1;
    color: color-mix(in srgb, var(--medal) 10%, transparent); pointer-events: none; user-select: none;
  }
  .pod.p1 .ghost { font-size: 220px; top: -36px; }
  .pod .place { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--medal); }
  .pod .who { position: relative; display: flex; align-items: center; gap: 14px; margin: 16px 0 18px; }
  .avatar {
    flex: none; width: 44px; height: 44px; border-radius: 12px;
    display: grid; place-items: center; font-weight: 700; font-size: 15px;
    background: var(--surface-3); color: var(--text-2);
  }
  .pod .avatar { width: 52px; height: 52px; font-size: 18px; border-radius: 14px; background: color-mix(in srgb, var(--medal) 18%, var(--surface-2)); color: var(--medal); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--medal) 40%, transparent); }
  .pod.p1 .avatar { width: 60px; height: 60px; font-size: 21px; }
  .pod .pname { font-size: 22px; font-weight: 700; line-height: 1.1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pod.p1 .pname { font-size: 26px; }
  .pod .pmeta { font-size: 12px; color: var(--text-3); margin-top: 4px; }
  .pod .score { position: relative; display: flex; align-items: baseline; gap: 8px; }
  .pod .score .v { font-size: 60px; font-weight: 800; line-height: 0.9; }
  .pod.p1 .score .v { font-size: 76px; }
  .pod .score .l { font-size: 12px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); }
  .pod .stats { position: relative; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
  .pod .stats .l { font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3); }
  .pod .stats .v { font-size: 17px; font-weight: 600; margin-top: 3px; }
  @media (max-width: 860px) {
    .podium { grid-template-columns: 1fr; }
    .pod.p1 { order: -1; }
  }

  /* ---------- destaques ---------- */
  .awards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 12px; }
  .award { display: flex; gap: 14px; align-items: center; padding: 16px 18px; }
  .award .ic { width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center; background: var(--accent-wash); color: var(--accent); flex: none; }
  .award .ic svg.i { width: 20px; height: 20px; }
  .award .body { min-width: 0; flex: 1; }
  .award .l { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3); }
  .award .pname { font-size: 16px; font-weight: 700; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .award .hint { font-size: 12px; color: var(--text-3); margin-top: 2px; }
  .award .v { font-size: 34px; font-weight: 700; line-height: 1; }

  /* ---------- scatter ---------- */
  .chart-card { padding: 18px 18px 10px; }
  .chart-legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: var(--text-3); margin: 0 4px 8px; }
  .chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
  .chart-legend .sw { width: 10px; height: 10px; border-radius: 50%; background: var(--accent-mark); }
  .chart-legend .sw.lg { width: 16px; height: 16px; }
  .chart-legend .dash { width: 18px; border-top: 1.5px dashed var(--baseline); }
  #scatter { position: relative; }
  #scatter svg { display: block; width: 100%; overflow: visible; }
  #scatter .tick { font-size: 11px; fill: var(--text-3); font-variant-numeric: tabular-nums; }
  #scatter .axis-title { font-size: 11px; font-weight: 600; fill: var(--text-3); letter-spacing: 0.06em; text-transform: uppercase; }
  #scatter .quad { font-size: 11px; font-weight: 600; fill: var(--text-3); opacity: 0.8; letter-spacing: 0.06em; text-transform: uppercase; }
  #scatter .lbl { font-size: 11.5px; font-weight: 600; fill: var(--text-2); paint-order: stroke; stroke: var(--surface); stroke-width: 4px; stroke-linejoin: round; }
  #scatter circle.pt { fill: var(--accent-mark); fill-opacity: 0.82; stroke: var(--surface); stroke-width: 2; transition: fill-opacity .15s; }
  #scatter circle.pt.dim { fill-opacity: 0.18; }
  #scatter circle.pt.hl { fill-opacity: 1; stroke: var(--text); }
  .tooltip {
    position: absolute; pointer-events: none; z-index: 5; min-width: 170px;
    background: var(--surface-2); border: 1px solid var(--border-strong); border-radius: 10px;
    padding: 10px 12px; font-size: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    opacity: 0; transform: translateY(4px); transition: opacity .12s, transform .12s;
  }
  .tooltip.on { opacity: 1; transform: none; }
  .tooltip .t { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
  .tooltip .r { display: flex; justify-content: space-between; gap: 16px; color: var(--text-2); padding: 1px 0; }
  .tooltip .r b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }

  /* ---------- tabela ---------- */
  .table-card { overflow: hidden; }
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    position: relative; text-align: right; font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--text-3); padding: 12px 10px; border-bottom: 1px solid var(--border);
    white-space: nowrap; user-select: none; background: var(--surface-2);
  }
  thead th button { font: inherit; letter-spacing: inherit; text-transform: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
  thead th button:hover, thead th[aria-sort] button { color: var(--text); }
  thead th .arrow { color: var(--accent); font-size: 9px; }
  th.left, td.left { text-align: left; }
  tbody tr.row { cursor: pointer; }
  tbody tr.row td { padding: 12px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; text-align: right; color: var(--text-2); font-variant-numeric: tabular-nums; }
  tbody tr.row td.left { text-align: left; }
  tbody tr.row:hover td { background: var(--accent-wash); }
  tbody tr.row.open td { background: var(--accent-wash); border-bottom-color: transparent; }
  td.rank { width: 48px; font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-size: 20px; font-weight: 700; color: var(--text-3) !important; }
  tr.r1 td.rank { color: var(--gold) !important; }
  tr.r2 td.rank { color: var(--silver) !important; }
  tr.r3 td.rank { color: var(--bronze) !important; }
  .pcell { display: flex; align-items: center; gap: 12px; }
  .pcell .avatar { width: 34px; height: 34px; font-size: 12px; border-radius: 10px; }
  .pcell .pname { font-weight: 600; color: var(--text); }
  .pcell .pmeta { font-size: 12px; color: var(--text-3); margin-top: 1px; }
  .tier {
    display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 6px;
    font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-weight: 800; font-size: 14px;
    border: 1px solid var(--border-strong); color: var(--text-2);
  }
  .tier.S { background: var(--accent); color: #17120a; border-color: transparent; }
  .tier.A { background: var(--accent-wash); color: var(--accent); border-color: var(--accent-glow); }
  .scorecell { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
  .bar { width: 80px; height: 6px; background: var(--surface-3); border-radius: 3px; overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--accent-mark); border-radius: 3px; }
  .scorecell b { color: var(--text); font-weight: 700; min-width: 38px; font-size: 15px; }
  td.strong { color: var(--text) !important; font-weight: 600; }
  .sub { color: var(--text-3); font-size: 12px; margin-left: 4px; font-weight: 400; }
  .kd { display: inline-flex; align-items: center; gap: 4px; font-weight: 600; }
  .kd.up { color: var(--good); }
  .kd.down { color: var(--bad); }
  .kd .tri { font-size: 8px; }
  .chev { color: var(--text-3); width: 28px; }
  .chev svg.i { width: 16px; height: 16px; transition: transform .2s; }
  tr.open .chev svg.i { transform: rotate(180deg); }
  tr.detail td { padding: 0 !important; border-bottom: 1px solid var(--border); background: var(--accent-wash); }
  .detail-inner { padding: 4px 16px 18px 70px; display: grid; grid-template-columns: 220px 1fr; gap: 20px; }
  .dstats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-content: start; }
  .dstat { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; }
  .dstat .l { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); }
  .dstat .v { font-size: 20px; font-weight: 700; margin-top: 2px; }
  .hist { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: auto; max-height: 300px; }
  .hist table td, .hist table th { padding: 7px 10px; font-size: 12.5px; border-bottom: 1px solid var(--border); white-space: nowrap; text-align: right; color: var(--text-2); }
  .hist table th { background: var(--surface-2); font-size: 10px; position: sticky; top: 0; }
  .hist table tr:last-child td { border-bottom: 0; }
  .hist table td.left, .hist table th.left { text-align: left; }
  .res { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 5px; font-size: 11px; font-weight: 700; }
  .res.w { background: color-mix(in srgb, var(--good) 16%, transparent); color: var(--good); }
  .res.l { background: color-mix(in srgb, var(--bad) 14%, transparent); color: var(--bad); }
  .empty { padding: 40px; text-align: center; color: var(--text-3); }
  @media (max-width: 760px) {
    th.sticky, td.sticky { position: sticky; left: 0; z-index: 1; background: var(--surface); }
    thead th.sticky { background: var(--surface-2); }
    .detail-inner { grid-template-columns: 1fr; padding-left: 16px; }
    td.rank, th.rankh { display: none; }
  }

  /* ---------- mapas ---------- */
  .maps { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(270px, 100%), 1fr)); gap: 14px; }
  .map { overflow: hidden; --mc: var(--accent-mark); }
  .map .mh {
    position: relative; padding: 16px 16px 14px;
    background: linear-gradient(135deg, color-mix(in srgb, var(--mc) 34%, transparent), transparent 75%);
    border-bottom: 1px solid var(--border);
  }
  .map .mh::after {
    content: ""; position: absolute; inset: 0; pointer-events: none; opacity: .6;
    background-image: repeating-linear-gradient(135deg, color-mix(in srgb, var(--mc) 14%, transparent) 0 1px, transparent 1px 9px);
    mask-image: linear-gradient(90deg, transparent 40%, black); -webkit-mask-image: linear-gradient(90deg, transparent 40%, black);
  }
  .map .mname { position: relative; font-size: 26px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.02em; line-height: 1; }
  .map .mdate { position: relative; font-size: 12px; color: var(--text-2); margin-top: 4px; }
  .map .mb { padding: 12px 16px 14px; }
  .team { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; padding: 6px 0 6px 10px; border-left: 3px solid transparent; }
  .team .tn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-3); font-size: 13.5px; }
  .team .ts { font-size: 28px; font-weight: 700; line-height: 1; color: var(--text-3); }
  .team.win { border-left-color: var(--accent); }
  .team.win .tn { color: var(--text); font-weight: 600; }
  .team.win .ts { color: var(--text); }
  .team .wtag { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin-left: 6px; }
  .map .mvp { display: flex; align-items: center; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border-strong); font-size: 12px; color: var(--text-3); }
  .map .mvp svg.i { width: 14px; height: 14px; color: var(--accent); }
  .map .mvp b { color: var(--text); font-weight: 600; }

  /* ---------- top mapas ---------- */
  .topmaps { overflow: hidden; }
  .tm {
    display: grid; grid-template-columns: 44px minmax(120px, 1.1fr) minmax(160px, 1.5fr) 150px minmax(160px, 1.4fr) 110px;
    align-items: center; gap: 16px; padding: 14px 20px; border-bottom: 1px solid var(--border); --mc: var(--accent-mark);
    background: linear-gradient(90deg, color-mix(in srgb, var(--mc) 16%, transparent), transparent 38%);
  }
  .tm:last-child { border-bottom: 0; }
  .tm.head { background: var(--surface-2); padding-top: 10px; padding-bottom: 10px; font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-3); }
  .tm .rk { font-size: 26px; font-weight: 800; color: var(--text-3); }
  .tm:nth-child(2) .rk { color: var(--gold); }
  .tm:nth-child(3) .rk { color: var(--silver); }
  .tm:nth-child(4) .rk { color: var(--bronze); }
  .tm .mn { display: flex; align-items: center; gap: 10px; font-size: 24px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.02em; }
  .tm .mn i { width: 4px; height: 26px; border-radius: 2px; background: var(--mc); flex: none; }
  .tm .games { display: flex; align-items: center; gap: 12px; }
  .tm .games .bar { flex: 1; width: auto; height: 8px; border-radius: 4px; }
  .tm .games .n { font-size: 22px; font-weight: 700; min-width: 30px; text-align: right; }
  .tm .games .pct { font-size: 12px; color: var(--text-3); min-width: 34px; }
  .tm .avg { font-size: 18px; font-weight: 600; text-align: right; }
  .tm .avg small, .tm .close small { display: block; font-family: Inter, system-ui, sans-serif; font-size: 11px; font-weight: 500; color: var(--text-3); }
  .tm .king { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .tm .king svg.i { width: 16px; height: 16px; color: var(--accent); }
  .tm .king .kn { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tm .king .kd2 { font-size: 12px; color: var(--text-3); }
  .tm .close { font-size: 18px; font-weight: 600; text-align: right; }
  .tm.head .avg, .tm.head .close { font-size: 11px; font-weight: 600; }
  @media (max-width: 860px) {
    .tm { grid-template-columns: 36px 1fr auto; gap: 8px 12px; }
    .tm.head { display: none; }
    .tm .games { grid-column: 2 / 4; }
    .tm .king { grid-column: 2 / 4; }
    .tm .avg, .tm .close { display: none; }
    .tm .mn { font-size: 20px; }
  }

  /* ---------- montar times ---------- */
  .builder { overflow: hidden; }
  .bd-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 14px 16px; border-bottom: 1px solid var(--border); }
  .bd-top .search { width: 200px; padding-left: 12px; }
  .bd-count { font-size: 13px; color: var(--text-2); }
  .bd-count b { color: var(--accent); font-size: 15px; }
  .bd-guest { display: flex; gap: 6px; margin-left: auto; }
  .bd-guest input { width: 150px; }
  .btn {
    font: inherit; font-size: 13px; font-weight: 600; color: var(--text-2); cursor: pointer; white-space: nowrap;
    background: var(--surface-2); border: 1px solid var(--border-strong); border-radius: 10px; padding: 8px 12px;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .btn:hover { color: var(--text); }
  .btn svg.i { width: 15px; height: 15px; }
  .btn.primary { background: var(--accent-mark); border-color: transparent; color: #17120a; }
  .btn.primary:hover { filter: brightness(1.08); color: #17120a; }
  .btn:disabled { opacity: .45; cursor: not-allowed; filter: none; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 16px; max-height: 260px; overflow-y: auto; }
  .chip {
    display: inline-flex; align-items: center; gap: 8px; font: inherit; font-size: 13px; font-weight: 600; color: var(--text-2);
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px 4px 4px; cursor: pointer;
  }
  .chip .avatar { width: 26px; height: 26px; font-size: 10px; border-radius: 50%; }
  .chip small { font-weight: 500; color: var(--text-3); font-variant-numeric: tabular-nums; }
  .chip:hover { border-color: var(--border-strong); color: var(--text); }
  .chip[aria-pressed="true"] { background: var(--accent-wash); border-color: var(--accent-mark); color: var(--text); }
  .chip[aria-pressed="true"] .avatar { background: var(--accent-mark); color: #17120a; }
  .chip.guest { border-style: dashed; }
  .bd-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 12px 16px; border-top: 1px solid var(--border); background: var(--surface-2); }
  .bd-actions .hint { font-size: 12px; color: var(--text-3); margin-left: auto; }
  .bd-result { padding: 16px; border-top: 1px solid var(--border); }
  .bd-result:empty { display: none; }
  .balance { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; margin-bottom: 14px; }
  .balance .side { font-size: 26px; font-weight: 700; }
  .balance .meter { height: 10px; border-radius: 5px; background: var(--surface-3); position: relative; overflow: hidden; display: flex; gap: 2px; }
  .balance .meter i { display: block; height: 100%; }
  .balance .meter i.a { background: var(--accent-mark); border-radius: 5px 0 0 5px; }
  .balance .meter i.b { background: var(--text-3); border-radius: 0 5px 5px 0; }
  .verdict { text-align: center; font-size: 13px; color: var(--text-2); margin: -6px 0 14px; }
  .verdict b { color: var(--text); }
  .teams { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 760px) { .teams { grid-template-columns: 1fr; } .bd-guest { margin-left: 0; } }
  .team-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .team-card.a { box-shadow: inset 3px 0 0 var(--accent-mark); }
  .team-card.b { box-shadow: inset 3px 0 0 var(--text-3); }
  .team-card .th { display: flex; align-items: baseline; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
  .team-card .th .nm { font-size: 20px; font-weight: 700; text-transform: uppercase; }
  .team-card .th .st { margin-left: auto; font-size: 12px; color: var(--text-3); }
  .team-card .th .st b { color: var(--text); font-size: 15px; }
  .tp { display: flex; align-items: center; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--border); font-size: 13px; }
  .tp:last-child { border-bottom: 0; }
  .tp .avatar { width: 28px; height: 28px; font-size: 11px; border-radius: 8px; }
  .tp .n { font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tp .s { color: var(--text-3); font-variant-numeric: tabular-nums; font-size: 12px; }
  .tp .cap { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--accent); }
  .bd-msg { font-size: 13px; color: var(--text-3); padding: 4px 0; }
  .toast { position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 20px); z-index: 70; background: var(--text); color: var(--bg); font-weight: 600; font-size: 13px; padding: 10px 16px; border-radius: 10px; opacity: 0; transition: opacity .2s, transform .2s; pointer-events: none; }
  .toast.on { opacity: 1; transform: translate(-50%, 0); }

  footer { margin: 56px 0 48px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; line-height: 1.7; }
  footer b { color: var(--text-2); font-weight: 600; }

  /* ---------- seletor de noite ---------- */
  .select-wrap { position: relative; }
  .select {
    appearance: none; -webkit-appearance: none;
    font: inherit; font-size: 13px; font-weight: 600; color: var(--text);
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: 10px;
    padding: 8px 32px 8px 34px; cursor: pointer;
  }
  .select:focus { outline: 2px solid var(--accent-mark); outline-offset: -1px; }
  .select-wrap .cal { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); width: 15px; height: 15px; color: var(--accent); pointer-events: none; }
  .select-wrap .dn { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); width: 14px; height: 14px; color: var(--text-3); pointer-events: none; }

  /* ---------- clicáveis ---------- */
  .link { cursor: pointer; }
  .link:hover { color: var(--accent); }
  .pod, .award, .map, .ln-map, .tm, .duo-row, .riv-row { cursor: pointer; }
  .pod:hover, .award:hover, .map:hover { border-color: var(--border-strong); }
  .map { transition: transform .15s, border-color .15s; }
  .map:hover { transform: translateY(-2px); }

  /* ---------- imagem de mapa ---------- */
  .mapimg { background-color: var(--surface-3); background-size: cover; background-position: center; }

  /* ---------- última noite ---------- */
  .lastnight { display: grid; grid-template-columns: minmax(260px, 0.9fr) 1.6fr; overflow: hidden; }
  .ln-info { padding: 22px; display: flex; flex-direction: column; gap: 18px; border-right: 1px solid var(--border); }
  .ln-title { font-size: 34px; font-weight: 800; text-transform: uppercase; line-height: 0.95; }
  .ln-title span { color: var(--accent); }
  .ln-sub { font-size: 13px; color: var(--text-3); margin-top: 6px; }
  .ln-hl { display: flex; align-items: center; gap: 12px; }
  .ln-hl .l { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3); }
  .ln-hl .n { font-size: 16px; font-weight: 700; }
  .ln-hl .h { font-size: 12px; color: var(--text-3); }
  .ln-btn {
    align-self: flex-start; font: inherit; font-size: 13px; font-weight: 600; color: var(--accent);
    background: var(--accent-wash); border: 1px solid var(--accent-glow); border-radius: 10px; padding: 8px 14px; cursor: pointer;
  }
  .ln-btn:hover { background: var(--accent-glow); }
  .ln-maps { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(180px, 100%), 1fr)); gap: 10px; padding: 16px; align-content: start; }
  .ln-map { position: relative; height: 112px; border-radius: 12px; overflow: hidden; color: #fff; }
  .ln-map::after { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,0.15), rgba(0,0,0,0.8)); }
  .ln-map > div { position: absolute; left: 12px; right: 12px; bottom: 10px; z-index: 1; }
  .ln-map .nm { font-size: 20px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.02em; text-shadow: 0 1px 8px rgba(0,0,0,.5); }
  .ln-map .sc { font-size: 13px; font-weight: 600; opacity: .95; }
  .ln-map .sc b { color: #fbc15e; }
  .ln-map:hover { outline: 2px solid var(--accent-mark); }
  @media (max-width: 860px) { .lastnight { grid-template-columns: 1fr; } .ln-info { border-right: 0; border-bottom: 1px solid var(--border); } }

  /* ---------- forma ---------- */
  .pills { display: inline-flex; gap: 3px; }
  .pill {
    width: 18px; height: 18px; border-radius: 5px; display: inline-grid; place-items: center;
    font-size: 10px; font-weight: 700; font-family: Inter, system-ui, sans-serif;
  }
  .pill.w { background: color-mix(in srgb, var(--good) 18%, transparent); color: var(--good); }
  .pill.l { background: color-mix(in srgb, var(--bad) 15%, transparent); color: var(--bad); }
  .pill.e { background: var(--surface-3); color: transparent; }
  .forms { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 12px; }
  .fcard { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
  .fcard .top { display: flex; align-items: center; gap: 12px; }
  .fcard .ic { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; flex: none; }
  .fcard.hot .ic { background: var(--accent-wash); color: var(--accent); }
  .fcard.best .ic { background: color-mix(in srgb, var(--good) 14%, transparent); color: var(--good); }
  .fcard.cold .ic { background: color-mix(in srgb, var(--bad) 12%, transparent); color: var(--bad); }
  .fcard .l { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3); }
  .fcard .n { font-size: 16px; font-weight: 700; }
  .fcard .v { margin-left: auto; font-size: 34px; font-weight: 700; line-height: 1; text-align: right; }
  .fcard .v small { display: block; font-family: Inter, system-ui, sans-serif; font-size: 11px; font-weight: 500; color: var(--text-3); }
  .fcard .none { font-size: 13px; color: var(--text-3); }

  /* ---------- duplas e rivalidades ---------- */
  .pairs { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 860px) { .pairs { grid-template-columns: 1fr; } }
  .pairs .card { overflow: hidden; }
  .pairs h3 { margin: 0; padding: 14px 18px; font-size: 20px; font-weight: 700; text-transform: uppercase; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
  .pairs h3 svg.i { color: var(--accent); }
  .pairs h3 small { margin-left: auto; font-family: Inter, system-ui, sans-serif; font-size: 12px; font-weight: 500; color: var(--text-3); text-transform: none; }
  .duo-row, .riv-row { display: grid; align-items: center; gap: 12px; padding: 11px 18px; border-bottom: 1px solid var(--border); }
  .duo-row:last-child, .riv-row:last-child { border-bottom: 0; }
  .duo-row:hover, .riv-row:hover { background: var(--accent-wash); }
  .duo-row { grid-template-columns: 22px auto 1fr auto; }
  .duo-row .rk, .riv-row .rk { font-size: 18px; font-weight: 700; color: var(--text-3); }
  .stack { display: flex; }
  .stack .avatar { width: 30px; height: 30px; font-size: 11px; border-radius: 9px; box-shadow: 0 0 0 2px var(--surface); }
  .stack .avatar + .avatar { margin-left: -8px; }
  .duo-row .nm { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .duo-row .nm span { color: var(--text-3); font-weight: 400; margin: 0 4px; }
  .duo-row .rec { text-align: right; font-weight: 700; white-space: nowrap; }
  .duo-row .rec small { display: block; font-size: 11px; font-weight: 500; color: var(--text-3); }
  .riv-row { grid-template-columns: 22px 1fr auto 1fr; }
  .riv-row .a { text-align: right; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .riv-row .b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .riv-row .vs { text-align: center; font-size: 22px; font-weight: 700; white-space: nowrap; }
  .riv-row .vs small { display: block; font-family: Inter, system-ui, sans-serif; font-size: 10px; font-weight: 500; color: var(--text-3); letter-spacing: 0.04em; }
  .riv-row .lead { color: var(--accent); }
  .pairs .none { padding: 24px 18px; color: var(--text-3); font-size: 13px; }

  /* ---------- top mapas (thumb) ---------- */
  .tm .mn .thumb { width: 64px; height: 36px; border-radius: 7px; flex: none; }
  .tm:hover { filter: brightness(1.06); }

  /* ---------- cards de partida com imagem ---------- */
  .map .mh.mapimg { background-image: var(--img); min-height: 104px; display: flex; flex-direction: column; justify-content: flex-end; color: #fff; border-bottom: 0; }
  .map .mh.mapimg::after { content: ""; position: absolute; inset: 0; opacity: 1; mask-image: none; -webkit-mask-image: none; background: linear-gradient(180deg, rgba(0,0,0,0.05), rgba(0,0,0,0.78)); }
  .map .mh.mapimg .mname, .map .mh.mapimg .mdate { z-index: 1; color: #fff; text-shadow: 0 1px 8px rgba(0,0,0,.45); }
  .map .mh.mapimg .mdate { opacity: .9; }

  /* ---------- painel do jogador ---------- */
  body.locked { overflow: hidden; }
  .overlay { position: fixed; inset: 0; z-index: 40; background: rgba(0,0,0,0.55); opacity: 0; pointer-events: none; transition: opacity .2s; }
  .overlay.on { opacity: 1; pointer-events: auto; }
  .drawer {
    position: fixed; top: 0; right: 0; z-index: 50; height: 100%; width: min(600px, 100%);
    background: var(--bg); border-left: 1px solid var(--border-strong); overflow-y: auto;
    transform: translateX(102%); transition: transform .25s ease; box-shadow: -20px 0 60px rgba(0,0,0,0.35);
  }
  .drawer.on { transform: none; }
  .dr-head {
    position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 14px; padding: 18px 20px;
    background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
  }
  .dr-head .avatar { width: 52px; height: 52px; font-size: 18px; border-radius: 14px; background: var(--accent-wash); color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .dr-head .nm { font-size: 24px; font-weight: 700; line-height: 1.1; display: flex; align-items: center; gap: 8px; }
  .dr-head .sub2 { font-size: 12px; color: var(--text-3); margin-top: 3px; }
  .x {
    margin-left: auto; flex: none; width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
    background: var(--surface); border: 1px solid var(--border-strong); color: var(--text-2); cursor: pointer;
  }
  .x:hover { color: var(--text); }
  .dr-body { padding: 18px 20px 40px; display: flex; flex-direction: column; gap: 22px; }
  .dr-sec h4 { margin: 0 0 10px; font-size: 13px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-size: 16px; }
  .dgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  @media (max-width: 520px) { .dgrid { grid-template-columns: repeat(2, 1fr); } }
  .dcell { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; }
  .dcell .l { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); }
  .dcell .v { font-size: 24px; font-weight: 700; margin-top: 2px; line-height: 1.1; }
  .dcell .s { font-size: 11px; color: var(--text-3); margin-top: 2px; }
  .formline { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; font-size: 13px; color: var(--text-2); }
  .formline b { color: var(--text); }
  .rels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  @media (max-width: 520px) { .rels { grid-template-columns: 1fr; } }
  .rel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; }
  .rel .l { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); }
  .rel .n { font-weight: 700; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rel .s { font-size: 12px; color: var(--text-3); margin-top: 1px; }
  .rel.none .n { color: var(--text-3); font-weight: 500; }
  .permap, .matches { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .pm-row, .mt-row { display: grid; align-items: center; gap: 12px; padding: 9px 12px; border-bottom: 1px solid var(--border); font-size: 13px; font-variant-numeric: tabular-nums; }
  .pm-row:last-child, .mt-row:last-child { border-bottom: 0; }
  .pm-row { grid-template-columns: 56px 1fr 44px 64px 56px 48px; color: var(--text-2); }
  .pm-row.head, .mt-row.head { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); background: var(--surface-2); padding-top: 7px; padding-bottom: 7px; }
  .pm-row .thumb, .mt-row .thumb { width: 56px; height: 32px; border-radius: 6px; }
  .pm-row .mn, .mt-row .mn { font-weight: 700; color: var(--text); font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-size: 17px; text-transform: uppercase; }
  .pm-row > span:not(.mn):not(.thumb):not(.mn-h), .mt-row > span.r { text-align: right; }
  .mt-row { grid-template-columns: 56px 1fr 24px 56px 70px 48px 16px; color: var(--text-2); cursor: pointer; }
  .mt-row:not(.head):hover { background: var(--accent-wash); }
  .mt-row .dt { font-size: 11px; color: var(--text-3); font-family: Inter, system-ui, sans-serif; font-weight: 400; text-transform: none; }
  .mt-row .kda { color: var(--text); font-weight: 600; }
  .mt-row svg.i { width: 14px; height: 14px; color: var(--text-3); }
  @media (max-width: 520px) {
    .pm-row { grid-template-columns: 48px 1fr 60px 50px; }
    .pm-row > :nth-child(3), .pm-row > :nth-child(6) { display: none; }
    .mt-row { grid-template-columns: 48px 1fr 22px 60px 14px; }
    .mt-row > :nth-child(4), .mt-row > :nth-child(6) { display: none; }
    .pm-row .thumb, .mt-row .thumb { width: 48px; height: 28px; }
  }

  /* ---------- placar da partida ---------- */
  .modal-wrap { position: fixed; inset: 0; z-index: 60; display: none; place-items: center; padding: 16px; background: rgba(0,0,0,0.6); }
  .modal-wrap.on { display: grid; }
  .modal { width: min(880px, 100%); max-height: calc(100vh - 32px); overflow-y: auto; background: var(--bg); border: 1px solid var(--border-strong); border-radius: 18px; box-shadow: 0 30px 80px rgba(0,0,0,0.5); }
  .banner { position: relative; min-height: 200px; display: flex; flex-direction: column; justify-content: flex-end; padding: 18px 20px; color: #fff; }
  .banner::after { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,0.25), rgba(0,0,0,0.85)); }
  .banner > * { position: relative; z-index: 1; }
  .banner .x { position: absolute; top: 14px; right: 14px; z-index: 2; background: rgba(0,0,0,0.45); border-color: rgba(255,255,255,0.2); color: #fff; }
  .banner .bm { font-size: 13px; font-weight: 600; opacity: .9; }
  .banner .bn { font-size: 44px; font-weight: 800; text-transform: uppercase; line-height: 0.95; }
  .banner .bscore { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px; margin-top: 14px; }
  .banner .bt { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .75; }
  .banner .bt.win { opacity: 1; }
  .banner .bt.r { text-align: right; }
  .banner .bs { font-size: 52px; font-weight: 800; line-height: 1; white-space: nowrap; }
  .banner .bs .w { color: #fbc15e; }
  .banner .bs .lo { opacity: .7; }
  .banner .bs i { font-style: normal; opacity: .5; margin: 0 8px; font-weight: 500; }
  .boards { display: grid; grid-template-columns: 1fr; gap: 14px; padding: 16px 18px 22px; }
  .board { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .board .bh { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); font-weight: 700; }
  .board .bh .tag { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); background: var(--accent-wash); border-radius: 6px; padding: 3px 7px; }
  .board .bh .tag.l { color: var(--text-3); background: var(--surface-3); }
  .board .bh .sc { margin-left: auto; font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-size: 26px; }
  .board.win { box-shadow: inset 3px 0 0 var(--accent); }
  .board table td, .board table th { padding: 8px 12px; font-size: 13px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--text-2); }
  .board table th { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); background: var(--surface-2); }
  .board table tr:last-child td { border-bottom: 0; }
  .board td.pl, .board th.pl { text-align: left; }
  .board td.pl { color: var(--text); font-weight: 600; }
  .board td.pl svg.i { width: 13px; height: 13px; color: var(--accent); vertical-align: -1px; margin-left: 4px; }
  .board .scroll { overflow-x: auto; }
  .board table { width: 100%; min-width: 520px; table-layout: fixed; }
  .board th.pl { width: 34%; }
  .board td.pl { overflow: hidden; text-overflow: ellipsis; }
  @media (max-width: 560px) { .banner .bn { font-size: 34px; } .banner .bs { font-size: 38px; } }
</style>
</head>
<body>

<header class="hero">
  <div class="wrap">
    <div class="hero-top">
      <div class="brand"><span class="dot"></span>MatchZy · CS2 · Mix</div>
      <button class="theme-btn" id="theme-btn" type="button">
        <svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
        Tema
      </button>
    </div>
    <h1 class="display">Ranking <span class="accent">do Mix</span></h1>
    <p class="hero-sub" id="hero-sub"></p>
    <div class="kpis" id="kpis"></div>
  </div>
</header>

<div class="filterbar">
  <div class="wrap">
    <div class="select-wrap">
      <svg class="i cal" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>
      <select class="select" id="scope" aria-label="Escolher noite"></select>
      <svg class="i dn" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
    </div>
    <div class="seg" id="seg" role="group" aria-label="Mínimo de mapas"></div>
    <div class="search-wrap">
      <svg class="i" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input class="search" id="search" type="search" placeholder="Buscar jogador" aria-label="Buscar jogador" />
    </div>
  </div>
</div>

<main class="wrap">
  <section class="block" id="lastnight-sec">
    <div class="card lastnight" id="lastnight"></div>
  </section>

  <section class="block" id="teams-sec">
    <div class="sec-head">
      <h2 class="display">Montar times <small>marque quem vai jogar e equilibre</small></h2>
      <span class="note">força = MixScore + aproveitamento, de todas as noites</span>
    </div>
    <div class="card builder">
      <div class="bd-top">
        <input class="search" id="bd-search" type="search" placeholder="Filtrar jogadores" aria-label="Filtrar jogadores" />
        <span class="bd-count" id="bd-count"></span>
        <div class="bd-guest">
          <input class="search" id="bd-guest" type="text" placeholder="Convidado (nome)" aria-label="Nome do convidado" maxlength="24" />
          <button class="btn" type="button" id="bd-add">+ Adicionar</button>
        </div>
      </div>
      <div class="chips" id="bd-chips"></div>
      <div class="bd-actions">
        <button class="btn primary" type="button" id="bd-balance">⚖ Equilibrar</button>
        <button class="btn" type="button" id="bd-shuffle">↻ Sortear outra</button>
        <button class="btn" type="button" id="bd-copy">Copiar times</button>
        <button class="btn" type="button" id="bd-clear">Limpar</button>
        <span class="hint" id="bd-hint"></span>
      </div>
      <div class="bd-result" id="bd-result"></div>
    </div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Pódio <small>por vitórias</small></h2><span class="note" id="podium-note"></span></div>
    <div class="podium" id="podium"></div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Destaques</h2><span class="note" id="awards-note"></span></div>
    <div class="awards" id="awards"></div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Forma <small>sequências de vitórias e derrotas</small></h2></div>
    <div class="forms" id="forms"></div>
  </section>

  <section class="block">
    <div class="sec-head">
      <h2 class="display">Top mapas <small>os mais jogados</small></h2>
      <span class="note">rei do mapa = maior ADR médio com 2+ jogos no mapa</span>
    </div>
    <div class="card topmaps" id="topmaps"></div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Duplas e rivalidades</h2><span class="note" id="pairs-note"></span></div>
    <div class="pairs" id="pairs"></div>
  </section>

  <section class="block">
    <div class="sec-head">
      <h2 class="display">Dano × K/D <small>cada ponto é um jogador · clique pra abrir o perfil</small></h2>
    </div>
    <div class="card chart-card">
      <div class="chart-legend">
        <span><i class="sw"></i><i class="sw lg"></i> tamanho = mapas jogados</span>
        <span><i class="dash"></i> K/D 1,0 e ADR mediano</span>
      </div>
      <div id="scatter"><div class="tooltip" id="tip"></div></div>
    </div>
  </section>

  <section class="block">
    <div class="sec-head">
      <h2 class="display">Classificação <small id="count"></small></h2>
      <span class="note">clique num jogador pra ver as partidas dele</span>
    </div>
    <div class="card table-card">
      <div class="table-scroll">
        <table>
          <thead><tr id="thead-row"></tr></thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Partidas <small id="maps-count"></small></h2><span class="note">clique pra ver o placar completo</span></div>
    <div class="maps" id="maps"></div>
  </section>

  <footer id="footer"></footer>
</main>

<div class="overlay" id="overlay"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-label="Perfil do jogador"></aside>
<div class="modal-wrap" id="modal-wrap"><div class="modal" id="modal" role="dialog" aria-modal="true" aria-label="Placar da partida"></div></div>

<script>
const PAYLOAD = @@PAYLOAD_JSON@@;
const SCOPES = PAYLOAD.scopes;
const ALL_MAPS = {};
SCOPES[0].maps.forEach((m) => { ALL_MAPS[m.id] = m; });
const MAP_FILES = ["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke", "overpass", "train", "vertigo"];
const MAP_COLORS = {
  dust2: "#c8a165", mirage: "#d9965b", inferno: "#c7503b", nuke: "#4f86c6", ancient: "#4f8f5f",
  anubis: "#3aa0a0", train: "#8a8f98", cache: "#7d9a5a", overpass: "#6aa0c8", vertigo: "#9b7fd1",
};

const ICONS = {
  trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
  flame: '<path d="M12 3c1 4 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 2.5-5 .3 2 1.5 3 2.5 3 0-3-1-5 0-8z"/>',
  skull: '<path d="M12 3a8 8 0 0 0-5 14.2V20h10v-2.8A8 8 0 0 0 12 3z"/><circle cx="9" cy="11" r="1.5"/><circle cx="15" cy="11" r="1.5"/><path d="M10 20v-2M14 20v-2"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z"/>',
  chevr: '<path d="M9 6l6 6-6 6"/>',
  medal: '<circle cx="12" cy="15" r="6"/><path d="M8.5 10.2L6 3h4l2 4 2-4h4l-2.5 7.2"/>',
  up: '<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>',
  down: '<path d="M3 7l6 6 4-4 8 8M15 17h6v-6"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  swords: '<path d="M14.5 17.5L3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4M19 21l2-2M9.5 6.5L14 2h3v3l-4.5 4.5M5 14l-2 2 3 3 2-2"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
};
const icon = (n) => '<svg class="i" viewBox="0 0 24 24">' + ICONS[n] + "</svg>";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const fmt = (v, d = 0) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
const isAlnum = (ch) => ch.toLowerCase() !== ch.toUpperCase() || "0123456789".includes(ch);
const initials = (name) => {
  const words = String(name || "?").split(" ").map((w) => Array.from(w).filter(isAlnum).join("")).filter(Boolean);
  if (!words.length) return "?";
  const s = words.length > 1 ? Array.from(words[0])[0] + Array.from(words[1])[0] : Array.from(words[0]).slice(0, 2).join("");
  return s.toUpperCase();
};
const tierOf = (r) => (r >= 72 ? "S" : r >= 65 ? "A" : r >= 58 ? "B" : r >= 50 ? "C" : "D");
const shortDate = (d) => (d ? d.slice(8, 10) + "/" + d.slice(5, 7) + " " + d.slice(11, 16) : "");
const dayOf = (d) => (d ? d.slice(8, 10) + "/" + d.slice(5, 7) : "");
const mapBg = (slug) => {
  const c = MAP_COLORS[slug] || "var(--accent-mark)";
  return MAP_FILES.includes(slug)
    ? "background-color:" + c + ";background-image:url(assets/maps/" + slug + ".jpg)"
    : "background-image:linear-gradient(135deg," + c + ",var(--surface-3))";
};
const thumb = (slug) => '<span class="thumb mapimg" style="' + mapBg(slug) + '"></span>';
const pills = (hist, n) => {
  const last = hist.slice(0, n).reverse();
  const pad = Array(Math.max(0, n - last.length)).fill('<span class="pill e"></span>');
  return '<span class="pills" title="últimos resultados, do mais antigo pro mais recente">' + pad.join("") +
    last.map((h) => '<span class="pill ' + (h.won ? "w" : "l") + '">' + (h.won ? "V" : "D") + "</span>").join("") + "</span>";
};
const playerLink = (sid, name) => '<span class="link" data-player="' + esc(sid) + '">' + esc(name) + "</span>";

/* ---------- tema ---------- */
(function () {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("mix-theme"); } catch (e) {}
  if (saved) root.dataset.theme = saved;
  $("theme-btn").addEventListener("click", () => {
    const cur = root.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("mix-theme", next); } catch (e) {}
    renderScatter();
  });
})();

/* ---------- escopo (todas as noites ou uma noite) ---------- */
let S, ALL, MAPS, BYSID, MAX_WINS, FILTERS;
const state = { minMaps: 1, q: "", sortKey: "rank", sortDir: 1 };

function enrich(p) {
  // histórico vem do mais recente pro mais antigo
  const h = p.history;
  let streak = 0;
  if (h.length) {
    const first = h[0].won;
    for (const x of h) { if (x.won !== first) break; streak += 1; }
    if (!first) streak = -streak;
  }
  let best = 0, run = 0;
  h.slice().reverse().forEach((x) => { run = x.won ? run + 1 : 0; best = Math.max(best, run); });
  return Object.assign({}, p, { streak, best_streak: best });
}

function setScope(id) {
  S = SCOPES.find((s) => s.id === id) || SCOPES[0];
  ALL = S.players.map(enrich);
  MAPS = S.maps;
  BYSID = {};
  ALL.forEach((p) => { BYSID[p.steamid64] = p; });
  MAX_WINS = Math.max(...ALL.map((p) => p.wins), 1);
  FILTERS = [1, 3, 5, 10].filter((n) => n === 1 || ALL.filter((p) => p.matches >= n).length >= 3);
  if (!FILTERS.includes(state.minMaps)) state.minMaps = 1;
  $("scope").value = S.id;
  renderScope();
}

function visiblePlayers() {
  return ALL.filter((p) => p.matches >= state.minMaps)
    .slice()
    .sort((a, b) => b.wins - a.wins || b.win_pct - a.win_pct || b.rating - a.rating)
    .map((p, i) => Object.assign({}, p, { rank: i + 1 }));
}

$("scope").innerHTML = SCOPES.map((s) =>
  '<option value="' + s.id + '">' + esc(s.label) + " · " + plural(s.maps.length, "mapa", "mapas") + "</option>").join("");
$("scope").addEventListener("change", (e) => { setScope(e.target.value); window.scrollTo({ top: 0, behavior: "smooth" }); });

/* ---------- cabeçalho ---------- */
function renderHeader() {
  const totalRounds = MAPS.reduce((s, m) => s + m.score1 + m.score2, 0);
  const totalKills = ALL.reduce((s, p) => s + p.kills, 0);
  const totalHs = ALL.reduce((s, p) => s + p.kills * p.hs_pct / 100, 0);
  const nights = SCOPES.length - 1;
  $("hero-sub").innerHTML = S.id === "all"
    ? "<b>" + esc(S.period) + "</b> · " + plural(nights, "noite", "noites") + " de mix · só mapas finalizados " + PAYLOAD.teamSize + "x" + PAYLOAD.teamSize + " · fonte: " + esc(PAYLOAD.sources)
    : "<b>" + esc(S.label) + "</b> · só mapas finalizados " + PAYLOAD.teamSize + "x" + PAYLOAD.teamSize + " · fonte: " + esc(PAYLOAD.sources);
  $("kpis").innerHTML = [
    ["Mapas", fmt(MAPS.length)],
    ["Rounds", fmt(totalRounds)],
    ["Jogadores", fmt(ALL.length)],
    ["Kills", fmt(totalKills)],
    ["Headshot", fmt(totalKills ? (totalHs / totalKills) * 100 : 0, 1) + "%"],
  ].map(([l, v]) => '<div class="kpi"><div class="l">' + l + '</div><div class="v display num">' + v + "</div></div>").join("");
  $("maps-count").textContent = plural(MAPS.length, "mapa", "mapas");
}

function renderSeg() {
  $("seg").innerHTML = FILTERS.map((n) =>
    '<button type="button" data-n="' + n + '" aria-pressed="' + (state.minMaps === n) + '">' +
    (n === 1 ? "Todos" : n + "+ mapas") + "</button>").join("");
  $("seg").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { state.minMaps = Number(b.dataset.n); renderAll(); }));
}

/* ---------- última noite ---------- */
function renderLastNight() {
  const sec = $("lastnight-sec");
  const N = SCOPES[1];
  if (S.id !== "all" || !N) { sec.style.display = "none"; return; }
  sec.style.display = "";
  const ps = N.players;
  const pool = ps.filter((p) => p.matches >= 2);
  const src = pool.length ? pool : ps;
  const mvp = src.reduce((a, b) => (!a || b.raw_rating > a.raw_rating ? b : a), null);
  const top = ps.slice().sort((a, b) => b.wins - a.wins || b.win_pct - a.win_pct)[0];
  const hl = (lbl, ic, p, hint) => p ? '<div class="ln-hl"><div class="avatar">' + esc(initials(p.name)) + "</div><div>" +
    '<div class="l">' + lbl + '</div><div class="n">' + playerLink(p.steamid64, p.name) + '</div><div class="h">' + hint + "</div></div></div>" : "";
  const maps = N.maps.slice().reverse();
  $("lastnight").innerHTML =
    '<div class="ln-info"><div><div class="ln-title display">Última noite <span>' + esc(N.period) + "</span></div>" +
    '<div class="ln-sub">' + plural(maps.length, "mapa", "mapas") + " · " + plural(ps.length, "jogador", "jogadores") + "</div></div>" +
    hl("MVP da noite", "star", mvp, mvp ? "nota " + fmt(mvp.raw_rating, 1) + " · " + fmt(mvp.adr, 1) + " ADR · K/D " + fmt(mvp.kd, 2) : "") +
    hl("Mais vitórias", "trophy", top, top ? top.wins + "V " + top.losses + "D na noite" : "") +
    '<button class="ln-btn" type="button" id="ln-go">Ver só essa noite →</button></div>' +
    '<div class="ln-maps">' + maps.map((m) => {
      const w1 = m.winner === m.team1;
      return '<div class="ln-map mapimg" data-match="' + m.id + '" style="' + mapBg(m.slug) + '"><div>' +
        '<div class="nm display">' + esc(m.map) + '</div><div class="sc num">' +
        (w1 ? "<b>" + m.score1 + "</b>–" + m.score2 : m.score1 + "–<b>" + m.score2 + "</b>") + " · " + esc(m.winner) + "</div></div></div>";
    }).join("") + "</div>";
  $("ln-go").addEventListener("click", () => setScope(N.id));
}

/* ---------- pódio ---------- */
function renderPodium(list) {
  const top = list.slice(0, 3);
  $("podium-note").textContent = state.minMaps > 1 ? "entre quem jogou " + state.minMaps + "+ mapas" : "";
  $("podium").innerHTML = [1, 0, 2].map((i) => {
    const p = top[i];
    if (!p) return "<div></div>";
    const stats = [["MixScore", fmt(p.rating, 1)], ["K/D", fmt(p.kd, 2)], ["ADR", fmt(p.adr, 1)], ["HS", fmt(p.hs_pct, 0) + "%"]];
    return '<div class="card pod p' + (i + 1) + '" data-player="' + p.steamid64 + '">' +
      '<div class="ghost display">' + (i + 1) + "</div>" +
      '<div class="place">' + icon("medal") + (i + 1) + "º lugar</div>" +
      '<div class="who"><div class="avatar">' + esc(initials(p.name)) + '</div><div style="min-width:0">' +
      '<div class="pname">' + esc(p.name) + '</div><div class="pmeta">' +
      plural(p.matches, "mapa", "mapas") + " · " + fmt(p.win_pct, 0) + "% de aproveitamento</div></div></div>" +
      '<div class="score"><span class="v display num">' + p.wins + '</span><span class="l">' + (p.wins === 1 ? "vitória" : "vitórias") +
      " · " + plural(p.losses, "derrota", "derrotas") + "</span></div>" +
      '<div class="stats">' + stats.map(([l, v]) => '<div><div class="l">' + l + '</div><div class="v num">' + v + "</div></div>").join("") + "</div>" +
      "</div>";
  }).join("");
}

/* ---------- destaques ---------- */
function renderAwards(list) {
  // em "todas as noites", só entre quem já jogou o suficiente (mesmo peso de confiança do MixScore)
  const need = S.id === "all" ? PAYLOAD.confidence : 0;
  const pool = list.filter((p) => p.rounds >= need);
  const src = pool.length >= 3 ? pool : list;
  $("awards-note").textContent = need && src === pool ? "entre quem jogou " + need + "+ rounds" : "";
  const best = (key) => src.reduce((a, b) => (!a || b[key] > a[key] ? b : a), null);
  const defs = [
    { l: "Melhor aproveitamento", k: "win_pct", ic: "trophy", v: (p) => fmt(p.win_pct, 0) + "%", h: (p) => p.wins + " vitórias em " + plural(p.matches, "mapa", "mapas") },
    { l: "Maior dano (ADR)", k: "adr", ic: "flame", v: (p) => fmt(p.adr, 1), h: (p) => fmt(p.damage) + " de dano em " + p.rounds + " rounds" },
    { l: "Melhor K/D", k: "kd", ic: "skull", v: (p) => fmt(p.kd, 2), h: (p) => p.kills + " kills · " + p.deaths + " mortes" },
    { l: "Mira de headshot", k: "hs_pct", ic: "target", v: (p) => fmt(p.hs_pct, 1) + "%", h: (p) => Math.round(p.kills * p.hs_pct / 100) + " de " + p.kills + " kills na cabeça" },
    { l: "Rei do clutch", k: "clutch_won", ic: "shield", v: (p) => p.clutch_won, h: (p) => "venceu " + p.clutch_won + " de " + p.clutch_att + " (1v1 e 1v2)" },
    { l: "Multi-kills", k: "multi_kills", ic: "zap", v: (p) => p.multi_kills, h: (p) => p.k4 + " quadras · " + plural(p.aces, "ace", "aces") },
  ];
  $("awards").innerHTML = defs.map((d) => {
    const p = best(d.k);
    if (!p) return "";
    return '<div class="card award" data-player="' + p.steamid64 + '"><div class="ic">' + icon(d.ic) + '</div><div class="body">' +
      '<div class="l">' + d.l + '</div><div class="pname">' + esc(p.name) + '</div><div class="hint">' + d.h(p) + "</div></div>" +
      '<div class="v display num">' + d.v(p) + "</div></div>";
  }).join("");
}

/* ---------- forma ---------- */
function renderForms(list) {
  const hot = list.filter((p) => p.streak >= 2).sort((a, b) => b.streak - a.streak || b.wins - a.wins)[0];
  const best = list.filter((p) => p.best_streak >= 2).sort((a, b) => b.best_streak - a.best_streak || b.wins - a.wins)[0];
  const cold = list.filter((p) => p.streak <= -2).sort((a, b) => a.streak - b.streak || a.wins - b.wins)[0];
  const card = (cls, ic, lbl, p, val, sub, empty) => '<div class="card fcard ' + cls + '"' + (p ? ' data-player="' + p.steamid64 + '"' : "") + ">" +
    '<div class="top"><div class="ic">' + icon(ic) + '</div><div><div class="l">' + lbl + "</div>" +
    (p ? '<div class="n">' + esc(p.name) + "</div>" : '<div class="none">' + empty + "</div>") + "</div>" +
    (p ? '<div class="v display num">' + val + "<small>" + sub + "</small></div>" : "") + "</div>" +
    (p ? pills(p.history, 10) : "") + "</div>";
  $("forms").innerHTML =
    card("hot", "flame", "On fire · sequência atual", hot, hot && hot.streak, "vitórias seguidas", "ninguém com 2+ vitórias seguidas agora") +
    card("best", "up", "Maior sequência", best, best && best.best_streak, "vitórias seguidas", "sem sequências ainda") +
    card("cold", "down", "Na seca · sequência atual", cold, cold && -cold.streak, "derrotas seguidas", "ninguém com 2+ derrotas seguidas");
}

/* ---------- duplas e rivalidades ---------- */
function renderPairs() {
  const duos = S.duos || [], rivals = S.rivals || [];
  const minG = S.id === "all" ? 3 : 2;
  $("pairs-note").textContent = "mínimo de " + minG + " jogos juntos / contra";
  const duoHtml = duos.length ? duos.map((d, i) =>
    '<div class="duo-row" data-player="' + d.a + '"><span class="rk display num">' + (i + 1) + "</span>" +
    '<span class="stack"><span class="avatar">' + esc(initials(d.a_name)) + '</span><span class="avatar">' + esc(initials(d.b_name)) + "</span></span>" +
    '<span class="nm">' + playerLink(d.a, d.a_name) + "<span>+</span>" + playerLink(d.b, d.b_name) + "</span>" +
    '<span class="rec num">' + d.wins + "V " + (d.games - d.wins) + "D<small>" + fmt((d.wins / d.games) * 100, 0) + "% em " + plural(d.games, "jogo", "jogos") + "</small></span></div>"
  ).join("") : '<div class="none">Nenhuma dupla com ' + minG + "+ jogos juntos.</div>";
  const rivHtml = rivals.length ? rivals.map((r, i) => {
    const aLead = r.a_wins > r.b_wins, bLead = r.b_wins > r.a_wins;
    return '<div class="riv-row" data-player="' + r.a + '"><span class="rk display num">' + (i + 1) + "</span>" +
      '<span class="a">' + playerLink(r.a, r.a_name) + "</span>" +
      '<span class="vs display num"><span class="' + (aLead ? "lead" : "") + '">' + r.a_wins + '</span> × <span class="' + (bLead ? "lead" : "") + '">' + r.b_wins +
      "</span><small>" + plural(r.games, "confronto", "confrontos") + "</small></span>" +
      '<span class="b">' + playerLink(r.b, r.b_name) + "</span></div>";
  }).join("") : '<div class="none">Nenhum confronto com ' + minG + "+ jogos.</div>";
  $("pairs").innerHTML =
    '<div class="card"><h3 class="display">' + icon("users") + "Duplas que mais ganham<small>mesmo time</small></h3>" + duoHtml + "</div>" +
    '<div class="card"><h3 class="display">' + icon("swords") + "Rivalidades<small>times opostos</small></h3>" + rivHtml + "</div>";
}

/* ---------- scatter ADR × K/D ---------- */
let scatterList = [];
function renderScatter(list) {
  if (list) scatterList = list;
  list = scatterList;
  const box = $("scatter");
  const tip = $("tip");
  box.querySelectorAll("svg").forEach((s) => s.remove());
  if (!list.length) return;
  const W = Math.max(320, box.clientWidth);
  const H = W < 600 ? 320 : 400;
  const m = { l: 44, r: 16, t: 14, b: 40 };
  const adrs = list.map((p) => p.adr), kds = list.map((p) => p.kd);
  const x0 = Math.floor((Math.min(...adrs) - 4) / 10) * 10, x1 = Math.ceil((Math.max(...adrs) + 4) / 10) * 10;
  const y0 = 0, y1 = Math.ceil((Math.max(...kds) + 0.1) * 4) / 4;
  const sx = (v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
  const sy = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const sortedAdr = adrs.slice().sort((a, b) => a - b);
  const medAdr = sortedAdr[Math.floor(sortedAdr.length / 2)];
  const maxMaps = Math.max(...list.map((p) => p.matches));
  const rad = (p) => 4 + 7 * Math.sqrt(p.matches / maxMaps);

  let g = "";
  for (let x = x0; x <= x1; x += 10) {
    g += '<line x1="' + sx(x) + '" x2="' + sx(x) + '" y1="' + m.t + '" y2="' + (H - m.b) + '" stroke="var(--grid)" />';
    g += '<text class="tick" x="' + sx(x) + '" y="' + (H - m.b + 18) + '" text-anchor="middle">' + x + "</text>";
  }
  const ystep = y1 > 2 ? 0.5 : 0.25;
  for (let y = y0; y <= y1 + 1e-9; y += ystep) {
    g += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + sy(y) + '" y2="' + sy(y) + '" stroke="var(--grid)" />';
    g += '<text class="tick" x="' + (m.l - 8) + '" y="' + (sy(y) + 4) + '" text-anchor="end">' + fmt(y, 2) + "</text>";
  }
  g += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + (H - m.b) + '" y2="' + (H - m.b) + '" stroke="var(--baseline)" />';
  if (1 >= y0 && 1 <= y1) g += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + sy(1) + '" y2="' + sy(1) + '" stroke="var(--baseline)" stroke-dasharray="4 4" stroke-width="1.5" />';
  g += '<line x1="' + sx(medAdr) + '" x2="' + sx(medAdr) + '" y1="' + m.t + '" y2="' + (H - m.b) + '" stroke="var(--baseline)" stroke-dasharray="4 4" stroke-width="1.5" />';
  g += '<text class="quad" x="' + (sx(medAdr) + 8) + '" y="' + (m.t + 14) + '">Carregam o time ↗</text>';
  g += '<text class="quad" x="' + (m.l + 8) + '" y="' + (H - m.b - 8) + '">Em evolução</text>';
  g += '<text class="axis-title" x="' + (W - m.r) + '" y="' + (H - 4) + '" text-anchor="end">ADR (dano por round) →</text>';

  const q = state.q;
  const pts = list.slice().sort((a, b) => b.matches - a.matches).map((p) => {
    const match = !q || p.name.toLowerCase().includes(q);
    return '<circle class="pt' + (q && !match ? " dim" : "") + (q && match ? " hl" : "") + '" cx="' + sx(p.adr) + '" cy="' + sy(p.kd) + '" r="' + rad(p) + '" />';
  }).join("");

  // rótulos: top 5 do ranking (e quem bate na busca), no primeiro lugar livre
  const labeled = list.filter((p) => p.rank <= 5 || (q && p.name.toLowerCase().includes(q)));
  const placed = [];
  const hits = (b) => placed.some((o) => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y) ||
    list.some((p) => { const cx = sx(p.adr), cy = sy(p.kd), r = rad(p);
      return cx + r > b.x && cx - r < b.x + b.w && cy + r > b.y && cy - r < b.y + b.h; });
  const lbls = labeled.slice().sort((a, b) => a.rank - b.rank).map((p) => {
    const cx = sx(p.adr), cy = sy(p.kd), r = rad(p);
    const w = p.name.length * 6.6 + 4, h = 14;
    const spots = [
      { x: cx + r + 5, y: cy - h / 2, a: "start" },
      { x: cx - r - 5 - w, y: cy - h / 2, a: "end" },
      { x: cx - w / 2, y: cy - r - 4 - h, a: "middle" },
      { x: cx - w / 2, y: cy + r + 4, a: "middle" },
      { x: cx + r + 5, y: cy - h / 2 - 14, a: "start" },
      { x: cx - r - 5 - w, y: cy - h / 2 + 14, a: "end" },
    ].filter((b) => b.x >= m.l && b.x + w <= W - m.r && b.y >= m.t && b.y + h <= H - m.b);
    const spot = spots.find((b) => !hits({ x: b.x, y: b.y, w, h })) || spots[0];
    if (!spot) return "";
    placed.push({ x: spot.x, y: spot.y, w, h });
    const tx = spot.a === "start" ? spot.x : spot.a === "end" ? spot.x + w : spot.x + w / 2;
    return '<text class="lbl" x="' + tx + '" y="' + (spot.y + 11) + '" text-anchor="' + spot.a + '">' + esc(p.name) + "</text>";
  }).join("");

  const svg = '<svg viewBox="0 0 ' + W + " " + H + '" height="' + H + '" role="img" aria-label="Gráfico de dispersão: ADR por K/D de cada jogador">' +
    '<text class="axis-title" x="' + m.l + '" y="' + (m.t - 2) + '">K/D ↑</text>' + g + pts + lbls +
    '<circle id="hover-ring" r="0" fill="none" stroke="var(--text)" stroke-width="2" /></svg>';
  box.insertAdjacentHTML("afterbegin", svg);
  const svgEl = box.querySelector("svg");
  const ring = svgEl.querySelector("#hover-ring");
  let hover = null;
  const move = (ev) => {
    const rect = svgEl.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const py = ((ev.clientY - rect.top) / rect.height) * H;
    let bestP = null, bestD = 26 * 26;
    list.forEach((p) => {
      const d = (sx(p.adr) - px) ** 2 + (sy(p.kd) - py) ** 2;
      if (d < bestD) { bestD = d; bestP = p; }
    });
    hover = bestP;
    svgEl.style.cursor = bestP ? "pointer" : "default";
    if (!bestP) { tip.classList.remove("on"); ring.setAttribute("r", 0); return; }
    ring.setAttribute("cx", sx(bestP.adr)); ring.setAttribute("cy", sy(bestP.kd)); ring.setAttribute("r", rad(bestP) + 3);
    tip.innerHTML = '<div class="t">#' + bestP.rank + " " + esc(bestP.name) + "</div>" +
      [["Vitórias", bestP.wins + "V " + bestP.losses + "D"], ["MixScore", fmt(bestP.rating, 1)], ["K/D", fmt(bestP.kd, 2)], ["ADR", fmt(bestP.adr, 1)], ["Mapas", bestP.matches]]
        .map(([l, v]) => '<div class="r"><span>' + l + "</span><b>" + v + "</b></div>").join("");
    const bx = box.getBoundingClientRect();
    let left = ev.clientX - bx.left + 14, top = ev.clientY - bx.top + 14;
    if (left + 190 > bx.width) left = ev.clientX - bx.left - 190;
    if (top + 140 > bx.height) top = ev.clientY - bx.top - 140;
    tip.style.left = left + "px"; tip.style.top = top + "px";
    tip.classList.add("on");
  };
  svgEl.addEventListener("pointermove", move);
  svgEl.addEventListener("pointerleave", () => { hover = null; tip.classList.remove("on"); ring.setAttribute("r", 0); });
  svgEl.addEventListener("click", (ev) => { move(ev); if (hover) openPlayer(hover.steamid64); });
}
let rsz;
addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => renderScatter(), 120); });

/* ---------- tabela ---------- */
const COLUMNS = [
  { key: "rank", label: "#", cls: "rank left rankh" },
  { key: "name", label: "Jogador", cls: "left sticky" },
  { key: "wins", label: "Vitórias" },
  { key: "streak", label: "Forma" },
  { key: "rating", label: "MixScore" },
  { key: "kd", label: "K/D" },
  { key: "adr", label: "ADR" },
  { key: "hs_pct", label: "HS%" },
  { key: "kills", label: "K" },
  { key: "deaths", label: "D" },
  { key: "clutch_pct", label: "Clutch" },
  { key: "_go", label: "" },
];

function cell(p, key) {
  switch (key) {
    case "rank": return p.rank;
    case "name":
      return '<div class="pcell"><div class="avatar">' + esc(initials(p.name)) + '</div><div><div class="pname">' + esc(p.name) +
        ' <span class="tier ' + tierOf(p.rating) + '" title="Tier ' + tierOf(p.rating) + '">' + tierOf(p.rating) + '</span></div><div class="pmeta">' +
        plural(p.matches, "mapa", "mapas") + " · " + p.rounds + " rounds</div></div></div>";
    case "wins":
      return '<div class="scorecell"><div class="bar"><i style="width:' + (p.wins / MAX_WINS) * 100 + '%"></i></div><b>' + p.wins +
        '</b><span class="sub" style="min-width:58px;text-align:left">' + p.losses + "D · " + fmt(p.win_pct, 0) + "%</span></div>";
    case "streak": return pills(p.history, 5);
    case "rating": return '<span style="color:var(--text);font-weight:600">' + fmt(p.rating, 1) + "</span>";
    case "kd": {
      const up = p.kd >= 1;
      return '<span class="kd ' + (up ? "up" : "down") + '"><span class="tri">' + (up ? "▲" : "▼") + "</span>" + fmt(p.kd, 2) + "</span>";
    }
    case "adr": return '<span style="color:var(--text);font-weight:600">' + fmt(p.adr, 1) + "</span>";
    case "hs_pct": return fmt(p.hs_pct, 1) + "%";
    case "clutch_pct": return (p.clutch_att ? fmt(p.clutch_pct, 0) + "%" : "—") + '<span class="sub">' + p.clutch_won + "/" + p.clutch_att + "</span>";
    case "_go": return icon("chevr");
    default: return p[key];
  }
}

function renderTable(list) {
  const q = state.q;
  const rows = list.filter((p) => !q || p.name.toLowerCase().includes(q)).sort((a, b) => {
    const va = a[state.sortKey], vb = b[state.sortKey];
    if (typeof va === "string") return va.localeCompare(vb, "pt-BR") * state.sortDir;
    return (va - vb) * state.sortDir;
  });

  $("thead-row").innerHTML = COLUMNS.map((c) => {
    const cls = (c.cls || "").replace("rank ", "");
    if (c.key === "_go") return '<th class="' + cls + '"></th>';
    const sorted = c.key === state.sortKey;
    return '<th class="' + cls + '"' + (sorted ? ' aria-sort="' + (state.sortDir < 0 ? "descending" : "ascending") + '"' : "") +
      '><button type="button" data-k="' + c.key + '">' + c.label + (sorted ? '<span class="arrow">' + (state.sortDir < 0 ? "▼" : "▲") + "</span>" : "") + "</button></th>";
  }).join("");
  $("thead-row").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.k;
    if (state.sortKey === k) state.sortDir *= -1;
    else { state.sortKey = k; state.sortDir = k === "name" || k === "rank" ? 1 : -1; }
    renderTable(list);
  }));

  $("tbody").innerHTML = rows.length
    ? rows.map((p) =>
        '<tr class="row' + (p.rank <= 3 ? " r" + p.rank : "") + '" data-player="' + p.steamid64 + '">' +
        COLUMNS.map((c) => '<td class="' + (c.cls || "") + (c.key === "_go" ? " chev" : "") + '">' + cell(p, c.key) + "</td>").join("") +
        "</tr>").join("")
    : '<tr><td class="empty" colspan="' + COLUMNS.length + '">Nenhum jogador encontrado.</td></tr>';
  $("count").textContent = rows.length + " de " + list.length + " jogadores";
}

/* ---------- partidas ---------- */
function renderMaps() {
  $("maps").innerHTML = MAPS.map((m) => {
    const team = (name, score) => '<div class="team' + (name === m.winner ? " win" : "") + '"><span class="tn">' + esc(name) +
      (name === m.winner ? '<span class="wtag">venceu</span>' : "") + '</span><span class="ts display num">' + score + "</span></div>";
    return '<div class="card map" data-match="' + m.id + '" style="--mc:' + (MAP_COLORS[m.slug] || "var(--accent-mark)") + '">' +
      '<div class="mh mapimg" style="--img:url(assets/maps/' + m.slug + '.jpg);' + mapBg(m.slug) + '"><div class="mname display">' + esc(m.map) +
      '</div><div class="mdate">' + shortDate(m.date) + " · " + (m.score1 + m.score2) + " rounds</div></div>" +
      '<div class="mb">' + team(m.team1, m.score1) + team(m.team2, m.score2) +
      (m.mvp ? '<div class="mvp">' + icon("star") + "MVP <b>" + esc(m.mvp) + "</b> · " + m.mvp_kills + "/" + m.mvp_deaths + " · " + fmt(m.mvp_adr, 1) + " ADR</div>" : "") +
      "</div></div>";
  }).join("");
}

/* ---------- top mapas ---------- */
function mapPerf(players) {
  const perf = {};
  players.forEach((p) => p.history.forEach((h) => {
    const k = h.slug + "|" + p.steamid64;
    const r = perf[k] || (perf[k] = { slug: h.slug, sid: p.steamid64, name: p.name, games: 0, adr: 0, kills: 0, deaths: 0, wins: 0 });
    r.games += 1; r.adr += h.adr; r.kills += h.kills; r.deaths += h.deaths; r.wins += h.won ? 1 : 0;
  }));
  return Object.values(perf);
}
function renderTopMaps() {
  const by = {};
  MAPS.forEach((m) => {
    const s = by[m.slug] || (by[m.slug] = { slug: m.slug, map: m.map, games: 0, rounds: 0, closest: null, ot: 0 });
    s.games += 1;
    s.rounds += m.score1 + m.score2;
    if (m.score1 + m.score2 > 24) s.ot += 1;
    const diff = Math.abs(m.score1 - m.score2);
    if (!s.closest || diff < s.closest.diff || (diff === s.closest.diff && m.score1 + m.score2 > s.closest.total))
      s.closest = { id: m.id, diff, total: m.score1 + m.score2, a: Math.max(m.score1, m.score2), b: Math.min(m.score1, m.score2) };
  });
  const perf = mapPerf(ALL);
  const kingOf = (slug) => {
    const cands = perf.filter((r) => r.slug === slug);
    const pool = cands.filter((r) => r.games >= 2);
    const src = pool.length ? pool : cands;
    return src.reduce((a, b) => (!a || b.adr / b.games > a.adr / a.games ? b : a), null);
  };
  const stats = Object.values(by).sort((a, b) => b.games - a.games || b.rounds - a.rounds);
  const maxGames = Math.max(...stats.map((s) => s.games));
  $("topmaps").innerHTML =
    '<div class="tm head"><span>#</span><span>Mapa</span><span>Jogos</span><span class="avg">Média</span><span>Rei do mapa</span><span class="close">Mais disputado</span></div>' +
    stats.map((s, i) => {
      const k = kingOf(s.slug);
      return '<div class="tm" data-match="' + s.closest.id + '" style="--mc:' + (MAP_COLORS[s.slug] || "var(--accent-mark)") + '">' +
        '<span class="rk display num">' + (i + 1) + "</span>" +
        '<span class="mn display">' + thumb(s.slug) + esc(s.map) + "</span>" +
        '<span class="games"><span class="n display num">' + s.games + '</span><span class="bar"><i style="width:' + (s.games / maxGames) * 100 + '%"></i></span>' +
        '<span class="pct num">' + fmt((s.games / MAPS.length) * 100, 0) + "%</span></span>" +
        '<span class="avg num">' + fmt(s.rounds / s.games, 1) + "<small>rounds/jogo" + (s.ot ? " · " + plural(s.ot, "prorrogação", "prorrogações") : "") + "</small></span>" +
        (k ? '<span class="king">' + icon("star") + '<span style="min-width:0"><div class="kn">' + playerLink(k.sid, k.name) + '</div><div class="kd2">' +
          fmt(k.adr / k.games, 1) + " ADR · " + k.kills + "/" + k.deaths + " · " + plural(k.games, "jogo", "jogos") + "</div></span></span>" : "<span></span>") +
        '<span class="close num">' + s.closest.a + "–" + s.closest.b + "<small>" + (s.closest.diff <= 2 ? "no detalhe" : "diferença de " + s.closest.diff) + "</small></span>" +
        "</div>";
    }).join("");
}

/* ---------- perfil do jogador ---------- */
let lastFocus = null;
function lock() { document.body.classList.add("locked"); }
function unlockIfIdle() { if (!$("drawer").classList.contains("on") && !$("modal-wrap").classList.contains("on")) document.body.classList.remove("locked"); }

function openPlayer(sid) {
  const ranked = visiblePlayers();
  const p = ranked.find((x) => x.steamid64 === sid) || BYSID[sid] ||
    (SCOPES[0].players.find((x) => x.steamid64 === sid) && enrich(SCOPES[0].players.find((x) => x.steamid64 === sid)));
  if (!p) return;
  const scopeName = S.id === "all" ? "todas as noites" : S.label.toLowerCase();
  const rankTxt = p.rank ? "#" + p.rank + " no ranking" : "fora do filtro atual";
  const cells = [
    ["Vitórias", p.wins + "–" + p.losses, fmt(p.win_pct, 0) + "% de aproveitamento"],
    ["MixScore", fmt(p.rating, 1), "nota bruta " + fmt(p.raw_rating, 1)],
    ["K/D", fmt(p.kd, 2), p.kills + " kills · " + p.deaths + " mortes"],
    ["ADR", fmt(p.adr, 1), fmt(p.kpr, 2) + " kills por round"],
    ["Headshot", fmt(p.hs_pct, 0) + "%", Math.round(p.kills * p.hs_pct / 100) + " na cabeça"],
    ["Clutch", p.clutch_won + "/" + p.clutch_att, p.clutch_att ? fmt(p.clutch_pct, 0) + "% (1v1 e 1v2)" : "nenhum"],
    ["Multi-kills", p.multi_kills, p.k3 + " triplas · " + p.k4 + " quadras"],
    ["Aces", p.aces, p.assists + " assistências"],
  ];
  const rel = (lbl, r, fmtS) => r
    ? '<div class="rel"><div class="l">' + lbl + '</div><div class="n">' + playerLink(r.sid, r.name) + '</div><div class="s">' + fmtS(r) + "</div></div>"
    : '<div class="rel none"><div class="l">' + lbl + '</div><div class="n">—</div><div class="s">poucos jogos pra dizer</div></div>';
  const perMap = mapPerf([p]).sort((a, b) => b.games - a.games || b.wins - a.wins);
  const streakTxt = p.streak >= 2 ? "<b>" + p.streak + " vitórias</b> seguidas agora" :
    p.streak <= -2 ? "<b>" + -p.streak + " derrotas</b> seguidas agora" :
    p.streak === 1 ? "venceu o último mapa" : p.streak === -1 ? "perdeu o último mapa" : "";

  $("drawer").innerHTML =
    '<div class="dr-head"><div class="avatar">' + esc(initials(p.name)) + '</div><div style="min-width:0"><div class="nm">' + esc(p.name) +
    ' <span class="tier ' + tierOf(p.rating) + '">' + tierOf(p.rating) + '</span></div><div class="sub2">' + rankTxt + " · " + scopeName + " · " +
    plural(p.matches, "mapa", "mapas") + " · " + p.rounds + ' rounds</div></div><button class="x" type="button" id="dr-x" aria-label="Fechar">' + icon("x") + "</button></div>" +
    '<div class="dr-body">' +
    '<div class="dr-sec"><div class="dgrid">' + cells.map(([l, v, s]) =>
      '<div class="dcell"><div class="l">' + l + '</div><div class="v display num">' + v + '</div><div class="s">' + s + "</div></div>").join("") + "</div></div>" +
    '<div class="dr-sec"><h4>Forma</h4><div class="formline">' + pills(p.history, 10) + (streakTxt ? "<span>" + streakTxt + "</span>" : "") +
    "<span>melhor sequência: <b>" + plural(p.best_streak, "vitória", "vitórias") + "</b></span></div></div>" +
    '<div class="dr-sec"><h4>Parceiros e rivais</h4><div class="rels">' +
    rel("Melhor parceiro", p.partner, (r) => r.wins + " vitórias em " + plural(r.games, "jogo", "jogos") + " juntos") +
    rel("Freguês", p.fregues, (r) => "venceu " + r.wins + " de " + (r.wins + r.losses) + " contra") +
    rel("Carrasco", p.carrasco, (r) => "perdeu " + r.losses + " de " + (r.wins + r.losses) + " contra") +
    "</div></div>" +
    '<div class="dr-sec"><h4>Por mapa</h4><div class="permap"><div class="pm-row head"><span></span><span class="mn-h">Mapa</span><span>Jogos</span><span>V–D</span><span>ADR</span><span>K/D</span></div>' +
    perMap.map((r) => {
      const name = (p.history.find((h) => h.slug === r.slug) || {}).map || r.slug;
      return '<div class="pm-row">' + thumb(r.slug) + '<span class="mn">' + esc(name) + "</span><span>" + r.games + "</span><span>" + r.wins + "–" + (r.games - r.wins) +
        "</span><span>" + fmt(r.adr / r.games, 1) + "</span><span>" + fmt(r.kills / Math.max(r.deaths, 1), 2) + "</span></div>";
    }).join("") + "</div></div>" +
    '<div class="dr-sec"><h4>Partidas (' + p.history.length + ')</h4><div class="matches"><div class="mt-row head"><span></span><span>Mapa</span><span></span><span class="r">Placar</span><span class="r">K/D/A</span><span class="r">ADR</span><span></span></div>' +
    p.history.map((h) =>
      '<div class="mt-row" data-match="' + h.mid + '">' + thumb(h.slug) + '<span><span class="mn">' + esc(h.map) + '</span><div class="dt">' + shortDate(h.date) + "</div></span>" +
      '<span class="res ' + (h.won ? "w" : "l") + '">' + (h.won ? "V" : "D") + '</span><span class="r">' + h.score + "–" + h.opp + '</span><span class="r kda">' +
      h.kills + "/" + h.deaths + "/" + h.assists + '</span><span class="r">' + fmt(h.adr, 1) + "</span>" + icon("chevr") + "</div>").join("") +
    "</div></div></div>";

  $("drawer").scrollTop = 0;
  if (!$("drawer").classList.contains("on")) lastFocus = document.activeElement;
  $("drawer").classList.add("on");
  $("overlay").classList.add("on");
  lock();
  $("dr-x").addEventListener("click", closePlayer);
  $("dr-x").focus({ preventScroll: true });
}
function closePlayer() {
  $("drawer").classList.remove("on");
  $("overlay").classList.remove("on");
  unlockIfIdle();
  if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
}

/* ---------- placar da partida ---------- */
function openMatch(id) {
  const m = ALL_MAPS[id];
  if (!m) return;
  const w1 = m.winner === m.team1;
  const board = (name, score, rows, win) =>
    '<div class="board' + (win ? " win" : "") + '"><div class="bh">' + esc(name) + '<span class="tag' + (win ? "" : " l") + '">' + (win ? "venceu" : "perdeu") +
    '</span><span class="sc num">' + score + '</span></div><div class="scroll"><table><thead><tr><th class="pl">Jogador</th><th>K</th><th>D</th><th>A</th><th>+/−</th><th>ADR</th><th>HS%</th><th>3K+</th></tr></thead><tbody>' +
    rows.map((r) => {
      const diff = r.k - r.d;
      return '<tr><td class="pl">' + playerLink(r.sid, r.name) + (r.sid === m.mvp_sid ? icon("star") : "") + "</td><td>" + r.k + "</td><td>" + r.d + "</td><td>" + r.a +
        '</td><td style="color:' + (diff > 0 ? "var(--good)" : diff < 0 ? "var(--bad)" : "var(--text-3)") + '">' + (diff > 0 ? "+" : "") + diff +
        "</td><td>" + fmt(r.adr, 1) + "</td><td>" + r.hs + "%</td><td>" + r.mk + "</td></tr>";
    }).join("") + "</tbody></table></div></div>";
  const first = w1 ? ["team1", m.team1, m.score1] : ["team2", m.team2, m.score2];
  const second = w1 ? ["team2", m.team2, m.score2] : ["team1", m.team1, m.score1];
  $("modal").innerHTML =
    '<div class="banner mapimg" style="' + mapBg(m.slug) + '"><button class="x" type="button" id="md-x" aria-label="Fechar">' + icon("x") + "</button>" +
    '<div class="bm">' + shortDate(m.date) + " · " + (m.score1 + m.score2) + " rounds" + (m.mvp ? " · MVP " + esc(m.mvp) : "") + "</div>" +
    '<div class="bn display">' + esc(m.map) + "</div>" +
    '<div class="bscore"><span class="bt' + (w1 ? " win" : "") + '">' + esc(m.team1) + '</span><span class="bs display num"><span class="' + (w1 ? "w" : "lo") + '">' + m.score1 +
    '</span><i>:</i><span class="' + (w1 ? "lo" : "w") + '">' + m.score2 + '</span></span><span class="bt r' + (w1 ? "" : " win") + '">' + esc(m.team2) + "</span></div></div>" +
    '<div class="boards">' + board(first[1], first[2], m.board[first[0]], true) + board(second[1], second[2], m.board[second[0]], false) + "</div>";
  $("modal-wrap").classList.add("on");
  lock();
  $("md-x").addEventListener("click", closeMatch);
  $("md-x").focus({ preventScroll: true });
}
function closeMatch() {
  $("modal-wrap").classList.remove("on");
  unlockIfIdle();
}

/* ---------- cliques ---------- */
document.addEventListener("click", (e) => {
  const inModal = e.target.closest("#modal");
  if (e.target.id === "modal-wrap") { closeMatch(); return; }
  if (e.target.id === "overlay") { closePlayer(); return; }
  const pl = e.target.closest("[data-player]");
  const mt = e.target.closest("[data-match]");
  // o nome clicado vence o card (ex: jogador dentro de uma linha clicável)
  if (pl && (!mt || mt.contains(pl))) {
    if (inModal) closeMatch();
    openPlayer(pl.dataset.player);
    return;
  }
  if (mt) openMatch(mt.dataset.match);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($("modal-wrap").classList.contains("on")) closeMatch();
  else if ($("drawer").classList.contains("on")) closePlayer();
});

/* ---------- montar times ---------- */
// força = MixScore (já com peso de confiança) + 20 × (aproveitamento ajustado − 50%)
// aproveitamento ajustado = (vitórias + 2,5) / (mapas + 5): começa em 50% e vai pro real conforme joga
const BD_BASE = SCOPES[0].players.map((p) => ({
  sid: p.steamid64, name: p.name, matches: p.matches,
  strength: p.rating + 20 * ((p.wins + 2.5) / (p.matches + 5) - 0.5),
}));
const BD_MEDIAN = (() => { const v = BD_BASE.map((p) => p.strength).sort((a, b) => a - b); return v.length ? v[Math.floor(v.length / 2)] : 55; })();
const bd = { sel: new Set(), guests: [], result: null, q: "" };
try {
  const saved = JSON.parse(localStorage.getItem("mix-teams") || "null");
  if (saved) { bd.guests = saved.guests || []; (saved.sel || []).forEach((s) => bd.sel.add(s)); }
} catch (e) {}
const bdSave = () => { try { localStorage.setItem("mix-teams", JSON.stringify({ sel: Array.from(bd.sel), guests: bd.guests })); } catch (e) {} };
const bdPool = () => BD_BASE.concat(bd.guests.map((g) => ({ sid: "g:" + g, name: g, matches: 0, strength: BD_MEDIAN, guest: true })));
const bdSelected = () => bdPool().filter((p) => bd.sel.has(p.sid));

function bdSplits(players) {
  // todas as divisões em dois times (o 1º jogador fica sempre no time A pra não repetir espelhado)
  const n = players.length, k = Math.floor(n / 2), out = [];
  const rec = (start, pick) => {
    if (pick.length === k) {
      const a = pick.map((i) => players[i]);
      const b = players.filter((_, i) => !pick.includes(i));
      const avg = (t) => t.reduce((s, p) => s + p.strength, 0) / t.length;
      out.push({ a, b, sa: avg(a), sb: avg(b), diff: Math.abs(avg(a) - avg(b)) });
      return;
    }
    for (let i = start; i < n; i++) rec(i + 1, pick.concat(i));
  };
  rec(1, [0]);
  return out.sort((x, y) => x.diff - y.diff);
}

function bdRun(random) {
  const players = bdSelected();
  if (players.length < 2 || players.length % 2 || players.length > 16) return;
  const splits = bdSplits(players);
  let pick = splits[0];
  if (random) {
    // sorteia entre as divisões quase tão boas quanto a melhor
    const good = splits.filter((s) => s.diff <= splits[0].diff + 1).slice(0, 20);
    const key = (s) => s.a.map((p) => p.sid).sort().join();
    const others = good.filter((s) => !bd.result || key(s) !== key(bd.result));
    const pool = others.length ? others : good;
    pick = pool[Math.floor(Math.random() * pool.length)];
  }
  // time A = o do jogador mais forte, pra ordem ficar estável
  const top = (t) => Math.max(...t.map((p) => p.strength));
  if (top(pick.b) > top(pick.a)) pick = { a: pick.b, b: pick.a, sa: pick.sb, sb: pick.sa, diff: pick.diff };
  bd.result = pick;
  renderBuilderResult();
}

function renderBuilderResult() {
  const r = bd.result;
  const box = $("bd-result");
  if (!r) { box.innerHTML = ""; return; }
  const sort = (t) => t.slice().sort((x, y) => y.strength - x.strength);
  const A = sort(r.a), B = sort(r.b);
  const pa = (r.sa / (r.sa + r.sb)) * 100;
  const verdict = r.diff < 1 ? "muito equilibrado" : r.diff < 2.5 ? "equilibrado" : r.diff < 5 ? "um pouco desequilibrado" : "desequilibrado";
  const card = (cls, t, avg) => '<div class="team-card ' + cls + '"><div class="th"><span class="nm display">Time ' + esc(t[0].name) + "</span>" +
    '<span class="st">força média <b class="num">' + fmt(avg, 1) + "</b></span></div>" +
    t.map((p, i) => '<div class="tp"><div class="avatar">' + esc(initials(p.name)) + '</div><span class="n">' +
      (p.guest ? esc(p.name) + ' <span class="s">(convidado)</span>' : playerLink(p.sid, p.name)) + "</span>" +
      (i === 0 ? '<span class="cap">capitão</span>' : "") + '<span class="s">' + fmt(p.strength, 1) + "</span></div>").join("") + "</div>";
  box.innerHTML =
    '<div class="balance"><span class="side display num">' + fmt(pa, 1) + '%</span><div class="meter" title="parcela da força total">' +
    '<i class="a" style="width:' + pa + '%"></i><i class="b" style="width:' + (100 - pa) + '%"></i></div><span class="side display num">' + fmt(100 - pa, 1) + "%</span></div>" +
    '<div class="verdict"><b>' + verdict + "</b> · diferença de " + fmt(r.diff, 1) + " ponto" + (r.diff >= 1.95 ? "s" : "") + " de força média</div>" +
    '<div class="teams">' + card("a", A, r.sa) + card("b", B, r.sb) + "</div>";
}

function renderBuilder() {
  const pool = bdPool().filter((p) => !bd.q || p.name.toLowerCase().includes(bd.q))
    .sort((a, b) => (bd.sel.has(b.sid) - bd.sel.has(a.sid)) || (b.guest ? 1 : 0) - (a.guest ? 1 : 0) || b.matches - a.matches || a.name.localeCompare(b.name, "pt-BR"));
  $("bd-chips").innerHTML = pool.map((p) =>
    '<button type="button" class="chip' + (p.guest ? " guest" : "") + '" data-sid="' + esc(p.sid) + '" aria-pressed="' + bd.sel.has(p.sid) + '">' +
    '<span class="avatar">' + esc(initials(p.name)) + "</span>" + esc(p.name) + " <small>" + fmt(p.strength, 0) + "</small></button>").join("") ||
    '<span class="bd-msg">Ninguém com esse nome.</span>';
  const n = bdSelected().length;
  $("bd-count").innerHTML = "<b>" + n + "</b> " + (n === 1 ? "selecionado" : "selecionados");
  const ok = n >= 2 && n % 2 === 0 && n <= 16;
  $("bd-balance").disabled = !ok;
  $("bd-shuffle").disabled = !ok;
  $("bd-copy").disabled = !bd.result;
  $("bd-hint").textContent = n === 0 ? "clique nos jogadores que vão jogar" : n % 2 ? "número ímpar: falta 1 jogador" :
    n > 16 ? "máximo de 16 jogadores" : n === 10 ? "5x5 pronto pra equilibrar" : n / 2 + "x" + n / 2;
}

$("bd-chips").addEventListener("click", (e) => {
  const c = e.target.closest(".chip");
  if (!c) return;
  const sid = c.dataset.sid;
  if (bd.sel.has(sid)) bd.sel.delete(sid); else bd.sel.add(sid);
  bd.result = null;
  bdSave(); renderBuilder(); renderBuilderResult();
});
$("bd-search").addEventListener("input", (e) => { bd.q = e.target.value.trim().toLowerCase(); renderBuilder(); });
const bdAddGuest = () => {
  const name = $("bd-guest").value.trim();
  if (!name || bd.guests.includes(name)) return;
  bd.guests.push(name);
  bd.sel.add("g:" + name);
  $("bd-guest").value = "";
  bd.result = null;
  bdSave(); renderBuilder(); renderBuilderResult();
};
$("bd-add").addEventListener("click", bdAddGuest);
$("bd-guest").addEventListener("keydown", (e) => { if (e.key === "Enter") bdAddGuest(); });
$("bd-balance").addEventListener("click", () => { bdRun(false); renderBuilder(); });
$("bd-shuffle").addEventListener("click", () => { bdRun(true); renderBuilder(); });
$("bd-clear").addEventListener("click", () => { bd.sel.clear(); bd.guests = []; bd.result = null; bdSave(); renderBuilder(); renderBuilderResult(); });
function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 1800);
}
$("bd-copy").addEventListener("click", () => {
  const r = bd.result;
  if (!r) return;
  const NL = String.fromCharCode(10);
  const line = (t, avg) => "Time " + t[0].name + " (força " + fmt(avg, 1) + "): " + t.map((p) => p.name).join(", ");
  const sort = (t) => t.slice().sort((x, y) => y.strength - x.strength);
  const text = line(sort(r.a), r.sa) + NL + line(sort(r.b), r.sb);
  const done = () => toast("Times copiados!");
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => prompt("Copie os times:", text));
  else prompt("Copie os times:", text);
});
renderBuilder();

/* ---------- rodapé ---------- */
$("footer").innerHTML =
  "<b>Como ler.</b> Só entram mapas finalizados em que os dois times estavam completos; espectadores são ignorados. " +
  "Uma <b>noite</b> junta os mapas jogados sem intervalo de mais de 4h (mapa de madrugada conta pra noite anterior). " +
  "<b>Ranking</b> ordenado por vitórias; empate é decidido pelo aproveitamento (% de vitórias) e depois pelo MixScore. " +
  "<b>MixScore</b> combina kills por round, ADR, HS%, clutch% e K/D numa nota só (a <b>nota bruta</b>) e depois aplica um peso de confiança: " +
  "quem jogou poucos rounds fica puxado pra média e vai ganhando a própria nota conforme joga " +
  "(com " + PAYLOAD.confidence + " rounds, fica no meio do caminho). " +
  "<b>Tiers</b>: S ≥ 72 · A ≥ 65 · B ≥ 58 · C ≥ 50 · D abaixo disso. " +
  "<b>Freguês</b> é o adversário que o jogador mais venceu; <b>carrasco</b>, o que mais venceu ele (mínimo de 2 confrontos). " +
  "Em todas as noites, jogadores com menos de " + PAYLOAD.minRounds + " rounds não aparecem. " +
  "Imagens dos mapas: ghostcap-gaming/cs2-map-images.";

/* ---------- tudo ---------- */
function renderAll() {
  const list = visiblePlayers();
  renderSeg();
  renderPodium(list);
  renderAwards(list);
  renderForms(list);
  renderScatter(list);
  renderTable(list);
}
function renderScope() {
  renderHeader();
  renderLastNight();
  renderTopMaps();
  renderPairs();
  renderMaps();
  renderAll();
}
$("search").addEventListener("input", (e) => {
  state.q = e.target.value.trim().toLowerCase();
  const list = visiblePlayers();
  renderTable(list);
  renderScatter(list);
});
setScope("all");
</script>
</body>
</html>
"""


def render_html(scopes: list[dict], db_paths: list[Path], team_size: int, min_rounds: int, confidence: int) -> str:
    payload = {
        "scopes": scopes,
        "sources": " + ".join(p.name for p in db_paths),
        "teamSize": team_size,
        "minRounds": min_rounds,
        "confidence": confidence,
    }
    # "</" escapado pra um nome de jogador não fechar a tag <script>
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("@@PAYLOAD_JSON@@", data)


def main():
    ap = argparse.ArgumentParser(description="Gera o ranking HTML do mix a partir do banco do MatchZy.")
    ap.add_argument(
        "--db",
        nargs="+",
        help="Caminho pro matchzy.db (se omitido, tenta achar sozinho). "
        "Aceita vários bancos pra consolidar: --db antigo.db novo.db",
    )
    ap.add_argument("--out", default="index.html", help="Arquivo HTML de saída (padrão: index.html).")
    ap.add_argument(
        "--min-rounds",
        type=int,
        default=10,
        help="Ignora jogadores com menos rounds que isso no total (padrão: 10, evita ruído de quem entrou só de visita).",
    )
    ap.add_argument(
        "--team-size",
        type=int,
        default=5,
        help="Mínimo de jogadores em cada time pra partida contar (padrão: 5).",
    )
    ap.add_argument(
        "--confianca",
        type=int,
        default=100,
        help="Rounds de 'peso de confiança' do MixScore: quem jogou menos que isso fica mais "
        "perto da média do mix (padrão: 100, ~5 mapas; 0 desliga).",
    )
    args = ap.parse_args()

    db_paths = find_dbs(args.db)
    all_maps = load_maps(db_paths, args.team_size)
    everything = build_scope(all_maps, args.min_rounds, args.confianca, min_pair_games=3)

    if not everything["players"]:
        sys.exit(
            "Nenhum jogador encontrado (ou todos abaixo do --min-rounds). "
            "Confira se já foram jogadas partidas completas pelo MatchZy."
        )

    nights = split_nights(all_maps)
    dates = [n[0]["start_time"] for n in nights]
    period = f"{night_label(nights[0])} a {dates[-1][8:10]}/{dates[-1][5:7]}" if nights else ""
    scopes = [{"id": "all", "label": "Todas as noites", "period": period, **everything}]
    # noites da mais recente pra mais antiga; o MixScore de cada noite usa só os mapas dela
    for night in reversed(nights):
        scope = build_scope(night, 1, args.confianca, min_pair_games=2)
        if scope["players"]:
            scopes.append({"id": "n" + night[0]["start_time"][:10], "label": "Noite de " + night_label(night), "period": night_label(night), **scope})

    html = render_html(scopes, db_paths, args.team_size, args.min_rounds, args.confianca)
    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(
        f"Ranking gerado: {out_path.resolve()}  "
        f"({len(everything['players'])} jogadores, {len(everything['maps'])} mapas, {len(nights)} noites)"
    )


if __name__ == "__main__":
    main()
