# Tres corvos negros

## Definicao canonica

Reversao baixista de tres candles longos depois de alta. Todos fecham em baixa
e perto das minimas, cada fechamento fica abaixo do anterior e as velas 1 e 2
abrem dentro do corpo real precedente.

## Geometria e sequencia

Para `i = 0..2`, `Ci < Oi`, corpo `LONG` e
`LOWER_WICK_TO_RANGE <= 0.15`. Para `i = 1..2`, `Oi` fica estritamente entre o
fechamento e a abertura anterior e `Ci < C(i-1)`. Corpo/faixa >= 0,60 torna
objetivo `LONG`; 15% torna objetivo o fechamento perto da minima. Esses limites
sao escolhas de implementacao. O contexto exige tres fechamentos ascendentes.

## Formacao

Um ou dois corvos validos mantem estado `FORMING`. O candidato falha se uma
vela fecha altista, nao produz fechamento menor, abre fora do corpo precedente
ou deixa sombra inferior acima do limite.

## Confirmacao e gatilho

O terceiro fechamento completa o padrao. A venda so e proposta no rompimento
posterior da minima das tres velas com buffer, evitando antecipar continuacao
depois de uma queda ja extensa.

## Stop e invalidacao

Stop acima da maior maxima das tres velas mais buffer. A distancia pode ser
grande; o Risk Engine deve rejeitar a operacao se nao houver RR minimo de 2.
Fechamento acima da maxima invalida e rompimento antes da entrada cancela.

## Contexto e limitacoes

Bulkowski observou reversao baixista em 78% e rank geral 3 de 103. Contudo, ele
alerta para nao negociar o breakout para baixo no horizonte de dez dias: o rank
alto e fortemente influenciado por breakouts para cima, contrarios ao sinal
teorico, e a melhor combinacao tinha apenas 66 amostras. O pacote reconhece uma
formacao direcional frequente, mas a operacao short exige confluencia forte.

## Evidencia e fixtures

Bulkowski encontrou 2.660 padroes em mais de 4,7 milhoes de linhas de candle e
classificou o primeiro fechamento alem da maxima/minima como breakout. Resultados
foram separados por regime e direcao. Literatura classica e TA-Lib sustentam a
definicao, nao uma promessa de rentabilidade.

As fixtures OHLC sinteticas cobrem caso positivo, abertura fora do corpo e
sequencia parcial, todas no split `development`. Os limites de 60% e 15% sao
aplicados sem tolerancia. Elas exercitam a regra deterministica. A coleta visual
será ampliada com o uso diário.

## Resultado da evidencia publicada

- Reversao baixista observada: 78%.
- Rank de reversao: 6 de 103.
- Amostras informadas: 2.660.
- Rank de desempenho geral: 3 de 103.
- Melhor taxa de alvo: 36%, em breakout para cima.
- Limitacao residual: fraco seguimento no breakout baixista de dez dias.
