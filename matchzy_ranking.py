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


def map_label(raw: str) -> str:
    name = raw[3:] if raw.startswith(("de_", "cs_")) else raw
    return name.replace("_", " ").title()


def fetch_maps(maps: list[dict], current_names: dict[str, str]) -> list[dict]:
    result = []
    for m in maps:
        # MVP do mapa = mais kills (desempate por dano)
        mvp = max(m["players"], key=lambda r: (r["kills"], r["damage"]), default=None)
        result.append(
            {
                "map": map_label(m["mapname"]),
                "date": (m["start_time"] or "")[:16],
                "team1": team_label(m["team1_name"]) if m["team1_name"] else "Time 1",
                "team2": team_label(m["team2_name"]) if m["team2_name"] else "Time 2",
                "score1": m["team1_score"],
                "score2": m["team2_score"],
                "winner": team_label(m["winner"]),
                # nome mais recente do jogador, igual ao da tabela
                "mvp": current_names.get(str(mvp["steamid64"]), mvp["name"]) if mvp else None,
                "mvp_kills": mvp["kills"] if mvp else 0,
                "mvp_deaths": mvp["deaths"] if mvp else 0,
                "mvp_adr": round(mvp["damage"] / max(m["rounds"], 1), 1) if mvp else 0,
            }
        )
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


HTML_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ranking do Mix</title>
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

  footer { margin: 56px 0 48px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; line-height: 1.7; }
  footer b { color: var(--text-2); font-weight: 600; }
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
    <span class="lbl">Mostrar quem jogou</span>
    <div class="seg" id="seg" role="group" aria-label="Mínimo de mapas"></div>
    <div class="search-wrap">
      <svg class="i" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input class="search" id="search" type="search" placeholder="Buscar jogador" aria-label="Buscar jogador" />
    </div>
  </div>
</div>

<main class="wrap">
  <section class="block">
    <div class="sec-head"><h2 class="display">Pódio</h2><span class="note" id="podium-note"></span></div>
    <div class="podium" id="podium"></div>
  </section>

  <section class="block">
    <div class="sec-head"><h2 class="display">Destaques</h2><span class="note" id="awards-note"></span></div>
    <div class="awards" id="awards"></div>
  </section>

  <section class="block">
    <div class="sec-head">
      <h2 class="display">Top mapas <small>os mais jogados do mix</small></h2>
      <span class="note">rei do mapa = maior ADR médio com 2+ jogos no mapa</span>
    </div>
    <div class="card topmaps" id="topmaps"></div>
  </section>

  <section class="block">
    <div class="sec-head">
      <h2 class="display">Dano × K/D <small>cada ponto é um jogador · passe o mouse</small></h2>
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
      <span class="note">clique num jogador pra ver o histórico</span>
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
    <div class="sec-head"><h2 class="display">Mapas jogados <small id="maps-count"></small></h2></div>
    <div class="maps" id="maps"></div>
  </section>

  <footer id="footer"></footer>
</main>

<script>
const PAYLOAD = @@PAYLOAD_JSON@@;
const ALL = PAYLOAD.players;
const MAPS = PAYLOAD.maps;

const ICONS = {
  trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
  flame: '<path d="M12 3c1 4 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 2.5-5 .3 2 1.5 3 2.5 3 0-3-1-5 0-8z"/>',
  skull: '<path d="M12 3a8 8 0 0 0-5 14.2V20h10v-2.8A8 8 0 0 0 12 3z"/><circle cx="9" cy="11" r="1.5"/><circle cx="15" cy="11" r="1.5"/><path d="M10 20v-2M14 20v-2"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  medal: '<circle cx="12" cy="15" r="6"/><path d="M8.5 10.2L6 3h4l2 4 2-4h4l-2.5 7.2"/>',
};
const icon = (n) => '<svg class="i" viewBox="0 0 24 24">' + ICONS[n] + '</svg>';
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

