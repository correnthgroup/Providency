# Engolfo de baixa

- **Familia:** `BEARISH_REVERSAL`
- **Nome em ingles:** Bearish Engulfing
- **Interpretacao:** perda abrupta do controle comprador depois de uma alta.
- **Formacao:** candle de alta seguido por candle de baixa cujo corpo real
  engloba integralmente o corpo anterior.
- **Confirmacao:** o fechamento da segunda vela completa a geometria.
- **Gatilho:** rompimento abaixo da minima do padrao, com buffer.
- **Stop:** acima da maxima de toda a formacao, com buffer.
- **Invalidacao:** fechamento acima da maxima ou rompimento da maxima antes do
  gatilho short.
- **Contexto preferencial:** repique de alta dentro de uma tendencia primaria
  de baixa, proximo a resistencia.
- **BTC:** exigir contexto e fechamento; a negociacao continua reduz a utilidade
  de regras que dependem de gap.
- **TA-Lib:** `CDLENGULFING` com retorno negativo.

As fixtures em `positive/`, `negative/` e `forming/` sao casos OHLC
deterministicos, nao imagens de mercado nem amostra estatistica.

## Exemplos

- `positive/001.yaml`: engolfo completo depois de tres fechamentos ascendentes.
- `negative/001.yaml`: a segunda vela nao cobre a abertura anterior.
- `forming/001.yaml`: existe somente a primeira vela da sequencia.
