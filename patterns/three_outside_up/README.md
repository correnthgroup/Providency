# Tres externos de alta

- **Familia:** `BULLISH_REVERSAL`
- **Nome em ingles:** Three Outside Up
- **Interpretacao:** um Engolfo de alta confirmado por novo fechamento maior.
- **Formacao:** candle baixista, candle altista que engloba seu corpo e terceira
  vela fechando acima do fechamento da segunda.
- **Confirmacao:** o fechamento da terceira vela completa o padrao.
- **Gatilho:** rompimento da maxima das tres velas, com buffer.
- **Stop:** abaixo da minima das tres velas, com buffer.
- **Invalidacao:** fechamento abaixo da minima ou perda da minima antes da entrada.
- **Contexto preferencial:** correcao de baixa em tendencia primaria altista,
  proxima a suporte.
- **BTC:** nao depende de gap entre sessoes, mas requer tendencia anterior.
- **TA-Lib:** `CDL3OUTSIDE` com retorno positivo.

As fixtures sao casos OHLC deterministicos para teste de regra.

## Exemplos

- `positive/001.yaml`: engolfo altista seguido por fechamento superior.
- `negative/001.yaml`: a terceira vela nao supera o segundo fechamento.
- `forming/001.yaml`: o engolfo existe, mas falta a terceira vela.