/* ---------- cabeçalho ---------- */
const totalRounds = MAPS.reduce((s, m) => s + m.score1 + m.score2, 0);
const totalKills = ALL.reduce((s, p) => s + p.kills, 0);
const totalHs = ALL.reduce((s, p) => s + p.kills * p.hs_pct / 100, 0);
$("hero-sub").innerHTML =
  "<b>" + esc(PAYLOAD.period || "") + "</b> · só mapas finalizados " + PAYLOAD.teamSize + "x" + PAYLOAD.teamSize +
  " · fonte: " + esc(PAYLOAD.sources);
$("kpis").innerHTML = [
  ["Mapas", fmt(MAPS.length)],
  ["Rounds", fmt(totalRounds)],
  ["Jogadores", fmt(ALL.length)],
  ["Kills", fmt(totalKills)],
  ["Headshot", fmt(totalKills ? (totalHs / totalKills) * 100 : 0, 1) + "%"],
].map(([l, v]) => '<div class="kpi"><div class="l">' + l + '</div><div class="v display num">' + v + "</div></div>").join("");
$("maps-count").textContent = plural(MAPS.length, "mapa", "mapas");
$("footer").innerHTML =
  "<b>Como ler.</b> Só entram mapas finalizados em que os dois times estavam completos; espectadores são ignorados. " +
  "<b>Ranking</b> ordenado por vitórias; empate é decidido pelo aproveitamento (% de vitórias) e depois pelo MixScore. " +
  "<b>MixScore</b> combina kills por round, ADR, HS%, clutch% e K/D numa nota só (a <b>nota bruta</b>) e depois aplica um peso de confiança: " +
  "quem jogou poucos rounds fica puxado pra média do mix e vai ganhando a própria nota conforme joga " +
  "(com " + PAYLOAD.confidence + " rounds, fica no meio do caminho). Assim ninguém chega ao topo com 2 ou 3 mapas de sorte. " +
  "A nota bruta aparece no histórico de cada jogador e no gráfico. " +
  "<b>Tiers</b>: S ≥ 72 · A ≥ 65 · B ≥ 58 · C ≥ 50 · D abaixo disso. " +
  "Jogadores com menos de " + PAYLOAD.minRounds + " rounds não aparecem.";

/* ---------- estado ---------- */
const state = { minMaps: 1, q: "", sortKey: "rank", sortDir: 1, open: null };
const MAX_WINS = Math.max(...ALL.map((p) => p.wins), 1);
const FILTERS = [1, 3, 5, 10].filter((n) => n === 1 || ALL.filter((p) => p.matches >= n).length >= 3);

function visiblePlayers() {
  return ALL.filter((p) => p.matches >= state.minMaps)
    .slice()
    .sort((a, b) => b.wins - a.wins || b.win_pct - a.win_pct || b.rating - a.rating)
    .map((p, i) => Object.assign({}, p, { rank: i + 1 }));
}

function renderSeg() {
  $("seg").innerHTML = FILTERS.map((n) =>
    '<button type="button" data-n="' + n + '" aria-pressed="' + (state.minMaps === n) + '">' +
    (n === 1 ? "Todos" : n + "+ mapas") + "</button>").join("");
  $("seg").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { state.minMaps = Number(b.dataset.n); state.open = null; renderAll(); }));
}

