# Estrela da tarde

- **Familia:** `BEARISH_REVERSAL`
- **Nome em ingles:** Evening Star
- **Interpretacao:** exaustao compradora seguida por retomada vendedora.
- **Formacao:** candle longo de alta, star de corpo pequeno separada acima e
  candle longo de baixa fechando alem da metade do primeiro corpo.
- **Confirmacao:** o fechamento da terceira vela completa o padrao.
- **Gatilho:** rompimento da minima das tres velas, com buffer.
- **Stop:** acima da maxima das tres velas, com buffer.
- **Invalidacao:** fechamento acima da maxima ou perda do setup antes da entrada.
- **Contexto preferencial:** repique de alta em tendencia primaria baixista,
  proximo a resistencia.
- **BTC:** a versao canonica exige gaps de corpo e sera rara; variantes sem gap
  nao pertencem a este pacote.
- **TA-Lib:** `CDLEVENINGSTAR`.

As fixtures sao casos OHLC deterministicos para teste de regra.

## Exemplos

- `positive/001.yaml`: tres velas completas com penetracao alem da metade.
- `negative/001.yaml`: a terceira vela nao cruza o ponto medio.
- `forming/001.yaml`: primeira vela e star, sem a terceira vela.
