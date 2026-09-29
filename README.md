# Ranking do Mix — CS2

Painel do mix de CS2 gerado a partir do banco do plugin [MatchZy](https://github.com/shobhit-pathak/MatchZy):
ranking por vitórias, pódio, destaques, top mapas, gráfico ADR × K/D, histórico por jogador
e resultado de cada mapa.

Abra o [`index.html`](index.html) no navegador.

## Atualizar

```bash
python3 matchzy_ranking.py   # lê ./matchzy.db e gera ./index.html
```

Só Python 3 (biblioteca padrão). Rotina completa em [`docs/uso-diario.md`](docs/uso-diario.md);
regras e métricas em [`docs/como-funciona.md`](docs/como-funciona.md).