/* ---------- pódio ---------- */
function renderPodium(list) {
  const top = list.slice(0, 3);
  $("podium-note").textContent = state.minMaps > 1 ? "entre quem jogou " + state.minMaps + "+ mapas" : "";
  const order = [1, 0, 2];
  $("podium").innerHTML = order.map((i) => {
    const p = top[i];
    if (!p) return "<div></div>";
    const stats = [["MixScore", fmt(p.rating, 1)], ["K/D", fmt(p.kd, 2)], ["ADR", fmt(p.adr, 1)], ["HS", fmt(p.hs_pct, 0) + "%"]];
    return '<div class="card pod p' + (i + 1) + '">' +
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
  // destaques só entre quem já jogou o suficiente (mesmo peso de confiança do MixScore)
  const pool = list.filter((p) => p.rounds >= PAYLOAD.confidence);
  const src = pool.length >= 3 ? pool : list;
  $("awards-note").textContent = src === pool ? "entre quem jogou " + PAYLOAD.confidence + "+ rounds" : "";
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
    return '<div class="card award"><div class="ic">' + icon(d.ic) + '</div><div class="body">' +
      '<div class="l">' + d.l + '</div><div class="pname">' + esc(p.name) + '</div><div class="hint">' + d.h(p) + "</div></div>" +
      '<div class="v display num">' + d.v(p) + "</div></div>";
  }).join("");
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
    return '<circle class="pt' + (q && !match ? " dim" : "") + (q && match ? " hl" : "") + '" data-id="' + p.steamid64 + '" cx="' + sx(p.adr) + '" cy="' + sy(p.kd) + '" r="' + rad(p) + '" />';
  }).join("");

  // rótulos só pros 5 primeiros do ranking (e pra quem bate na busca)
  const labeled = list.filter((p) => p.rank <= 5 || (q && p.name.toLowerCase().includes(q)));
  // posiciona cada rótulo no primeiro lugar livre (direita, esquerda, acima, abaixo)
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

  const move = (ev) => {
    const rect = svgEl.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const py = ((ev.clientY - rect.top) / rect.height) * H;
    let bestP = null, bestD = 26 * 26;
    list.forEach((p) => {
      const d = (sx(p.adr) - px) ** 2 + (sy(p.kd) - py) ** 2;
      if (d < bestD) { bestD = d; bestP = p; }
    });
    if (!bestP) { tip.classList.remove("on"); ring.setAttribute("r", 0); return; }
    ring.setAttribute("cx", sx(bestP.adr)); ring.setAttribute("cy", sy(bestP.kd)); ring.setAttribute("r", rad(bestP) + 3);
    tip.innerHTML = '<div class="t">#' + bestP.rank + " " + esc(bestP.name) + "</div>" +
      [["MixScore", fmt(bestP.rating, 1)], ["Nota bruta", fmt(bestP.raw_rating, 1)], ["K/D", fmt(bestP.kd, 2)], ["ADR", fmt(bestP.adr, 1)], ["Mapas", bestP.matches + " (" + bestP.wins + "V " + bestP.losses + "D)"]]
        .map(([l, v]) => '<div class="r"><span>' + l + "</span><b>" + v + "</b></div>").join("");
    const bx = box.getBoundingClientRect();
    let left = ev.clientX - bx.left + 14, top = ev.clientY - bx.top + 14;
    if (left + 190 > bx.width) left = ev.clientX - bx.left - 190;
    if (top + 120 > bx.height) top = ev.clientY - bx.top - 120;
    tip.style.left = left + "px"; tip.style.top = top + "px";
    tip.classList.add("on");
  };
  svgEl.addEventListener("pointermove", move);
  svgEl.addEventListener("pointerleave", () => { tip.classList.remove("on"); ring.setAttribute("r", 0); });
}
let rsz;
addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => renderScatter(), 120); });

/* ---------- tabela ---------- */
const COLUMNS = [
  { key: "rank", label: "#", cls: "rank left rankh" },
  { key: "name", label: "Jogador", cls: "left sticky" },
  { key: "wins", label: "Vitórias" },
  { key: "rating", label: "MixScore" },
  { key: "kd", label: "K/D" },
  { key: "adr", label: "ADR" },
  { key: "hs_pct", label: "HS%" },
  { key: "kills", label: "K" },
  { key: "deaths", label: "D" },
  { key: "assists", label: "A" },
  { key: "multi_kills", label: "Multi" },
  { key: "clutch_pct", label: "Clutch" },
  { key: "_chev", label: "" },
];

