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

## Noites

`split_nights()` junta os mapas em noites de mix: mais de 4h entre um mapa e o próximo começa
outra noite. `night_label()` data a noite pelo início menos 6h (mapa às 00:56 do dia 26 é
"noite de 25/09"). O seletor no topo da página troca entre "Todas as noites" e cada noite; cada
noite é calculada à parte (`build_scope()`), inclusive MixScore, duplas e rivalidades.

## Duplas, rivalidades, parceiro, freguês e carrasco

`pair_stats()` olha a escalação de cada mapa:

- **Dupla**: dois jogadores no mesmo time. Ranking por vitórias juntos (mínimo 3 jogos em todas
  as noites, 2 numa noite só).
- **Rivalidade**: dois jogadores em times opostos. Ranking pelo número de confrontos.
- No perfil de cada jogador: **melhor parceiro** (mais vitórias juntos, 2+ jogos), **freguês**
  (adversário que ele mais venceu) e **carrasco** (quem mais venceu ele), mínimo 2 confrontos.

## Forma

Calculada no JS (`enrich()`) a partir do histórico: últimos resultados (bolinhas V/D),
sequência atual (positiva = vitórias, negativa = derrotas) e maior sequência de vitórias.

## Montar times

Seção logo depois da "Última noite". O dono marca quem vai jogar (e pode adicionar convidados
que não estão no banco), e a página divide em dois times:

- **Força** de cada jogador = MixScore (com peso de confiança) + 20 × (aproveitamento ajustado − 50%),
  com aproveitamento ajustado = (vitórias + 2,5) / (mapas + 5). Sempre de **todas as noites**.
  Convidado entra com a força mediana do mix.
- **Equilibrar** testa todas as divisões possíveis (até 16 jogadores) e pega a de menor diferença de
  força média. **Sortear outra** escolhe aleatoriamente entre as divisões até 1 ponto piores que a
  melhor (no máximo 20), pra variar os times sem desequilibrar.
- Veredito: < 1 ponto "muito equilibrado", < 2,5 "equilibrado", < 5 "um pouco desequilibrado".
- **Copiar times** manda o texto pro clipboard (pra colar no grupo). A seleção fica salva no navegador
  (`localStorage`, chave `mix-teams`).
- Teste nos 21 mapas do banco (24–29/09): o time com maior força média venceu 16 de 21 (só MixScore:
  15 de 21). É dentro da amostra — não é uma previsão calibrada; por isso a página mostra a parcela
  de força e não "chance de vitória".

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

## Imagens dos mapas

`assets/maps/<slug>.jpg`, 640×360, tiradas de
[ghostcap-gaming/cs2-map-images](https://github.com/ghostcap-gaming/cs2-map-images)
("map screenshots you can use for cs2 projects"). A lista dos que existem fica em `MAP_FILES`
no JS; mapa sem imagem usa um degradê com a cor de `MAP_COLORS`. Pra adicionar um mapa novo
(ex: `de_dust3`):

```bash
curl -sL -o /tmp/m.png https://raw.githubusercontent.com/ghostcap-gaming/cs2-map-images/main/cs2/de_dust3.png
sips -s format jpeg -s formatOptions 72 --resampleWidth 640 /tmp/m.png --out assets/maps/dust3.jpg
```

e inclua `"dust3"` em `MAP_FILES` (e uma cor em `MAP_COLORS`).

## Estrutura do `matchzy_ranking.py`

| Função | Faz |
|---|---|
| `find_dbs()` | resolve os caminhos do `--db` (ou acha `./matchzy.db`) |
| `load_maps()` | lê todos os bancos, filtra mapas completos, junta jogadores de cada mapa |
| `fetch_players()` | agrega por `steamid64`, calcula métricas, MixScore, histórico e ordena |
| `fetch_maps()` | monta cada partida: placar, vencedor, MVP e o placar completo dos 10 jogadores (`board`) |
| `pair_stats()` | duplas, rivalidades, melhor parceiro, freguês e carrasco |
| `split_nights()` / `night_label()` | separa e nomeia as noites de mix |
| `build_scope()` | calcula tudo pra um conjunto de mapas (todas as noites ou uma noite) |
| `render_html()` | injeta o JSON (`@@PAYLOAD_JSON@@`, com `scopes`: todas as noites + cada noite) no `HTML_TEMPLATE` |
| `HTML_TEMPLATE` | página inteira: CSS, HTML e JS |

O JS da página renderiza tudo a partir do `PAYLOAD`: seletor de noite (`setScope()`), filtro
"Todos / 3+ / 5+ / 10+ mapas" (recalcula pódio, destaques, forma, gráfico e tabela), busca,
ordenação por coluna, tema claro/escuro (salvo no `localStorage`). Qualquer elemento com
`data-player="<steamid>"` abre o perfil (`openPlayer()`); com `data-match="<id>"` abre o placar
(`openMatch()`). Esc ou clique fora fecha.

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
