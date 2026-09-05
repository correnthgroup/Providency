# Tres externos de alta

## Definicao canonica

Padrao altista de tres velas apos queda. A primeira e baixista; a segunda e
altista e engloba integralmente o corpo real da primeira; a terceira fecha acima
do fechamento da segunda. As sombras nao participam do engolfo.

## Geometria e sequencia

`C0 < O0`, `C1 > O1`, `O1 < C0`, `C1 > O0` e `C2 > C1`. O pacote usa
engolfo estrito nas duas bordas, compativel com a implementacao de referencia e
imune a corpos identicos. A terceira vela nao precisa ser altista: o requisito
canonico essencial e o fechamento maior. O contexto exige tres fechamentos
descendentes.

## Formacao

Depois de queda previa e candle baixista, o segundo candle forma um Engolfo de
alta. Nesse ponto o padrao permanece `FORMING`; somente o fechamento da terceira
vela acima de `C1` completa a confirmacao externa.

## Confirmacao e gatilho

A terceira vela e a confirmacao incorporada ao padrao. A entrada conservadora
ocorre apenas no rompimento posterior da maior maxima das tres velas, com buffer.

## Stop e invalidacao

Stop abaixo da menor minima das tres velas mais o buffer do Risk Engine. O
nivel cobre toda a estrutura e nao apenas a vela de engolfo. Fechamento abaixo
da minima invalida; rompimento antes da entrada cancela o setup.

## Contexto e limitacoes

Bulkowski observou reversao altista em 75% e desempenho geral 34 de 103. A
melhor taxa de alvo foi somente 47%, e o melhor movimento de dez dias ocorreu
em breakout para baixo, contrario a leitura altista. Priorizar retracao dentro
de tendencia primaria de alta, suporte e liquidez. A taxa de reversao mede o
lado do breakout, nao ganho apos custos.

## Evidencia e fixtures

Bulkowski avaliou centenas de formacoes em acoes, separadas por regime e lado
do breakout, em base de quase cinco milhoes de candles. Breakout significa
fechamento alem do extremo da formacao. Morris e a origem citada para a terceira
vela de confirmacao; TA-Lib fornece implementacao de referencia.

As fixtures OHLC sinteticas cobrem caso positivo, terceiro fechamento
insuficiente e sequencia parcial, todas no split `development`. Empates nas
bordas do engolfo sao negativos. Elas exercitam as regras deterministicamente.
A coleta visual será ampliada com o uso diário.

## Resultado da evidencia publicada

- Reversao altista observada: 75%.
- Rank de reversao: 7 de 103.
- Rank de frequencia: 24 de 103.
- Rank de desempenho geral: 34 de 103.
- Melhor taxa de alvo: 47%, bull market, breakout para cima.
- Limitacao residual: desempenho direcional nao implica rentabilidade long.