function cell(p, key, maxRating) {
  switch (key) {
    case "rank": return p.rank;
    case "name":
      return '<div class="pcell"><div class="avatar">' + esc(initials(p.name)) + '</div><div><div class="pname">' + esc(p.name) +
        ' <span class="tier ' + tierOf(p.rating) + '" title="Tier ' + tierOf(p.rating) + '">' + tierOf(p.rating) + '</span></div><div class="pmeta">' +
        plural(p.matches, "mapa", "mapas") + " · " + p.rounds + " rounds</div></div></div>";
    case "rating":
      return '<span style="color:var(--text);font-weight:600">' + fmt(p.rating, 1) + "</span>";
    case "wins":
      return '<div class="scorecell"><div class="bar"><i style="width:' + (p.wins / MAX_WINS) * 100 + '%"></i></div><b>' + p.wins +
        '</b><span class="sub" style="min-width:58px;text-align:left">' + p.losses + "D · " + fmt(p.win_pct, 0) + "%</span></div>";
    case "kd": {
      const up = p.kd >= 1;
      return '<span class="kd ' + (up ? "up" : "down") + '"><span class="tri">' + (up ? "▲" : "▼") + "</span>" + fmt(p.kd, 2) + "</span>";
    }
    case "adr": return '<span style="color:var(--text);font-weight:600">' + fmt(p.adr, 1) + "</span>";
    case "hs_pct": return fmt(p.hs_pct, 1) + "%";
    case "clutch_pct": return (p.clutch_att ? fmt(p.clutch_pct, 0) + "%" : "—") + '<span class="sub">' + p.clutch_won + "/" + p.clutch_att + "</span>";
    case "_chev": return icon("chev");
    default: return p[key];
  }
}

function detailRow(p) {
  const ds = [
    ["MixScore", fmt(p.rating, 1)], ["Nota bruta", fmt(p.raw_rating, 1)],
    ["K/round", fmt(p.kpr, 2)], ["Assists", p.assists],
    ["Triplas", p.k3], ["Quadras", p.k4],
    ["Aces", p.aces], ["Clutches", p.clutch_won + "/" + p.clutch_att],
  ];
  const hist = p.history.map((h) =>
    '<tr><td class="left">' + shortDate(h.date) + '</td><td class="left" style="color:var(--text)">' + esc(h.map) + "</td>" +
    '<td><span class="res ' + (h.won ? "w" : "l") + '">' + (h.won ? "V" : "D") + "</span></td>" +
    "<td>" + h.score + "–" + h.opp + '</td><td style="color:var(--text)">' + h.kills + "/" + h.deaths + "/" + h.assists + "</td>" +
    "<td>" + fmt(h.adr, 1) + "</td><td>" + h.hs_pct + "%</td></tr>").join("");
  return '<tr class="detail"><td colspan="' + COLUMNS.length + '"><div class="detail-inner">' +
    '<div class="dstats">' + ds.map(([l, v]) => '<div class="dstat"><div class="l">' + l + '</div><div class="v display num">' + v + "</div></div>").join("") + "</div>" +
    '<div class="hist"><table><thead><tr><th class="left">Data</th><th class="left">Mapa</th><th>Res.</th><th>Placar</th><th>K/D/A</th><th>ADR</th><th>HS</th></tr></thead><tbody>' +
    hist + "</tbody></table></div></div></td></tr>";
}

