# Tres corvos negros

- **Familia:** `BEARISH_REVERSAL`
- **Nome em ingles:** Three Black Crows
- **Interpretacao:** tres sessoes de pressao vendedora progressiva apos alta.
- **Formacao:** tres candles baixistas longos, com fechamentos descendentes,
  aberturas dentro do corpo anterior e sombras inferiores curtas.
- **Confirmacao:** fechamento da terceira vela.
- **Gatilho:** rompimento da minima do padrao, com buffer.
- **Stop:** acima da maxima do padrao, com buffer.
- **Invalidacao:** fechamento acima da maxima ou perda do setup antes da entrada.
- **Contexto preferencial:** topo/resistencia apos alta; rejeitar venda tardia
  quando a extensao ja comprometer o risco-retorno.
- **BTC:** aplicavel sem gaps, com filtro obrigatorio de exaustao e contexto.
- **TA-Lib:** `CDL3BLACKCROWS`.

As fixtures sao casos OHLC deterministicos para teste de regra.

## Exemplos

- `positive/001.yaml`: tres candles fortes, sobrepostos e descendentes.
- `negative/001.yaml`: o terceiro abre fora do corpo anterior.
- `forming/001.yaml`: existem somente os dois primeiros corvos.
