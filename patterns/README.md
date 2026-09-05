# Catalogo inicial de candlesticks

Este catalogo reune somente padroes classicos que passaram pelo corte editorial
abaixo. A inclusao significa que a definicao e o comportamento direcional foram
sustentados por fonte reconhecida; nao significa promessa de lucro.

## Criterio de inclusao

- definicao canonica em Nison ou Morris;
- nome e detector de referencia disponiveis no TA-Lib, sem presumir equivalencia
  exata de criterios;
- comportamento de reversao observado em pelo menos 70% na amostra de
  Bulkowski, ou evidencia academica especifica adicional;
- gatilho, stop e invalidacao objetivos;
- contexto anterior obrigatorio e confirmacao por fechamento;
- limitacoes da evidencia registradas no pacote.

`research_confidence` classifica a robustez das fontes usadas. Os percentuais
publicados por terceiros não são tratados como precisão do reconhecedor do
Providency.

## Pacotes incluidos

| Pacote | Leitura | Reversao observada | Confianca da pesquisa |
|---|---|---:|---|
| `bearish_engulfing` | reversao baixista | 79% | VERY_HIGH |
| `morning_star` | reversao altista | 78% | HIGH |
| `evening_star` | reversao baixista | 72% | HIGH |
| `three_outside_up` | reversao altista | 75% | HIGH |
| `three_white_soldiers` | reversao altista | 82% | HIGH |
| `three_black_crows` | reversao baixista | 78% | HIGH |

Os percentuais acima sao frequencias de direcao de breakout na metodologia de
Bulkowski, baseada em acoes e candles diarios. Nao sao taxa de trades vencedores.

## Candidatos excluidos neste corte

- Bullish Engulfing: 63% de reversao na base de Bulkowski e resultado academico
  forte para abertura/minima, mas nao para fechamento.
- Three Outside Down: 69% de reversao, abaixo do corte objetivo.
- Hammer e Shooting Star: padroes importantes, mas com taxa direcional inferior
  ao corte e forte dependencia de confirmacao.
- Piercing Pattern e Dark Cloud Cover: definicoes classicas, mas sem evidencia
  quantitativa comparavel suficiente para este primeiro grupo.

## Politica operacional comum

- A confirmacao geometrica (`CONFIRMED`) ocorre no fechamento da ultima vela da
  formacao, quando todas as regras do pacote sao satisfeitas.
- LONG: gatilho acima da maxima do padrao e stop abaixo da minima do padrao.
- SHORT: gatilho abaixo da minima do padrao e stop acima da maxima do padrao.
- As taxas de Bulkowski usam breakout confirmado por fechamento; o gatilho
  intrabar do Providency e mais rapido e, portanto, nao herda essas taxas.
- O buffer deve cobrir ao menos um tick e, no uso real, spread e slippage
  esperados; seu valor pertence ao Risk Engine.
- O padrao expira se o lado oposto for rompido antes do gatilho.
- Suporte, resistencia, tendencia primaria, liquidez e relacao risco-retorno
  continuam obrigatorios. Candlestick isolado nao autoriza operacao.

## Fontes transversais

- Steve Nison, *Japanese Candlestick Charting Techniques*, 2nd ed., 2001.
- Gregory L. Morris, *Candlestick Charting Explained*, 1995.
- Thomas N. Bulkowski, *Encyclopedia of Candlestick Charts*, 2008.
- CMT Association, "Learning from the Legend", 2020.
- TA-Lib, Pattern Recognition Functions.

## Evidencia contraria e limite de uso

A selecao nao ignora resultados negativos. Marshall, Young e Rose (2006),
*Candlestick technical trading strategies: Can they create value for
investors?*, DOI
[`10.1016/j.jbankfin.2005.08.001`](https://doi.org/10.1016/j.jbankfin.2005.08.001),
e Duvinage, Mazza e Petitjean (2013), DOI
[`10.1080/14697688.2013.768774`](https://doi.org/10.1080/14697688.2013.768774),
mostram por que reconhecimento direcional nao deve ser confundido com estrategia
rentavel. No segundo estudo, nenhuma regra superou buy-and-hold depois de custos
e correcao para data snooping. Por isso, estes pacotes fornecem sinal e nivel de
risco; confluencia, custos e Risk Engine continuam decisivos.

Data da pesquisa: 2026-09-04.

Os campos e operadores usados nos YAMLs estao definidos em
`PREDICATE_CONTRACT.md`.