function renderTable(list) {
  const q = state.q;
  const rows = list.filter((p) => !q || p.name.toLowerCase().includes(q)).sort((a, b) => {
    const va = a[state.sortKey], vb = b[state.sortKey];
    if (typeof va === "string") return va.localeCompare(vb, "pt-BR") * state.sortDir;
    return (va - vb) * state.sortDir;
  });
  const maxRating = Math.max(...ALL.map((p) => p.rating), 1);

  $("thead-row").innerHTML = COLUMNS.map((c) => {
    const cls = (c.cls || "").replace("rank ", "");
    if (c.key === "_chev") return '<th class="' + cls + '"></th>';
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

  if (!rows.length) {
    $("tbody").innerHTML = '<tr><td class="empty" colspan="' + COLUMNS.length + '">Nenhum jogador encontrado.</td></tr>';
  } else {
    $("tbody").innerHTML = rows.map((p) => {
      const open = state.open === p.steamid64;
      return '<tr class="row' + (p.rank <= 3 ? " r" + p.rank : "") + (open ? " open" : "") + '" data-id="' + p.steamid64 + '" aria-expanded="' + open + '">' +
        COLUMNS.map((c) => '<td class="' + (c.cls || "") + (c.key === "_chev" ? " chev" : "") + '">' + cell(p, c.key, maxRating) + "</td>").join("") +
        "</tr>" + (open ? detailRow(p) : "");
    }).join("");
  }
  $("tbody").querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => {
    state.open = state.open === tr.dataset.id ? null : tr.dataset.id;
    renderTable(list);
  }));
  $("count").textContent = rows.length + " de " + list.length + " jogadores";
}

/* ---------- mapas ---------- */
const MAP_COLORS = {
  dust2: "#c8a165", mirage: "#d9965b", inferno: "#c7503b", nuke: "#4f86c6", ancient: "#4f8f5f",
  anubis: "#3aa0a0", train: "#8a8f98", cache: "#7d9a5a", overpass: "#6aa0c8", vertigo: "#9b7fd1",
};
function renderMaps() {
  $("maps").innerHTML = MAPS.map((m) => {
    const c = MAP_COLORS[m.map.toLowerCase()] || "var(--accent-mark)";
    const team = (name, score) => '<div class="team' + (name === m.winner ? " win" : "") + '"><span class="tn">' + esc(name) +
      (name === m.winner ? '<span class="wtag">venceu</span>' : "") + '</span><span class="ts display num">' + score + "</span></div>";
    return '<div class="card map" style="--mc:' + c + '"><div class="mh"><div class="mname display">' + esc(m.map) +
      '</div><div class="mdate">' + shortDate(m.date) + " · " + (m.score1 + m.score2) + " rounds</div></div>" +
      '<div class="mb">' + team(m.team1, m.score1) + team(m.team2, m.score2) +
      (m.mvp ? '<div class="mvp">' + icon("star") + "MVP <b>" + esc(m.mvp) + "</b> · " + m.mvp_kills + "/" + m.mvp_deaths + " · " + fmt(m.mvp_adr, 1) + " ADR</div>" : "") +
      "</div></div>";
  }).join("");
}

