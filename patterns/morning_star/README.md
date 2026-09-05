# Estrela da manha

- **Familia:** `BULLISH_REVERSAL`
- **Nome em ingles:** Morning Star
- **Interpretacao:** exaustao vendedora seguida por retomada compradora.
- **Formacao:** candle longo de baixa, star de corpo pequeno separada abaixo e
  candle longo de alta fechando alem da metade do primeiro corpo.
- **Confirmacao:** o fechamento da terceira vela completa o padrao.
- **Gatilho:** rompimento da maxima das tres velas, com buffer.
- **Stop:** abaixo da minima das tres velas, com buffer.
- **Invalidacao:** fechamento abaixo da minima ou perda da minima antes da
  entrada.
- **Contexto preferencial:** correcao de baixa dentro de tendencia primaria de
  alta, proxima a suporte.
- **BTC:** a versao canonica exige gaps de corpo e sera rara; nao relaxar os
  gaps silenciosamente.
- **TA-Lib:** `CDLMORNINGSTAR`.

As fixtures sao casos OHLC deterministicos para teste de regra.

## Exemplos

- `positive/001.yaml`: tres velas completas com penetracao alem da metade.
- `negative/001.yaml`: a terceira vela nao cruza o ponto medio.
- `forming/001.yaml`: primeira vela e star, sem a terceira vela.
