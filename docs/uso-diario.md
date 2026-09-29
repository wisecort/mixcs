# Uso diário

Passo a passo pra atualizar o ranking depois de uma sessão de mix.

## 1. Colocar o banco novo

O banco do MatchZy fica no servidor em:

```
csgo/addons/counterstrikesharp/plugins/MatchZy/matchzy.db
```

Baixe e coloque na raiz do projeto como `matchzy.db`, substituindo o anterior.
Se o navegador salvar como `matchzy (1).db`, renomeie:

```bash
mv "matchzy (1).db" matchzy.db
```

> O banco do servidor é **acumulativo**: o arquivo novo já contém as partidas antigas.
> Só use vários bancos juntos (§ 4) se o servidor tiver sido **resetado** e o banco antigo
> tiver ficado salvo à parte.

## 2. Checagens do banco

Rode antes de gerar, pra saber o que vai ficar de fora:

```bash
# período e quantidade de mapas
sqlite3 matchzy.db "select count(*), min(start_time), max(start_time) from matchzy_stats_maps;"

# mapas que NÃO vão contar (sem vencedor / não terminaram)
sqlite3 -header matchzy.db "select matchid, mapname, team1_score, team2_score, start_time
  from matchzy_stats_maps where winner is null or winner = '' or end_time is null or end_time = '';"

# jogadores por time em cada mapa (time com menos de 5 = mapa fora do ranking)
sqlite3 -header matchzy.db "select matchid, team, count(*) n from matchzy_stats_players
  where team != 'Spectator' group by 1, 2 having n != 5;"
```

O que reportar pro dono:

- mapas sem vencedor (normalmente partida abandonada ou `.restart`);
- times com menos de 5 (mapa fica fora) ou mais de 5 (alguém entrou no lugar de outro no
  meio — **conta normal**, os dois jogadores ficam com o resultado);
- se algum jogador parece somar coisa demais, confira o `steamid64` — é comum a mesma pessoa
  trocar de nick (o ranking usa o nick **mais recente**).

## 3. Gerar e abrir

```bash
python3 matchzy_ranking.py        # lê ./matchzy.db, escreve ./index.html
open index.html
```

A saída mostra `(N jogadores, M mapas)`. Resumo pro dono: período, nº de mapas, top 5 por
vitórias (V–D e %), e qualquer anomalia das checagens.

Top 5 rápido no terminal:

```bash
python3 -c "
import matchzy_ranking as m; from pathlib import Path
ps = m.fetch_players(m.load_maps([Path('matchzy.db')], 5), 10, 100)
for p in ps[:5]: print(p['rank'], p['name'], p['wins'], 'V', p['losses'], 'D', p['win_pct'], '%')
"
```

## 4. Opções do script

| Opção | Padrão | Pra quê |
|---|---|---|
| `--db A.db [B.db ...]` | `./matchzy.db` | Um ou mais bancos. Vários = consolida (IDs repetidos entre bancos são tratados; o mesmo mapa em dois bancos conta uma vez só) |
| `--out arquivo.html` | `index.html` | Arquivo de saída |
| `--min-rounds N` | `10` | Esconde quem jogou menos de N rounds no total |
| `--team-size N` | `5` | Mínimo de jogadores por time pro mapa contar (ex: `4` aceita 4x4) |
| `--confianca N` | `100` | Peso de confiança do MixScore em rounds; `0` desliga |

Exemplos:

```bash
python3 matchzy_ranking.py --db antigo.db matchzy.db     # servidor resetado: junta os dois
python3 matchzy_ranking.py --team-size 4                  # aceita mapas 4x4
```

## 5. Commit e push (só quando o dono pedir)

```bash
git add -A
git commit -m "Atualiza ranking até DD/MM"
git pull --no-rebase     # o dono às vezes sobe arquivos pelo site do GitHub
git push
```

`DD/MM` = data da última partida (aparece no topo da página, em "24/09 a 29/09").

O remote é HTTPS (`https://github.com/wisecort/mixcs.git`) autenticado pelo `gh`. Se o
`pull` der conflito no `index.html`, rode `python3 matchzy_ranking.py` de novo e commite o
resultado. Se o push pedir senha, confira `gh auth status`.
