# Como funciona

## Fonte dos dados

Tabelas do MatchZy usadas (`matchzy.db`, SQLite):

| Tabela | O que tem |
|---|---|
| `matchzy_stats_matches` | uma linha por partida: `team1_name`, `team2_name`, vencedor, horários |
| `matchzy_stats_maps` | uma linha por mapa: `mapname`, placar (`team1_score`/`team2_score`), `winner`, `start_time`, `end_time` |
| `matchzy_stats_players` | uma linha por jogador por mapa: `steamid64`, `name`, `team`, kills, deaths, assists, damage, headshots, 2k–5k, clutches 1v1/1v2 |

Times vêm como `team_<capitão>` (espaços viram `_`); `team_label()` limpa isso pra exibição.

## Filtro de mapas

`COMPLETE_MAPS_SQL` / `load_maps()` só aceitam mapas:

1. com `winner` e `end_time` preenchidos (terminaram de verdade);
2. em que **os dois** times têm pelo menos `--team-size` jogadores (sem contar `Spectator`).

Linhas de `Spectator` nunca entram nas estatísticas. Com vários bancos, cada mapa ganha a chave
`(índice do banco, matchid, mapnumber)` e duplicatas são descartadas pela "impressão digital"
`(start_time, mapname, mapnumber, placar)`.

## Ranking

Ordem da classificação e do pódio (em `fetch_players()` e em `visiblePlayers()` no JS):

1. **vitórias** (mapas em que o time do jogador venceu);
2. **% de vitórias**;
3. **MixScore**.

## MixScore

Nota bruta por jogador (`raw_rating`):

```
kills_por_round × 40
+ (ADR / 100) × 25
+ HS% × 0,05
+ clutch% × 0,10
+ K/D × 5
```

Nota final com **peso de confiança** (`rating`):

```
final = (rounds × bruta + confianca × média_do_mix) / (rounds + confianca)
```

`média_do_mix` é a média das notas brutas ponderada por rounds. Com `confianca = 100`, quem tem
100 rounds (~5 mapas) fica no meio do caminho entre a própria nota e a média. Serve pra ninguém
aparecer no topo com 2–3 mapas de sorte.

**Tiers** (badge na tabela, pelo MixScore final): S ≥ 72 · A ≥ 65 · B ≥ 58 · C ≥ 50 · D abaixo.

## Outras métricas

| Métrica | Cálculo |
|---|---|
| ADR | dano total / rounds jogados |
| K/D | kills / deaths |
| HS% | kills de headshot / kills |
| Clutch | vitórias em 1v1 + 1v2 / tentativas |
| Multi-kills | rounds com 2, 3, 4 ou 5 kills (5 = ace) |
| MVP do mapa | quem fez mais kills no mapa (desempate: dano) |
| Rei do mapa (Top mapas) | maior ADR médio naquele mapa entre quem jogou 2+ vezes nele |
| Destaques | melhor de cada métrica entre quem tem 100+ rounds (`--confianca`) |

## Estrutura do `matchzy_ranking.py`

| Função | Faz |
|---|---|
| `find_dbs()` | resolve os caminhos do `--db` (ou acha `./matchzy.db`) |
| `load_maps()` | lê todos os bancos, filtra mapas completos, junta jogadores de cada mapa |
| `fetch_players()` | agrega por `steamid64`, calcula métricas, MixScore, histórico e ordena |
| `fetch_maps()` | monta os cards de mapa (placar, vencedor, MVP) |
| `render_html()` | injeta o JSON (`@@PAYLOAD_JSON@@`) no `HTML_TEMPLATE` |
| `HTML_TEMPLATE` | página inteira: CSS, HTML e JS |

O JS da página renderiza tudo a partir do `PAYLOAD`: filtro "Todos / 3+ / 5+ / 10+ mapas"
(recalcula pódio, destaques, gráfico e tabela), busca, ordenação por coluna, histórico ao
clicar na linha, tema claro/escuro (salvo no `localStorage`).

## Mexer no visual

- Cores são tokens CSS em `:root` (tema escuro é o padrão) e repetidos pro tema claro em
  `@media (prefers-color-scheme: light)` e `:root[data-theme="light"]`. Mudou um, mude nos três.
- `--accent-mark` (barras e pontos) foi validado no validador de paleta pros dois fundos:
  `#c98500` no escuro, `#c97800` no claro. `--accent` é só pra texto/brilho.
- Cores dos mapas: `MAP_COLORS` no JS. Mapa novo sem cor usa o âmbar padrão.
- Fontes: Barlow Condensed (títulos/números) e Inter, via Google Fonts. Sem internet cai nas
  fontes do sistema.
- **Não use `\` dentro do `HTML_TEMPLATE`** (string Python normal).

## Verificar o visual

```bash
python3 matchzy_ranking.py
# JS válido?
sed -n '/<script>/,/<\/script>/p' index.html | sed '1d;$d' > /tmp/check.js && node --check /tmp/check.js
# screenshot desktop
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu \
  --hide-scrollbars --virtual-time-budget=4000 --window-size=1280,3000 \
  --screenshot=/tmp/ranking.png "file://$PWD/index.html"
```

Pra testar celular, o Chrome headless não desce abaixo de ~500px de largura: coloque a página
num `<iframe style="width:390px">` e tire o screenshot desse arquivo (precisa de
`--allow-file-access-from-files`).
