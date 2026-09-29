# AGENTS.md — Ranking do Mix (MatchZy / CS2)

Instruções pra qualquer agente (Claude Code, Codex etc.) que trabalhe neste repositório.
O dono usa isso **todo dia** depois das sessões de mix. Responda em **português**, direto ao ponto.

## O que é

`matchzy_ranking.py` lê o banco SQLite do plugin MatchZy (`matchzy.db`) e gera um painel
estático `index.html` com o ranking do mix: seletor de noite, última noite (MVP), **montar times**
(equilibra dois times a partir de quem vai jogar), pódio,
destaques, forma (sequências), top mapas, duplas e rivalidades, gráfico ADR × K/D,
classificação, perfil de cada jogador (painel lateral com todas as partidas) e o placar
completo de cada mapa (janela ao clicar numa partida).

Site: **https://mixcs-eta.vercel.app/** — a Vercel publica automaticamente a cada push na `main`
(serve o `index.html` e a pasta `assets/` direto do repositório; não há build).

Repositório público: `https://github.com/wisecort/mixcs` (branch `main`). Tudo aqui é
público de propósito, incluindo o `matchzy.db` e os Steam IDs no HTML — o dono autorizou.

## Arquivos

| Arquivo | Papel |
|---|---|
| `matchzy_ranking.py` | Script único: leitura do banco, cálculo das métricas e template HTML (`HTML_TEMPLATE`) |
| `matchzy.db` | Banco do MatchZy mais recente (vem do servidor; às vezes chega como `matchzy (1).db` pelo download) |
| `index.html` | Saída gerada — **nunca editar à mão**, sempre regenerar pelo script |
| `assets/maps/*.jpg` | Imagens dos mapas (640px) usadas pela página — precisam ir junto com o `index.html` |
| `assets/favicon.svg`, `assets/apple-touch-icon.png` | Ícone do site (mira âmbar) e versão 180px pra tela inicial do celular |
| `docs/uso-diario.md` | Rotina diária passo a passo, checagens e comandos |
| `docs/como-funciona.md` | Regras do ranking, métricas, estrutura do código e como mexer no visual |

## Rotina diária (resumo)

1. O dono coloca o banco novo na pasta. Se vier como `matchzy (1).db`, confirme com ele e
   renomeie pra `matchzy.db` (substituindo o antigo).
2. Rode as checagens do banco (`docs/uso-diario.md` § Checagens) e reporte partidas
   incompletas, times incompletos ou espectadores.
3. `python3 matchzy_ranking.py` → gera `index.html` a partir de `./matchzy.db`.
4. Abra (`open index.html`) e resuma pro dono: período, nº de mapas, top 5 por vitórias,
   e qualquer coisa estranha.
5. Commit + push **só quando o dono pedir** (ver § Git).

Detalhes, comandos prontos e casos especiais: **`docs/uso-diario.md`**.

## Regras do ranking (não mude sem o dono pedir)

- Ordem: **vitórias** → desempate por **% de vitórias** → depois **MixScore**.
  O dono pediu explicitamente ranking por vitória, não por nota.
- Só contam mapas **finalizados** (com vencedor e `end_time`) com os **dois times completos**
  (`--team-size`, padrão 5). Linhas de `Spectator` são ignoradas.
- MixScore tem **peso de confiança** (`--confianca`, padrão 100 rounds) pra quem jogou pouco
  não aparecer no topo.
- Destaques só consideram quem tem **100+ rounds** (mesmo valor do `--confianca`).

Explicação completa e fórmulas: **`docs/como-funciona.md`**.

## Cuidados ao editar o código

- `HTML_TEMPLATE` é uma string Python **normal** (não raw): **não use `\` no JS/CSS do template**
  (`\"`, `\s`, `\d`, `\n`…). Python come a barra e o JS quebra. Use aspas simples/duplas
  alternadas e evite regex com barra invertida.
- Depois de mexer no template, **sempre** valide o JS e olhe a página:
  ```bash
  python3 matchzy_ranking.py
  sed -n '/<script>/,/<\/script>/p' index.html | sed '1d;$d' > /tmp/check.js && node --check /tmp/check.js
  ```
  e tire um screenshot (comando em `docs/como-funciona.md` § Verificar o visual).
- Nomes de jogador entram no HTML via `esc()` — mantenha isso em qualquer HTML novo.
- Cores: tokens CSS em `:root` (escuro padrão + claro). Barras/pontos usam `--accent-mark`
  (validado); texto usa `--accent`. Não coloque número/texto na cor da série.

## Git

- Remote: `https://github.com/wisecort/mixcs.git` (**HTTPS**, não SSH), branch `main`. A autenticação
  usa o GitHub CLI (`gh`, conta `wisecort`) via `credential.helper` configurado neste repositório.
- Commitar tudo (script, banco, HTML, docs).
- Mensagem padrão do commit diário: `Atualiza ranking até DD/MM` (data da última partida).
- Só faça commit/push quando o dono pedir. **Push = site no ar** (Vercel), então confira a página antes.
- Depois do push, espere ~1 min e confirme que o site atualizou:
  `curl -s https://mixcs-eta.vercel.app/ | grep -o '"period": "[^"]*"' | head -1`
- Antes do push, rode `git pull --no-rebase` — o dono às vezes sobe arquivos direto pelo site do
  GitHub. Se der conflito no `index.html`, **regenere** com o script em vez de escolher um lado.
- Não use SSH (o `github.com` não está no `known_hosts` do Mac dele). Se o push pedir senha,
  confira `gh auth status`.