/* ---------- top mapas ---------- */
function renderTopMaps() {
  const by = {};
  MAPS.forEach((m) => {
    const s = by[m.map] || (by[m.map] = { map: m.map, games: 0, rounds: 0, closest: null, ot: 0 });
    s.games += 1;
    s.rounds += m.score1 + m.score2;
    if (m.score1 + m.score2 > 24) s.ot += 1;
    const diff = Math.abs(m.score1 - m.score2);
    if (!s.closest || diff < s.closest.diff || (diff === s.closest.diff && m.score1 + m.score2 > s.closest.total))
      s.closest = { diff, total: m.score1 + m.score2, a: Math.max(m.score1, m.score2), b: Math.min(m.score1, m.score2) };
  });
  // desempenho de cada jogador em cada mapa, a partir do histórico
  const perf = {};
  ALL.forEach((p) => p.history.forEach((h) => {
    const k = h.map + "|" + p.steamid64;
    const r = perf[k] || (perf[k] = { map: h.map, name: p.name, games: 0, adr: 0, kills: 0, deaths: 0, wins: 0 });
    r.games += 1; r.adr += h.adr; r.kills += h.kills; r.deaths += h.deaths; r.wins += h.won ? 1 : 0;
  }));
  const kingOf = (map) => {
    const cands = Object.values(perf).filter((r) => r.map === map);
    const pool = cands.filter((r) => r.games >= 2);
    const src = pool.length ? pool : cands;
    return src.reduce((a, b) => (!a || b.adr / b.games > a.adr / a.games ? b : a), null);
  };
  const stats = Object.values(by).sort((a, b) => b.games - a.games || b.rounds - a.rounds);
  const maxGames = Math.max(...stats.map((s) => s.games));
  $("topmaps").innerHTML =
    '<div class="tm head"><span>#</span><span>Mapa</span><span>Jogos</span><span class="avg">Média</span><span>Rei do mapa</span><span class="close">Mais disputado</span></div>' +
    stats.map((s, i) => {
      const c = MAP_COLORS[s.map.toLowerCase()] || "var(--accent-mark)";
      const k = kingOf(s.map);
      return '<div class="tm" style="--mc:' + c + '">' +
        '<span class="rk display num">' + (i + 1) + "</span>" +
        '<span class="mn display"><i></i>' + esc(s.map) + "</span>" +
        '<span class="games"><span class="n display num">' + s.games + '</span><span class="bar"><i style="width:' + (s.games / maxGames) * 100 + '%"></i></span>' +
        '<span class="pct num">' + fmt((s.games / MAPS.length) * 100, 0) + "%</span></span>" +
        '<span class="avg num">' + fmt(s.rounds / s.games, 1) + "<small>rounds/jogo" + (s.ot ? " · " + plural(s.ot, "prorrogação", "prorrogações") : "") + "</small></span>" +
        (k ? '<span class="king">' + icon("star") + '<span style="min-width:0"><div class="kn">' + esc(k.name) + '</div><div class="kd2">' +
          fmt(k.adr / k.games, 1) + " ADR · " + k.kills + "/" + k.deaths + " · " + plural(k.games, "jogo", "jogos") + "</div></span></span>" : "<span></span>") +
        '<span class="close num">' + s.closest.a + "–" + s.closest.b + "<small>" + (s.closest.diff <= 2 ? "no detalhe" : "diferença de " + s.closest.diff) + "</small></span>" +
        "</div>";
    }).join("");
}

/* ---------- tudo ---------- */
function renderAll() {
  const list = visiblePlayers();
  renderSeg();
  renderPodium(list);
  renderAwards(list);
  renderScatter(list);
  renderTable(list);
}
$("search").addEventListener("input", (e) => {
  state.q = e.target.value.trim().toLowerCase();
  const list = visiblePlayers();
  renderTable(list);
  renderScatter(list);
});
renderMaps();
renderTopMaps();
renderAll();
</script>
</body>
</html>
"""


def render_html(
    players: list[dict], maps: list[dict], db_paths: list[Path], team_size: int, min_rounds: int, confidence: int
) -> str:
    dates = sorted(m["date"][:10] for m in maps if m["date"])
    period = ""
    if dates:
        fmt_date = lambda d: f"{d[8:10]}/{d[5:7]}"
        period = f" · {fmt_date(dates[0])} a {fmt_date(dates[-1])}"
    sources = " + ".join(p.name for p in db_paths)
    payload = {
        "players": players,
        "maps": maps,
        "period": period.lstrip(" ·"),
        "sources": sources,
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
    players = fetch_players(all_maps, args.min_rounds, args.confianca)

    if not players:
        sys.exit(
            "Nenhum jogador encontrado (ou todos abaixo do --min-rounds). "
            "Confira se já foram jogadas partidas completas pelo MatchZy."
        )

    maps = fetch_maps(all_maps, {p["steamid64"]: p["name"] for p in players})
    html = render_html(players, maps, db_paths, args.team_size, args.min_rounds, args.confianca)
    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"Ranking gerado: {out_path.resolve()}  ({len(players)} jogadores, {len(maps)} mapas)")


if __name__ == "__main__":
    main()
