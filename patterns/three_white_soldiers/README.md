# Tres soldados brancos

- **Familia:** `BULLISH_REVERSAL`
- **Nome em ingles:** Three White Soldiers
- **Interpretacao:** tres sessoes de pressao compradora progressiva apos queda.
- **Formacao:** tres candles altistas longos, com fechamentos ascendentes,
  aberturas dentro do corpo anterior e sombras superiores curtas.
- **Confirmacao:** fechamento da terceira vela.
- **Gatilho:** rompimento da maxima do padrao, com buffer.
- **Stop:** abaixo da minima do padrao, com buffer.
- **Invalidacao:** fechamento abaixo da minima ou perda da minima antes da entrada.
- **Contexto preferencial:** fundo/suporte depois de queda; evitar entrada tardia
  quando as tres velas ja consumiram grande extensao.
- **BTC:** aplicavel sem gaps, sempre com filtro de exaustao e risco-retorno.
- **TA-Lib:** `CDL3WHITESOLDIERS`.

As fixtures sao casos OHLC deterministicos para teste de regra.

## Exemplos

- `positive/001.yaml`: tres candles fortes, sobrepostos e ascendentes.
- `negative/001.yaml`: o terceiro abre fora do corpo anterior.
- `forming/001.yaml`: existem somente os dois primeiros soldados.
