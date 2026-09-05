# Tres soldados brancos

## Definicao canonica

Reversao altista de tres candles longos depois de queda. Todos fecham em alta e
perto das maximas, cada fechamento supera o anterior e as velas 1 e 2 abrem
dentro do corpo real precedente.

## Geometria e sequencia

Para `i = 0..2`, `Ci > Oi`, corpo `LONG` e
`UPPER_WICK_TO_RANGE <= 0.15`. Para `i = 1..2`, `Oi` fica estritamente entre a
abertura e o fechamento anterior e `Ci > C(i-1)`. Corpo/faixa >= 0,60 torna
objetivo `LONG`; 15% quantifica "fecha perto da maxima". Esses limites sao
escolhas reproduziveis de implementacao, nao numeros de Nison. O contexto exige
tres fechamentos descendentes.

## Formacao

Um ou dois candles validos mantem estado `FORMING`. O candidato falha quando
uma vela fecha baixista, nao produz novo fechamento alto, abre fora do corpo
anterior ou deixa sombra superior acima do limite.

## Confirmacao e gatilho

O terceiro fechamento completa a formacao. Para evitar comprar somente porque
o ultimo candle termina perto da maxima, o gatilho exige rompimento posterior
da maxima das tres velas com buffer.

## Stop e invalidacao

Stop abaixo da menor minima do conjunto mais buffer do Risk Engine. Apesar de
amplo, esse nivel representa invalidacao completa do fundo; a operacao deve ser
rejeitada se a distancia impedir RR minimo de 2. Fechamento abaixo da minima
invalida e rompimento antes da entrada cancela o setup.

## Contexto e limitacoes

Bulkowski observou reversao altista em 82%, mas explica que a terceira vela ja
fecha perto do topo, facilitando o breakout para cima. O rank geral foi 32 e a
melhor taxa de alvo, apenas 34%; breakouts altistas tiveram pouco seguimento.
Assim, a alta taxa direcional nao autoriza perseguir preco estendido. Priorizar
suporte e rejeitar RR insuficiente.

## Evidencia e fixtures

Bulkowski identificou 3.333 amostras em mais de 4,7 milhoes de linhas de candle
e classificou o primeiro fechamento acima da maxima ou abaixo da minima como
breakout. A definicao e corroborada pela literatura classica e pelo TA-Lib.

As fixtures OHLC sinteticas cobrem caso positivo, abertura fora do corpo e
sequencia parcial, todas no split `development`. Os limites de 60% e 15% sao
aplicados sem tolerancia. Elas exercitam a regra deterministica. A coleta visual
será ampliada com o uso diário.

## Resultado da evidencia publicada

- Reversao altista observada: 82%.
- Rank de reversao: 3 de 103.
- Amostras informadas: 3.333.
- Rank de desempenho geral: 32 de 103.
- Melhor taxa de alvo: 34%.
- Limitacao residual: breakout altista frequente, mas seguimento fraco.
