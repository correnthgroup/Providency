# Contrato minimo de predicados OHLC

Este contrato torna reproduziveis os predicados usados pelos pacotes iniciais.
O Pattern Matcher e o checker estrutural devem implementar estas semanticas.

## Medidas

Para uma vela com `O`, `H`, `L` e `C`:

```text
RANGE = H - L
BODY_HIGH = max(O, C)
BODY_LOW = min(O, C)
BODY_MIDPOINT = (O + C) / 2
BODY_TO_RANGE = abs(C - O) / RANGE
UPPER_WICK_TO_RANGE = (H - BODY_HIGH) / RANGE
LOWER_WICK_TO_RANGE = (BODY_LOW - L) / RANGE
BODY_COLOR = BULLISH se C > O; BEARISH se C < O
```

`RANGE <= 0` ou OHLC inconsistente produz `NO_MATCH`. Doji (`C = O`) nao
satisfaz `BULLISH` nem `BEARISH`.

## Referencias

Um `value` escalar e comparado diretamente. Um mapa como
`{candle: 0, field: CLOSE}` referencia outra vela da mesma janela.

`BETWEEN_EXCLUSIVE` recebe `lower_field` e `upper_field` da vela indicada e
exige `lower < field < upper`. O pacote deve fornecer as bordas ja na ordem
crescente.

## Operadores

- `EQ`: igualdade.
- `GT` / `GTE`: maior / maior ou igual.
- `LT` / `LTE`: menor / menor ou igual.
- `BETWEEN_EXCLUSIVE`: estritamente entre as duas referencias.

## Contexto anterior

`recognition.context` usa candles imediatamente anteriores a `candle: 0`:

```yaml
context:
  preceding_candles: 3
  rules:
    - field: CLOSE_SEQUENCE
      operator: EQ
      value: ASCENDING
```

`ASCENDING` exige `C[-3] < C[-2] < C[-1]`; `DESCENDING` exige o inverso.
Esse filtro minimo representa a tendencia anterior pedida pelas fontes. A
tendencia primaria e a estrutura de mercado continuam no Confluence Engine.
